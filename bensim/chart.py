"""Draw backtest trades exactly as the code saw them, to check the strategy against hand-marked charts.

    python -m bensim.chart --run 1 [--limit 11] [--out docs/charts]

Per trade, an M15 chart: candles, the 20-bar Donchian high/low (the "break" level), every 3-candle gap
(FVG) shaded, the chosen zone (base candle) as a box, entry / stop / target lines, and the actual fill
and exit. Written as SVG and rendered to PNG with Playwright.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import db
from .backtest import _win, load_m5, resample
from .strategy import Params, plan

W, H, PAD = 1400, 700, 60


def svg(m: dict, plan_: dict, trade: dict, title: str) -> str:
    n = len(m["t"])
    lo, hi = float(m["low"].min()), float(m["high"].max())
    for v in (plan_["entry"], plan_["stop"], plan_["target"]):
        lo, hi = min(lo, v), max(hi, v)
    pad = (hi - lo) * 0.05
    lo, hi = lo - pad, hi + pad
    cw = (W - 2 * PAD) / n
    x = lambda i: PAD + (i + 0.5) * cw  # noqa: E731
    y = lambda v: PAD + (hi - v) / (hi - lo) * (H - 2 * PAD)  # noqa: E731
    idx = {int(t): i for i, t in enumerate(m["t"])}
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif" font-size="13">',
           f'<rect width="{W}" height="{H}" fill="#fff"/>', f'<text x="{PAD}" y="30" font-size="16" font-weight="bold">{title}</text>']
    # 3-candle gaps (FVG) in both directions
    for j in range(2, n):
        if m["high"][j - 2] < m["low"][j]:
            out.append(f'<rect x="{x(j - 1) - cw / 2}" y="{y(m["low"][j])}" width="{cw * 3}" height="{y(m["high"][j - 2]) - y(m["low"][j])}" fill="#2e7d32" opacity="0.15"/>')
        if m["low"][j - 2] > m["high"][j]:
            out.append(f'<rect x="{x(j - 1) - cw / 2}" y="{y(m["low"][j - 2])}" width="{cw * 3}" height="{y(m["high"][j]) - y(m["low"][j - 2])}" fill="#c62828" opacity="0.15"/>')
    # Donchian 20 (channel of the bars BEFORE each bar = the level a close must break)
    for arr, col in ((m["high"], "#1565c0"), (m["low"], "#1565c0")):
        pts = [f"{x(i)},{y((arr[i - 20:i].max() if arr is m['high'] else arr[i - 20:i].min()))}" for i in range(20, n)]
        out.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{col}" stroke-dasharray="4 3" stroke-width="1"/>')
    for i in range(n):
        o, h_, l_, c = (float(m[k][i]) for k in ("open", "high", "low", "close"))
        col = "#26a69a" if c >= o else "#ef5350"
        out.append(f'<line x1="{x(i)}" x2="{x(i)}" y1="{y(h_)}" y2="{y(l_)}" stroke="{col}"/>')
        out.append(f'<rect x="{x(i) - cw * 0.35}" y="{y(max(o, c))}" width="{cw * 0.7}" height="{max(1, abs(y(o) - y(c)))}" fill="{col}"/>')
    b, s, e = idx.get(plan_["zone_base_time"]), idx.get(plan_["signal_time"]), (trade["t_entry"] // 900) * 900
    ei, xi = idx.get(int(e)), idx.get(int((trade["t_exit"] - 300) // 900 * 900))
    if b is not None:
        zt, zb = (m["high"][b], m["low"][b])
        out.append(f'<rect x="{x(b) - cw / 2}" y="{y(zt)}" width="{(W - PAD) - (x(b) - cw / 2)}" height="{y(zb) - y(zt)}" fill="none" stroke="#6a1b9a" stroke-width="2"/>')
        out.append(f'<text x="{x(b) - 30}" y="{y(zb) + 18}" fill="#6a1b9a">zone = base candle</text>')
    if s is not None:
        out.append(f'<line x1="{x(s)}" x2="{x(s)}" y1="{PAD}" y2="{H - PAD}" stroke="#ff8f00" stroke-dasharray="6 4"/>')
        out.append(f'<text x="{x(s) + 4}" y="{PAD + 14}" fill="#ff8f00">break (signal)</text>')
    x0 = x(s) if s is not None else PAD
    for v, col, lab in ((plan_["entry"], "#000", "entry"), (plan_["stop"], "#c62828", "stop"), (plan_["target"], "#2e7d32", "target")):
        out.append(f'<line x1="{x0}" x2="{W - PAD}" y1="{y(v)}" y2="{y(v)}" stroke="{col}" stroke-width="1.5"/>')
        out.append(f'<text x="{W - PAD + 4}" y="{y(v) + 4}" fill="{col}">{lab}</text>')
    if ei is not None:
        out.append(f'<circle cx="{x(ei)}" cy="{y(trade["entry"])}" r="6" fill="none" stroke="#000" stroke-width="2"/>')
    if xi is not None:
        out.append(f'<circle cx="{x(xi)}" cy="{y(trade["exit"])}" r="6" fill="{"#2e7d32" if trade["r"] > 0 else "#c62828"}"/>')
    out.append(f'<text x="{PAD}" y="{H - 20}">blue dashed = 20-bar Donchian high/low · green/red shading = bullish/bearish 3-candle gaps (FVG) · '
               f'purple box = chosen zone · circle = fill, dot = exit</text></svg>')
    return "\n".join(out)


async def render(paths: list[Path]) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        p = await b.new_page(viewport={"width": W, "height": H})
        for f in paths:
            await p.goto(f.resolve().as_uri())
            await p.screenshot(path=str(f.with_suffix(".png")))
        await b.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--out", default="docs/charts")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with db.connect() as c:
        rows = c.execute("""select symbol, extract(epoch from t_entry)::bigint, extract(epoch from t_exit)::bigint, entry, exit, r, exit_reason, dir
                            from backtest_trades where run_id=%s order by t_entry limit %s""", (a.run, a.limit)).fetchall()
    files = []
    for sym, te, tx, entry, exit_, r, why, d in rows:
        m5 = load_m5(sym, datetime.fromtimestamp(te - 40 * 86400, timezone.utc).isoformat(), datetime.fromtimestamp(tx + 86400, timezone.utc).isoformat())
        m15, h1 = resample(m5, 900), resample(m5, 3600)
        k = int(np.searchsorted(m5["t"], te - 300))  # the M5 bar whose close triggered
        now = te
        pl = plan(_win(m15, int(np.searchsorted(m15["t"] + 900, now, side="right")), 300), _win(h1, int(np.searchsorted(h1["t"] + 3600, now, side="right")), 200),
                  _win(m5, k + 1, 100), sym, Params())
        if not pl or pl.get("status") != "TRIGGERED":
            print(f"{sym} {datetime.fromtimestamp(te, timezone.utc)}: plan not reproduced ({pl})")
            continue
        a0 = int(np.searchsorted(m15["t"], pl["signal_time"])) - 45
        a1 = int(np.searchsorted(m15["t"], tx)) + 6
        sl = {kk: v[max(0, a0):a1] for kk, v in m15.items()}
        title = (f"{sym} {'BUY' if d == 1 else 'SELL'}  entry {datetime.fromtimestamp(te, timezone.utc):%Y-%m-%d %H:%M} UTC  "
                 f"-> {why}  {r:+.2f}R   (M15)")
        f = out / f"{sym}_{datetime.fromtimestamp(te, timezone.utc):%Y%m%d_%H%M}.svg"
        f.write_text(svg(sl, pl, {"t_entry": te, "t_exit": tx, "entry": entry, "exit": exit_, "r": r}, title), encoding="utf-8")
        files.append(f)
    asyncio.run(render(files))
    print("\n".join(str(f.with_suffix(".png")) for f in files))


if __name__ == "__main__":
    main()
