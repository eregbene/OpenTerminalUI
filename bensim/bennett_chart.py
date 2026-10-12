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

from . import bennett_strategy as bs
from . import indicators
from .backtest import load_m5, resample

W, H, PAD, TOP, RIGHT, INFO = 1500, 900, 70, 70, 110, 170
GREEN, OTE, RED, INK = "#e3f3e6", "#bfe3c7", "#f8dcdc", "#222"


def ts(t: int) -> str:
    return datetime.fromtimestamp(int(t), timezone.utc).strftime("%a %d %b %H:%M")


def state(sym: str, tr: pd.Series, p: bs.BennettParams) -> dict:
    """What the strategy saw at entry: 1H leg, OTE, FVGs in the area, the LTF CHoCH level."""
    t_in = int(tr.t_entry)
    m5 = load_m5(sym, pd.Timestamp(t_in - 12 * 86400, unit="s", tz="UTC").isoformat(),
                 pd.Timestamp(int(tr.t_exit) + 2 * 86400, unit="s", tz="UTC").isoformat())
    m15, h1 = resample(m5, 900), resample(m5, 3600)
    h = {k: v[:int(np.searchsorted(h1["t"] + 3600, t_in, side="right"))] for k, v in h1.items()}
    leg = bs.h1_leg({k: v[-200:] for k, v in h.items()}, p)
    off = len(h["t"]) - min(200, len(h["t"]))
    for k in ("e", "piv", "s_bar", "x_bar"):
        leg[k] += off
    z_lo, z_hi = bs.ote_band(leg, p)
    t0, t1 = int(h["t"][leg["s_bar"]]), int(h["t"][leg["x_bar"]]) + 3600
    gaps = []
    for name, b in (("1h FVG", h), ("15m FVG", {k: v[:int(np.searchsorted(m15["t"] + 900, t_in, side="right"))] for k, v in m15.items()})):
        for z in indicators.fvgs(b, auto=False):
            if z["dir"] == leg["d"] and t0 <= b["t"][z["formed"]] <= t1 and z["top"] >= z_lo and z["bottom"] <= z_hi:
                gaps.append((name, int(b["t"][z["formed"] - 2]), z["bottom"], z["top"]))
    k5 = int(np.searchsorted(m5["t"] + 300, t_in, side="right"))  # M5 bars closed at entry
    ltf = {k: v[max(0, k5 - 100):k5] for k, v in m5.items()}
    st = indicators.structure(ltf, p.ltf_len, indicators.atr_n(ltf, 200), min_break=p.break_atr * bs.atr(ltf))
    j = len(ltf["t"]) - 1
    piv = int(st["pivot"][j])
    choch = (int(ltf["t"][piv]), int(ltf["t"][j]), float(ltf["low"][piv] if leg["d"] == -1 else ltf["high"][piv])) if piv >= 0 else None
    brk_lvl = float(h["low"][leg["piv"]] if leg["d"] == -1 else h["high"][leg["piv"]])
    return {"m15": m15, "leg": leg, "h": h, "ote": (z_lo, z_hi), "gaps": gaps, "choch": choch, "brk": brk_lvl}


