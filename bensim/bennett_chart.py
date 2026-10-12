"""Draw Bennett-strategy backtest trades the way Justin Bennett marks his charts: the 1H BOS/CHoCH, the fib from the
displacement start to the external low/high (premium green / OTE darker green / discount red, his levels
0 / 0.5 / 0.62 / 0.79 / 1), the 1H / 15M FVGs inside the OTE, the 5M CHoCH trigger, and the position box.
State is rebuilt with the strategy's own functions at the entry time (no hindsight in the markings).

    python -m bensim.bennett_chart --csv data/bennett_3m_6_dxy.csv --rows 0,3,47,4,19 [--out docs/charts/bennett]
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import bennett_smc as smc
from . import bennett_strategy as bs
from .backtest import load_m5, resample

W, H, PAD, TOP, RIGHT, INFO = 1500, 900, 70, 70, 110, 170
GREEN, OTE, RED, INK = "#e3f3e6", "#bfe3c7", "#f8dcdc", "#222"


def ts(t: int) -> str:
    return datetime.fromtimestamp(int(t), timezone.utc).strftime("%a %d %b %H:%M")


def state(sym: str, tr: pd.Series, p: bs.BennettParams) -> dict:
    """What the strategy saw at entry, rebuilt with its own engine (bensim.bennett_smc): the leg, its labels and
    breaks, the OTE, the leg's FVGs in the OTE, the relief's equal highs/lows, and the trigger CHoCH."""
    t_in = int(tr.t_entry)
    m5 = load_m5(sym, pd.Timestamp(t_in - 70 * 86400, unit="s", tz="UTC").isoformat(),
                 pd.Timestamp(int(tr.t_exit) + 2 * 86400, unit="s", tz="UTC").isoformat())
    m15, h1 = resample(m5, 900), resample(m5, 3600)
    h = bs.closed({k: v for k, v in h1.items()}, 3600, t_in)
    h = {k: v[-1300:] for k, v in h.items()}
    hb, sec = (bs.closed(bs.agg(h, 14400), 14400, t_in), 14400) if p.leg_tf == "H4" else (h, 3600)
    leg = bs.leg_of(hb, p)
    d = leg["d"]
    z_lo, z_hi = smc.ote_band(leg["start"], leg["ext"], d, p.ote)
    t0, t1 = int(hb["t"][leg["s_bar"]]), int(hb["t"][leg["x_bar"]]) + sec
    m15c = bs.closed(m15, 900, t_in)
    aft = m15c["t"] >= int(hb["t"][leg["x_bar"]])
    hit = np.flatnonzero(aft & ((m15c["high"] >= z_lo) if d == -1 else (m15c["low"] <= z_hi)))
    touch_t = int(m15c["t"][hit[0]]) if len(hit) else t_in  # first OTE touch (FVGs used before it don't count)
    gaps = []
    for name, b in ((f"{p.leg_tf.lower()} FVG", hb), ("1h FVG", h), ("15m FVG", m15c)):
        if name == "1h FVG" and hb is h:
            continue
        for z in smc.fvgs(b):
            if (z["dir"] == d and t0 <= b["t"][z["formed"]] <= t1 and z["top"] >= z_lo and z["bottom"] <= z_hi
                    and z["touched"] >= int(np.searchsorted(b["t"], touch_t))):  # one-time use, same as the strategy
                gaps.append((name, int(b["t"][z["c1"]]), z["bottom"], z["top"]))
    k5 = int(np.searchsorted(m5["t"] + 300, t_in, side="right"))
    ltf = {k: v[max(0, k5 - 100):k5] for k, v in m5.items()}
    ms = smc.market_structure(ltf, p.break_atr * smc.atr(ltf), seed=6)
    ev = ms["events"][-1] if ms["events"] else None
    choch = (int(ltf["t"][ev[4]]), int(ltf["t"][ev[0]]), float(ev[3])) if ev and ev[1] == "CHoCH" and ev[2] == d else None
    labels = [(int(hb["t"][b_]), lab, price) for b_, lab, price in leg["ms"]["labels"] if hb["t"][b_] >= t0 - 20 * sec]
    breaks = [(int(hb["t"][pv]), int(hb["t"][e_]), kind, lvl) for e_, kind, _, lvl, pv in leg["ms"]["events"]
              if hb["t"][e_] >= t0 - 20 * sec]
    sh, sl_ = smc.swing_points({k: v[-200:] for k, v in m15c.items()}, 2)
    eqs = [e["level"] for e in smc.equal_levels(sh if d == -1 else sl_, p.eq_tol_atr * leg["atr"], high=d == -1)
           if (e["level"] - tr.entry) * -d > 0]
    return {"m15": m15, "leg": leg, "h": hb, "ote": (z_lo, z_hi), "gaps": gaps, "choch": choch,
            "brk": leg["ms"]["events"][-1][3], "labels": labels, "breaks": breaks, "eq": eqs, "tf": p.leg_tf}


def svg(sym: str, tr: pd.Series, s: dict, p: bs.BennettParams) -> str:
    m, leg, d = s["m15"], s["leg"], s["leg"]["d"]
    t_start = int(s["h"]["t"][leg["s_bar"]]) - 6 * 3600
    snap = bool(tr.get("snapshot", False))  # engine view at t_entry only: no bars after it are drawn
    t_end = int(tr.t_entry) + 300 if snap else int(tr.t_exit) + 8 * 3600
    i0, i1 = int(np.searchsorted(m["t"], t_start)), int(np.searchsorted(m["t"], t_end))
    v = {k: x[i0:i1] for k, x in m.items()}
    n = len(v["t"])
    lo_p = float(np.nanmin([v["low"].min(), tr.sl, tr.tp, leg["ext"], leg["start"]]))
    hi_p = float(np.nanmax([v["high"].max(), tr.sl, tr.tp, leg["ext"], leg["start"]]))
    span = hi_p - lo_p
    lo_p, hi_p = lo_p - 0.04 * span, hi_p + 0.04 * span
    cw = (W - PAD - RIGHT) / n
    X = lambda t: PAD + (np.searchsorted(v["t"], t) + 0.5) * cw  # noqa: E731
    Y = lambda pr: TOP + (hi_p - pr) / (hi_p - lo_p) * (H - TOP - INFO)  # noqa: E731
    dig = 2 if sym in ("XAUUSD", "USDJPY") else 5
    f = lambda x: f"{x:.{dig}f}"  # noqa: E731
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="Segoe UI,Arial" font-size="13">',
         f'<rect width="{W}" height="{H}" fill="#fff"/>']
    # fib zones (his tool): premium green, OTE darker green, discount red -- from the displacement start onward
    rng = (leg["start"] - leg["ext"]) * -d
    lvl = lambda r: leg["ext"] - d * r * rng  # noqa: E731
    xa, xb = X(int(s["h"]["t"][leg["s_bar"]])), PAD + n * cw
    prem = (lvl(0.5), lvl(1.0))
    z_lo, z_hi = s["ote"]
    o.append(f'<rect x="{xa:.1f}" y="{Y(max(prem)):.1f}" width="{xb - xa:.1f}" height="{abs(Y(prem[0]) - Y(prem[1])):.1f}" fill="{GREEN}"/>')
    o.append(f'<rect x="{xa:.1f}" y="{Y(z_hi):.1f}" width="{xb - xa:.1f}" height="{Y(z_lo) - Y(z_hi):.1f}" fill="{OTE}"/>')
    disc = (lvl(0.0), lvl(0.5))
    o.append(f'<rect x="{xa:.1f}" y="{Y(max(disc)):.1f}" width="{xb - xa:.1f}" height="{abs(Y(disc[0]) - Y(disc[1])):.1f}" fill="{RED}"/>')
    for r, c in ((0.0, "#555"), (0.5, "#d33"), (0.62, "#2a8"), (0.79, "#2a8"), (1.0, "#555")):
        y = Y(lvl(r))
        o.append(f'<line x1="{xa:.1f}" x2="{xb:.1f}" y1="{y:.1f}" y2="{y:.1f}" stroke="{c}" stroke-width="1" stroke-dasharray="{"" if r in (0.0, 1.0) else "4 3"}"/>')
        o.append(f'<text x="{xa + 4:.1f}" y="{y - 3:.1f}" fill="{c}" font-size="12">{r:g} ({f(lvl(r))})</text>')
    # FVGs inside the OTE: one per timeframe, the one nearest the entry (his "1h FVG" / "15m FVG")
    best = {}
    for g in s["gaps"]:
        dist = abs((g[2] + g[3]) / 2 - tr.entry)
        if g[0] not in best or dist < best[g[0]][0]:
            best[g[0]] = (dist, g)
    for name, t_g, gb, gt in (g for _, g in best.values()):
        xg = X(t_g)
        o.append(f'<rect x="{xg:.1f}" y="{Y(gt):.1f}" width="{xb - xg:.1f}" height="{max(2, Y(gb) - Y(gt)):.1f}" fill="none" stroke="#111" stroke-width="1.2"/>')
        o.append(f'<text x="{xb - 4:.1f}" y="{Y(gt) - 3:.1f}" text-anchor="end" fill="#111" font-size="12" font-weight="bold">{name}</text>')
    # candles
    for i in range(n):
        x = PAD + (i + 0.5) * cw
        up = v["close"][i] >= v["open"][i]
        col = "#26a69a" if up else "#333"
        o.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{Y(v["high"][i]):.1f}" y2="{Y(v["low"][i]):.1f}" stroke="{col}"/>')
        yb, ye = sorted((Y(v["open"][i]), Y(v["close"][i])))
        o.append(f'<rect x="{x - cw * 0.35:.1f}" y="{yb:.1f}" width="{cw * 0.7:.1f}" height="{max(1, ye - yb):.1f}" fill="{"#fff" if up else col}" stroke="{col}"/>')
    # leg-timeframe structure: every BOS / CHoCH line and the HH / HL / LH / LL labels the engine produced
    for ta, tb, kind, lv in s["breaks"]:
        if lv < lo_p or lv > hi_p:
            continue
        o.append(f'<line x1="{X(ta):.1f}" x2="{X(tb):.1f}" y1="{Y(lv):.1f}" y2="{Y(lv):.1f}" stroke="#000" stroke-width="{2 if kind == "CHoCH" else 1.2}"/>')
        o.append(f'<text x="{(X(ta) + X(tb)) / 2:.1f}" y="{Y(lv) - 4:.1f}" text-anchor="middle" font-size="12" font-weight="bold">{s["tf"]} {kind}</text>')
    for tl, lab, pr in s["labels"]:
        if lo_p <= pr <= hi_p and tl >= v["t"][0]:
            up_lab = lab in ("HH", "LH")
            o.append(f'<text x="{X(tl):.1f}" y="{Y(pr) + (-8 if up_lab else 16):.1f}" text-anchor="middle" font-size="11" fill="#1565c0" font-weight="bold">{lab}</text>')
    for lv in s["eq"]:
        o.append(f'<line x1="{xa:.1f}" x2="{xb:.1f}" y1="{Y(lv):.1f}" y2="{Y(lv):.1f}" stroke="#ef6c00" stroke-width="1.5" stroke-dasharray="6 3"/>')
        o.append(f'<text x="{xa + 6:.1f}" y="{Y(lv) - 4:.1f}" fill="#ef6c00" font-size="12" font-weight="bold">equal {"highs" if d == -1 else "lows"} = liquidity ({f(lv)})</text>')
    if s["choch"]:
        ta, tb, lv = s["choch"]
        o.append(f'<line x1="{X(ta):.1f}" x2="{X(tb):.1f}" y1="{Y(lv):.1f}" y2="{Y(lv):.1f}" stroke="#c2185b" stroke-width="2"/>')
        o.append(f'<text x="{X(ta) - 4:.1f}" y="{Y(lv) + (16 if d == -1 else -7):.1f}" text-anchor="end" fill="#c2185b" font-weight="bold">5m CHoCH</text>')
    # position box: risk red, reward green, from entry to exit (a snapshot has none)
    if snap:
        o.append(f'<line x1="{PAD + (n - 0.5) * cw:.1f}" x2="{PAD + (n - 0.5) * cw:.1f}" y1="{TOP}" y2="{H - INFO}" stroke="#1565c0" stroke-dasharray="3 3"/>')
        o.append(f'<text x="{PAD + (n - 0.5) * cw - 4:.1f}" y="{TOP + 14}" text-anchor="end" fill="#1565c0" font-weight="bold">now ({ts(tr.t_entry)}) -- nothing after this is shown</text>')
    xi, xo = X(int(tr.t_entry) - 300), X(int(tr.t_exit) - 300)
    xo = max(xo, xi + 30)
    for a_, b_, c in (() if snap else ((tr.entry, tr.sl, "rgba(220,60,60,0.30)"), (tr.entry, tr.tp, "rgba(40,160,90,0.30)"))):
        o.append(f'<rect x="{xi:.1f}" y="{Y(max(a_, b_)):.1f}" width="{xo - xi:.1f}" height="{abs(Y(a_) - Y(b_)):.1f}" fill="{c}"/>')
    for pr, lab, c in (() if snap else ((tr.entry, "Entry", "#111"), (tr.sl, "SL", "#c62828"), (tr.tp, "T1" if tr.chk_target_is_t1 else "T2", "#2e7d32"))):
        o.append(f'<line x1="{xi:.1f}" x2="{xo:.1f}" y1="{Y(pr):.1f}" y2="{Y(pr):.1f}" stroke="{c}" stroke-width="1.5"/>')
        o.append(f'<text x="{xo + 4:.1f}" y="{Y(pr) + 4:.1f}" fill="{c}" font-weight="bold">{lab} {f(pr)}</text>')
    if not snap:
        o.append(f'<circle cx="{X(int(tr.t_exit) - 300):.1f}" cy="{Y(tr.exit):.1f}" r="6" fill="{"#2e7d32" if tr.r > 0 else "#c62828"}"/>')
    # price axis
    for k in range(7):
        pr = lo_p + k * (hi_p - lo_p) / 6
        o.append(f'<text x="{W - RIGHT + 8}" y="{Y(pr) + 4:.1f}" fill="#777" font-size="11">{f(pr)}</text>')
    side = "BUY" if d == 1 else "SELL"
    res = f"SNAPSHOT, engine view, no lookahead -- {tr.note}" if snap else f"{'WIN' if tr.r > 0 else 'LOSS'} {tr.r:+.2f}R ({tr.exit_reason})"
    o.append(f'<text x="{PAD}" y="30" font-size="20" font-weight="bold">{sym} {side} -- {ts(tr.t_entry)} UTC -- {res}</text>')
    o.append(f'<text x="{PAD}" y="52" fill="#555">Bennett process (M15 candles): daily bias -> {s["tf"]} {leg["kind"]} -> relief into OTE 62-79% (+FVG) -> 5m CHoCH -> {side.lower()}, '
             f'stop above the {"high" if d == -1 else "low"} / equal {"highs" if d == -1 else "lows"}, target {"T1 = leg external " + ("low" if d == -1 else "high") if tr.chk_target_is_t1 else "T2 = next liquidity"}; planned {tr.chk_rr_planned}R</text>')
    steps = [f"1. Daily bias + {s['tf']} {leg['kind']}: close through {f(s['brk'])} -> {side.lower()}s only (protected level {f(leg['start'])}).",
             f"2. Fib {f(leg['start'])} -> {f(leg['ext'])}: {'premium' if d == -1 else 'discount'} beyond 0.5, OTE {f(z_lo)}-{f(z_hi)} (dark green).",
             f"3. Area of interest: {', '.join(sorted({g[0] for g in s['gaps']})) or 'no FVG'} inside the OTE.",
             f"4. Price traded into the OTE, then a 5m CHoCH (close through the 5m swing) = trigger; entry {f(tr.entry)}.",
             f"5. Stop {f(tr.sl)} beyond the 5m swing (invalidation); target {f(tr.tp)} ({tr.chk_rr_planned}R) -> exit {f(tr.exit)} = {tr.r:+.2f}R."]
    if snap:
        steps = steps[:3] + [f"4. Price now {f(tr.entry)} at fib {(tr.entry - leg['ext']) / (leg['start'] - leg['ext']):.2f}. {tr.note}"]
    for k, line in enumerate(steps):
        o.append(f'<text x="{PAD}" y="{H - INFO + 30 + 24 * k}" font-size="14" fill="{INK}">{line}</text>')
    o.append("</svg>")
    return "\n".join(o)


async def render(paths: list[Path]) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        pg = await b.new_page(viewport={"width": W, "height": H})
        for fp in paths:
            await pg.goto(fp.resolve().as_uri())
            await pg.screenshot(path=str(fp.with_suffix(".png")))
        await b.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--rows", default="", help="row numbers of the backtest CSV, e.g. 0,3,47")
    ap.add_argument("--snapshot", action="append", default=[], help='"SYMBOL|YYYY-MM-DD HH:MM|note": the engine view at that time')
    ap.add_argument("--out", default="docs/charts/bennett")
    ap.add_argument("--set", action="append", default=[], help='strategy params of that run, e.g. ltf="M5"')
    a = ap.parse_args()
    import ast
    p = bs.BennettParams(**{k: ast.literal_eval(x) for k, x in (kv.split("=", 1) for kv in a.set)})
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for sn in a.snapshot:
        sym, when, note = sn.split("|")
        t0 = int(pd.Timestamp(when, tz="UTC").timestamp())
        last = load_m5(sym, pd.Timestamp(t0 - 86400, unit="s", tz="UTC").isoformat(), pd.Timestamp(t0, unit="s", tz="UTC").isoformat())
        px = float(last["close"][-1])
        tr = pd.Series({"symbol": sym, "t_entry": t0, "t_exit": t0, "entry": px, "sl": np.nan, "tp": np.nan, "exit": px,
                        "r": np.nan, "exit_reason": "", "chk_target_is_t1": True, "chk_rr_planned": "-", "snapshot": True, "note": note})
        fp = out / f"snap_{sym}_{pd.Timestamp(t0, unit='s'):%Y%m%d_%H%M}.svg"
        fp.write_text(svg(sym, tr, state(sym, tr, p), p), encoding="utf-8")
        files.append(fp)
    t = pd.read_csv(a.csv) if a.csv else pd.DataFrame()
    for r in (int(x) for x in a.rows.split(",") if x):
        tr = t.loc[r]
        fp = out / f"{r:02d}_{tr.symbol}_{pd.Timestamp(int(tr.t_entry), unit='s'):%Y%m%d_%H%M}.svg"
        fp.write_text(svg(tr.symbol, tr, state(tr.symbol, tr, p), p), encoding="utf-8")
        files.append(fp)
    asyncio.run(render(files))
    print("\n".join(str(fp.with_suffix(".png")) for fp in files))


if __name__ == "__main__":
    main()
