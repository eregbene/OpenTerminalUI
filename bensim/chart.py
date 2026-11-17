"""Draw backtest trades exactly as the code saw them, to check the strategy against hand-marked charts.

    python -m bensim.chart --run 60 [--limit 12] [--trade N] [--out docs/charts]

Per trade, an M15 chart with the run's own parameters: candles, killzones (shaded columns), the 20-bar Donchian
high/low (the break level), swing + internal BOS/CHoCH, LuxAlgo FVGs and order blocks (the POI the entry sits in
drawn bold), the fib leg with 0.5 / 0.618 / 0.786, entry / stop / target, the fill and the exit, and the
confirmation checks the code saw at the signal. Written as SVG and rendered to PNG with Playwright.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import db, indicators
from .backtest import _win, load_m5, resample
from .strategy import Params, plan

W, H, PAD, INFO = 1500, 820, 60, 120
BULL, BEAR = "#2e7d32", "#c62828"


def svg(m: dict, ind: dict, off: int, pl: dict, trade: dict, p: Params, title: str, tf: int = 900) -> str:
    """m = the bars to draw (bar size `tf` seconds); ind = indicators on a longer window whose bar `off` is m's bar 0."""
    n, d = len(m["t"]), trade["dir"]
    idx = {int(t): i for i, t in enumerate(m["t"])}
    b, s = idx.get(pl["zone_base_time"] // tf * tf), idx.get(pl["signal_time"] // tf * tf)
    lo, hi = float(m["low"].min()), float(m["high"].max())
    for v in (pl["entry"], pl["stop"], pl["target"]):
        lo, hi = min(lo, v), max(hi, v)
    lo, hi = lo - (hi - lo) * 0.04, hi + (hi - lo) * 0.04
    cw = (W - 2 * PAD - 70) / n
    x = lambda i: PAD + (i + 0.5) * cw  # noqa: E731
    y = lambda v: PAD + (hi - v) / (hi - lo) * (H - INFO - 2 * PAD)  # noqa: E731
    right = W - PAD - 70
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif" font-size="12">',
         f'<rect width="{W}" height="{H}" fill="#fff"/>', f'<text x="{PAD}" y="30" font-size="16" font-weight="bold">{title}</text>']
    box = lambda i0, i1, top, bot, col, op, sw=0, dash="": (  # noqa: E731
        f'<rect x="{x(i0) - cw / 2}" y="{y(top)}" width="{max(cw, x(min(i1, n - 1)) - x(i0) + cw)}" height="{max(1, y(bot) - y(top))}" '
        f'fill="{col}" fill-opacity="{op}" stroke="{col}" stroke-width="{sw}" stroke-dasharray="{dash}"/>')
    # killzones: London 07-10, New York 12-15 UTC
    hours = (m["t"] % 86400) // 3600
    for i in range(n):
        if any(a <= hours[i] < z for a, z in indicators.KILLZONES):
            o.append(f'<rect x="{x(i) - cw / 2}" y="{PAD}" width="{cw}" height="{H - INFO - 2 * PAD}" fill="#fff59d" opacity="0.35"/>')
    # LuxAlgo FVGs and order blocks formed in view (faint), the POI the entry sits in (bold)
    si = s + off if s is not None else -1
    for kind, zones in (("FVG", ind["fvgs"]), ("OB", ind["swing"]["obs"] + ind["internal"]["obs"])):
        for z in zones:
            start = z.get("bar", z["formed"] - 1) - off
            end = z["mitigated"] - off
            if end < 0 or start >= n:
                continue
            col = BULL if z["dir"] == 1 else BEAR
            poi = z["dir"] == d and z["formed"] <= si < z["mitigated"] and z["bottom"] <= pl["entry"] <= z["top"]
            o.append(box(max(0, start), end, z["top"], z["bottom"], col, 0.28 if poi else 0.08, 2 if poi else 0, "" if poi else "3 3"))
            if poi:
                o.append(f'<text x="{x(max(0, start))}" y="{y(z["top"]) - 4}" fill="{col}" font-weight="bold">POI ({kind})</text>')
            elif kind == "OB":
                o.append(f'<text x="{x(max(0, start))}" y="{y(z["top"]) - 2}" fill="{col}" font-size="10" opacity="0.7">OB</text>')
    # Donchian 20: the level a close must break
    for arr in (m["high"], m["low"]):
        pts = [f"{x(i)},{y(arr[i - 20:i].max() if arr is m['high'] else arr[i - 20:i].min())}" for i in range(20, n)]
        o.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="#1565c0" stroke-dasharray="4 3" stroke-width="1" opacity="0.6"/>')
    # candles
    for i in range(n):
        op_, h_, l_, c = (float(m[k][i]) for k in ("open", "high", "low", "close"))
        col = "#26a69a" if c >= op_ else "#ef5350"
        o.append(f'<line x1="{x(i)}" x2="{x(i)}" y1="{y(h_)}" y2="{y(l_)}" stroke="{col}"/>')
        o.append(f'<rect x="{x(i) - cw * 0.35}" y="{y(max(op_, c))}" width="{cw * 0.7}" height="{max(1, abs(y(op_) - y(c)))}" fill="{col}"/>')
    # structure: BOS / CHoCH (swing = bold, internal = thin "i-")
    for layer, bold in (("swing", True), ("internal", False)):
        st = ind[layer]
        for j in np.flatnonzero(st["event"] != 0):
            jb, pv = j - off, st["pivot"][j] - off
            if jb < 0 or jb >= n:
                continue
            dd = st["event"][j]
            lvl = float(m["high"][pv] if dd == 1 else m["low"][pv]) if 0 <= pv < n else float(st["top"][j - 1] if dd == 1 else st["btm"][j - 1])
            col = BULL if dd == 1 else BEAR
            lab = ("CHoCH" if st["kind"][j] else "BOS") if bold else ("i-CHoCH" if st["kind"][j] else "i-BOS")
            x0 = x(max(0, pv))
            o.append(f'<line x1="{x0}" x2="{x(jb)}" y1="{y(lvl)}" y2="{y(lvl)}" stroke="{col}" stroke-width="{2 if bold else 1}" stroke-dasharray="{"" if bold else "2 2"}"/>')
            o.append(f'<text x="{(x0 + x(jb)) / 2 - 15}" y="{y(lvl) + (-5 if dd == 1 else 13)}" fill="{col}" font-size="{12 if bold else 10}" font-weight="{"bold" if bold else "normal"}">{lab}</text>')
    # fib leg (zone_mode=fib): 100% = leg origin (zone base bar), 0% = leg extreme before the signal
    if p.zone_mode == "fib" and b is not None and s is not None:
        leg0 = float(m["low"][b] if d == 1 else m["high"][b])
        leg1 = float(m["high"][b:s + 1].max() if d == 1 else m["low"][b:s + 1].min())
        o.append(f'<line x1="{x(b)}" x2="{x(s)}" y1="{y(leg0)}" y2="{y(leg1)}" stroke="#6a1b9a" stroke-width="1.5"/>')
        for f in (0.0, 0.5, 0.618, 0.786, 1.0):
            v = leg1 - d * f * abs(leg1 - leg0)
            o.append(f'<line x1="{x(b)}" x2="{right}" y1="{y(v)}" y2="{y(v)}" stroke="#6a1b9a" stroke-width="{1.5 if f == 0.618 else 0.7}" stroke-dasharray="{"" if f == 0.618 else "5 4"}" opacity="0.8"/>')
            o.append(f'<text x="{x(b) - 44}" y="{y(v) + 4}" fill="#6a1b9a" font-size="11">fib {f:g}</text>')
    if s is not None:
        o.append(f'<line x1="{x(s)}" x2="{x(s)}" y1="{PAD}" y2="{H - INFO - PAD}" stroke="#ff8f00" stroke-dasharray="6 4"/>')
        o.append(f'<text x="{x(s) + 4}" y="{PAD + 14}" fill="#ff8f00" font-weight="bold">signal: Donchian break</text>')
    x0 = x(s) if s is not None else PAD
    for v, col, lab in ((pl["entry"], "#000", "entry"), (pl["stop"], BEAR, "stop"), (pl["target"], BULL, "target")):
        o.append(f'<line x1="{x0}" x2="{right}" y1="{y(v)}" y2="{y(v)}" stroke="{col}" stroke-width="2"/>')
        o.append(f'<text x="{right + 4}" y="{y(v) + 4}" fill="{col}" font-weight="bold">{lab}</text>')
    ei, xi = idx.get(trade["t_entry"] // tf * tf), idx.get((trade["t_exit"] - 300) // tf * tf)
    if s is not None:  # the pending order: placed at the signal close at fib 0.618, valid plan_bars M15 bars
        end = ei if ei is not None else min(n - 1, s + p.plan_bars)
        o.append(f'<line x1="{x(s)}" x2="{x(end)}" y1="{y(pl["entry"])}" y2="{y(pl["entry"])}" stroke="#0d47a1" stroke-width="5" opacity="0.35"/>')
        o.append(f'<text x="{x(s) + 4}" y="{y(pl["entry"]) + (16 if d == 1 else -8)}" fill="#0d47a1" font-weight="bold">'
                 f'{"BUY" if d == 1 else "SELL"} LIMIT @ {pl["entry"]:.5g} (placed at signal, valid {p.plan_bars} bars)</text>')
        if ei is None and s + p.plan_bars < n:
            o.append(f'<line x1="{x(s + p.plan_bars)}" x2="{x(s + p.plan_bars)}" y1="{y(pl["entry"]) - 12}" y2="{y(pl["entry"]) + 12}" stroke="#0d47a1" stroke-width="2"/>')
            o.append(f'<text x="{x(s + p.plan_bars) + 3}" y="{y(pl["entry"]) - 14}" fill="#0d47a1" font-size="10">order expiry</text>')
    if ei is not None:  # TradingView-style position tool: green = entry->TP, red = entry->SL, from fill to exit
        x1 = xi if xi is not None else n - 1
        o.append(box(ei, x1, max(pl["entry"], pl["target"]), min(pl["entry"], pl["target"]), BULL, 0.22, 1))
        o.append(box(ei, x1, max(pl["entry"], pl["stop"]), min(pl["entry"], pl["stop"]), BEAR, 0.22, 1))
        o.append(f'<circle cx="{x(ei)}" cy="{y(trade["entry"])}" r="7" fill="none" stroke="#000" stroke-width="2.5"/>')
        o.append(f'<text x="{x(ei) + 9}" y="{y(trade["entry"]) - 9}" font-weight="bold">FILLED</text>')
    if xi is not None:
        o.append(f'<circle cx="{x(xi)}" cy="{y(trade["exit"])}" r="7" fill="{BULL if trade["r"] > 0 else BEAR}"/>')
    # what the code checked at the signal bar
    ck = pl.get("checks", {})
    yy = H - INFO + 10
    rows = [f"Required: H1 swing trend = {'up' if d == 1 else 'down'} (always) + {', '.join(p.confirm) or 'nothing extra'}",
            "Checks at signal:  " + "   ".join(f"{k}={v}" for k, v in ck.items()),
            "Legend: yellow columns = killzone (London 07-10, NY 12-15 UTC) · bold box = POI (FVG/OB the entry sits in) · faint dashed boxes = other FVG/OB · "
            "BOS/CHoCH bold = swing (50), i- = internal (5) · purple = fib leg · blue dashed = Donchian 20 · circle = fill, dot = exit"]
    for k, r in enumerate(rows):
        o.append(f'<text x="{PAD}" y="{yy + 22 * k}" font-size="{12 if k < 2 else 11}">{r}</text>')
    o.append("</svg>")
    return "\n".join(o)


async def render(paths: list[Path]) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        pg = await b.new_page(viewport={"width": W, "height": H})
        for f in paths:
            await pg.goto(f.resolve().as_uri())
            await pg.screenshot(path=str(f.with_suffix(".png")))
        await b.close()


def run_params(params: dict) -> Params:
    p = {k: v for k, v in params.items() if k in Params.__dataclass_fields__}
    p["confirm"] = tuple(p.get("confirm", ()))
    p["killzones"] = tuple(tuple(z) for z in p.get("killzones", ()))
    return Params(**p)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--trade", type=int, default=None, help="only the Nth trade of the run (1 = first)")
    ap.add_argument("--out", default="docs/charts")
    a = ap.parse_args()
    out = Path(a.out) / f"run{a.run}"
    out.mkdir(parents=True, exist_ok=True)
    with db.connect() as c:
        p = run_params(c.execute("select params from backtest_runs where id=%s", (a.run,)).fetchone()[0])
        rows = c.execute("""select symbol, extract(epoch from t_entry)::bigint, extract(epoch from t_exit)::bigint, entry, exit, r, exit_reason, dir
                            from backtest_trades where run_id=%s order by t_entry""", (a.run,)).fetchall()
    rows = [rows[a.trade - 1]] if a.trade else rows[:a.limit]
    files = []
    for sym, te, tx, entry, exit_, r, why, d in rows:
        m5 = load_m5(sym, datetime.fromtimestamp(te - 40 * 86400, timezone.utc).isoformat(), datetime.fromtimestamp(tx + 86400, timezone.utc).isoformat())
        m15, h1 = resample(m5, 900), resample(m5, 3600)
        k = int(np.searchsorted(m5["t"], te - 300))  # the M5 bar whose close triggered
        z15 = int(np.searchsorted(m15["t"] + 900, te, side="right"))
        pl = plan(_win(m15, z15, 300), _win(h1, int(np.searchsorted(h1["t"] + 3600, te, side="right")), 200), _win(m5, k + 1, 100), sym, p)
        if not pl or pl.get("status") != "TRIGGERED":
            print(f"{sym} {datetime.fromtimestamp(te, timezone.utc)}: plan not reproduced ({pl})")
            continue
        sig = int(np.searchsorted(m15["t"], pl["signal_time"]))
        a0, a1 = max(0, min(sig - 45, int(np.searchsorted(m15["t"], pl["zone_base_time"])) - 10)), int(np.searchsorted(m15["t"], tx)) + 6
        w0 = max(0, z15 - 300)  # same window start the plan used, so swings/OBs/FVGs match what it saw
        ind = indicators.compute({kk: v[w0:a1] for kk, v in m15.items()})
        view = {kk: v[a0:a1] for kk, v in m15.items()}
        title = (f"{sym} {'BUY' if d == 1 else 'SELL'}  signal {datetime.fromtimestamp(pl['signal_time'], timezone.utc):%Y-%m-%d %H:%M}  "
                 f"fill {datetime.fromtimestamp(te, timezone.utc):%H:%M} UTC  -> {why}  {r:+.2f}R   (M15, run {a.run})")
        f = out / f"{sym}_{datetime.fromtimestamp(te, timezone.utc):%Y%m%d_%H%M}.svg"
        f.write_text(svg(view, ind, a0 - w0, pl, {"t_entry": te, "t_exit": tx, "entry": entry, "exit": exit_, "r": r, "dir": d}, p, title), encoding="utf-8")
        files.append(f)
        # higher-timeframe context, as a trader would check it first: the same drawing on H4
        h4 = resample(m5, 14400)
        s4, x4 = int(np.searchsorted(h4["t"], pl["signal_time"] // 14400 * 14400)), int(np.searchsorted(h4["t"], tx)) + 4
        ind4 = indicators.compute({kk: v[:x4] for kk, v in h4.items()})
        v0 = max(0, s4 - 90)
        f4 = out / f"{sym}_{datetime.fromtimestamp(te, timezone.utc):%Y%m%d_%H%M}_H4.svg"
        f4.write_text(svg({kk: v[v0:x4] for kk, v in h4.items()}, ind4, v0, pl, {"t_entry": te, "t_exit": tx, "entry": entry, "exit": exit_, "r": r, "dir": d},
                          p, title.replace("(M15", "(H4 context"), tf=14400), encoding="utf-8")
        files.append(f4)
    asyncio.run(render(files))
    print("\n".join(str(f.with_suffix(".png")) for f in files))


if __name__ == "__main__":
    main()