def svg(sym: str, tr: pd.Series, s: dict, p: bs.BennettParams) -> str:
    m, leg, d = s["m15"], s["leg"], s["leg"]["d"]
    t_start = int(s["h"]["t"][leg["s_bar"]]) - 6 * 3600
    t_end = int(tr.t_exit) + 8 * 3600
    i0, i1 = int(np.searchsorted(m["t"], t_start)), int(np.searchsorted(m["t"], t_end))
    v = {k: x[i0:i1] for k, x in m.items()}
    n = len(v["t"])
    lo_p = min(v["low"].min(), tr.sl, tr.tp, leg["ext"], leg["start"])
    hi_p = max(v["high"].max(), tr.sl, tr.tp, leg["ext"], leg["start"])
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
    # 1H break (BOS / CHoCH) and the 5M CHoCH trigger
    xp, xe = X(int(s["h"]["t"][leg["piv"]])), X(int(s["h"]["t"][leg["e"]]) + 3600)
    o.append(f'<line x1="{xp:.1f}" x2="{xe:.1f}" y1="{Y(s["brk"]):.1f}" y2="{Y(s["brk"]):.1f}" stroke="#000" stroke-width="2"/>')
    o.append(f'<text x="{(xp + xe) / 2:.1f}" y="{Y(s["brk"]) - 5:.1f}" text-anchor="middle" font-weight="bold">1H {leg["kind"]}</text>')
    if s["choch"]:
        ta, tb, lv = s["choch"]
        o.append(f'<line x1="{X(ta):.1f}" x2="{X(tb):.1f}" y1="{Y(lv):.1f}" y2="{Y(lv):.1f}" stroke="#c2185b" stroke-width="2"/>')
        o.append(f'<text x="{X(ta) - 4:.1f}" y="{Y(lv) + (16 if d == -1 else -7):.1f}" text-anchor="end" fill="#c2185b" font-weight="bold">5m CHoCH</text>')
    # position box: risk red, reward green, from entry to exit
    xi, xo = X(int(tr.t_entry) - 300), X(int(tr.t_exit) - 300)
    xo = max(xo, xi + 30)
    for a_, b_, c in ((tr.entry, tr.sl, "rgba(220,60,60,0.30)"), (tr.entry, tr.tp, "rgba(40,160,90,0.30)")):
        o.append(f'<rect x="{xi:.1f}" y="{Y(max(a_, b_)):.1f}" width="{xo - xi:.1f}" height="{abs(Y(a_) - Y(b_)):.1f}" fill="{c}"/>')
    for pr, lab, c in ((tr.entry, "Entry", "#111"), (tr.sl, "SL", "#c62828"), (tr.tp, "T1" if tr.chk_target_is_t1 else "T2", "#2e7d32")):
        o.append(f'<line x1="{xi:.1f}" x2="{xo:.1f}" y1="{Y(pr):.1f}" y2="{Y(pr):.1f}" stroke="{c}" stroke-width="1.5"/>')
        o.append(f'<text x="{xo + 4:.1f}" y="{Y(pr) + 4:.1f}" fill="{c}" font-weight="bold">{lab} {f(pr)}</text>')
    o.append(f'<circle cx="{X(int(tr.t_exit) - 300):.1f}" cy="{Y(tr.exit):.1f}" r="6" fill="{"#2e7d32" if tr.r > 0 else "#c62828"}"/>')
    # price axis
    for k in range(7):
        pr = lo_p + k * (hi_p - lo_p) / 6
        o.append(f'<text x="{W - RIGHT + 8}" y="{Y(pr) + 4:.1f}" fill="#777" font-size="11">{f(pr)}</text>')
    side = "BUY" if d == 1 else "SELL"
    res = f"{'WIN' if tr.r > 0 else 'LOSS'} {tr.r:+.2f}R ({tr.exit_reason})"
    o.append(f'<text x="{PAD}" y="30" font-size="20" font-weight="bold">{sym} {side} -- {ts(tr.t_entry)} UTC -- {res}</text>')
    o.append(f'<text x="{PAD}" y="52" fill="#555">Bennett model (M15 candles): 1H {leg["kind"]} -> pullback into OTE 62-79% (+FVG) -> 5m CHoCH -> {side.lower()}, '
             f'stop beyond the 5m swing, target {"T1 = prior 1H " + ("low" if d == -1 else "high") if tr.chk_target_is_t1 else "T2 = next liquidity"}; DXY agreed; planned {tr.chk_rr_planned}R</text>')
    steps = [f"1. 1H {leg['kind']}: close through {f(s['brk'])} -> {side.lower()}s only.",
             f"2. Fib {f(leg['start'])} -> {f(leg['ext'])}: {'premium' if d == -1 else 'discount'} beyond 0.5, OTE {f(z_lo)}-{f(z_hi)} (dark green).",
             f"3. Area of interest: {', '.join(sorted({g[0] for g in s['gaps']})) or 'no FVG'} inside the OTE.",
             f"4. Price traded into the OTE, then a 5m CHoCH (close through the 5m swing) = trigger; entry {f(tr.entry)}.",
             f"5. Stop {f(tr.sl)} beyond the 5m swing (invalidation); target {f(tr.tp)} ({tr.chk_rr_planned}R) -> exit {f(tr.exit)} = {tr.r:+.2f}R."]
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
    ap.add_argument("--csv", required=True)
    ap.add_argument("--rows", required=True, help="row numbers of the backtest CSV, e.g. 0,3,47")
    ap.add_argument("--out", default="docs/charts/bennett")
    ap.add_argument("--set", action="append", default=[], help='strategy params of that run, e.g. ltf="M5"')
    a = ap.parse_args()
    import ast
    p = bs.BennettParams(**{k: ast.literal_eval(x) for k, x in (kv.split("=", 1) for kv in a.set)})
    t = pd.read_csv(a.csv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for r in (int(x) for x in a.rows.split(",")):
        tr = t.loc[r]
        fp = out / f"{r:02d}_{tr.symbol}_{pd.Timestamp(int(tr.t_entry), unit='s'):%Y%m%d_%H%M}.svg"
        fp.write_text(svg(tr.symbol, tr, state(tr.symbol, tr, p), p), encoding="utf-8")
        files.append(fp)
    asyncio.run(render(files))
    print("\n".join(str(fp.with_suffix(".png")) for fp in files))


if __name__ == "__main__":
    main()
