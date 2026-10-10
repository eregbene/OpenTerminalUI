"""Next-session plan chart per symbol, from the newest stored bars (what the live engine will see at the open):

    python -m bensim.plan_chart [--out docs/charts/plan_monday] [--local-utc-offset 3]

M15 candles of the last ~2.5 trading days with: H1 trend (allowed side), swing + internal BOS/CHoCH, open (unmitigated)
LuxAlgo FVGs and order blocks (POI candidates), liquidity levels (previous-day high/low, Asian range, last swing
high/low), the 20-candle Donchian high/low and the exact M15 close that would trigger the next signal, the next
session's killzones, and a short plan text. SVG + PNG (Playwright), one file per symbol.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import indicators
from .backtest import load_m5, resample
from .config import SYMBOLS
from .strategy import Params, atr, trend

W, H, PAD, RIGHT, INFO = 1500, 860, 60, 330, 150
BULL, BEAR = "#2e7d32", "#c62828"
P = Params(zone_mode="fib", confirm_any=("killzone", "poi", "sweep"), confirm_min=2)


def svg(sym: str, m: dict, ind: dict, h1_trend: int, local: int) -> str:
    n = len(m["t"])
    view = slice(max(0, n - 170), n)
    t, o, h, lo_, c = (m[k][view] for k in ("t", "open", "high", "low", "close"))
    off = view.start
    nv = len(t)
    a = atr(m)[-1]
    hi20, lo20 = m["high"][-20:].max(), m["low"][-20:].min()
    buy_trig, sell_trig = hi20 + P.min_break_atr * a, lo20 - P.min_break_atr * a
    pdh, pdl = ind["pdh"][-1], ind["pdl"][-1]
    asia = (ind["asia_hi"][-1], ind["asia_lo"][-1])
    sw = ind["swing"]
    zones = [z for z in ind["fvgs"] if z["mitigated"] >= n] + [z | {"ob": True} for z in sw["obs"] + ind["internal"]["obs"] if z["mitigated"] >= n]
    levels = [buy_trig, sell_trig, pdh, pdl, *[x for x in asia if np.isfinite(x)]]
    lo_p = min(lo_.min(), *[v for v in levels if np.isfinite(v)], *[z["bottom"] for z in zones if z["top"] >= lo_.min() - 3 * a])
    hi_p = max(h.max(), *[v for v in levels if np.isfinite(v)], *[z["top"] for z in zones if z["bottom"] <= h.max() + 3 * a])
    pad = (hi_p - lo_p) * 0.04
    lo_p, hi_p = lo_p - pad, hi_p + pad
    plot_w = W - PAD - RIGHT
    cw = plot_w / (nv + 40)  # 40 empty candles on the right = the coming session
    x = lambda i: PAD + (i + 0.5) * cw  # noqa: E731
    y = lambda v: PAD + (hi_p - v) / (hi_p - lo_p) * (H - INFO - 2 * PAD)  # noqa: E731
    xr = PAD + plot_w
    side = {1: "BUYS only (H1 trend UP)", -1: "SELLS only (H1 trend DOWN)", 0: "no side (H1 trend unclear)"}[h1_trend]
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" font-family="sans-serif" font-size="12">',
           f'<rect width="{W}" height="{H}" fill="#fff"/>',
           f'<text x="{PAD}" y="28" font-size="18" font-weight="bold">{sym} -- plan for the next session (M15)   |   {side}</text>']
    # past killzones + next session's killzones (in the empty area on the right)
    hours = (t % 86400) // 3600
    for i in range(nv):
        if any(s <= hours[i] < e for s, e in indicators.KILLZONES):
            out.append(f'<rect x="{x(i) - cw / 2}" y="{PAD}" width="{cw}" height="{H - INFO - 2 * PAD}" fill="#fff59d" opacity="0.35"/>')
    nxt = x(nv) - cw / 2
    out.append(f'<rect x="{nxt}" y="{PAD}" width="{40 * cw}" height="{H - INFO - 2 * PAD}" fill="#e3f2fd" opacity="0.5"/>')
    out.append(f'<text x="{nxt + 6}" y="{H - INFO - PAD - 40}" font-weight="bold" fill="#1565c0">NEXT SESSION</text>')
    out.append(f'<text x="{nxt + 6}" y="{H - INFO - PAD - 24}" fill="#1565c0">London KZ 07-10 UTC ({7 + local:02d}-{10 + local:02d} your time)</text>')
    out.append(f'<text x="{nxt + 6}" y="{H - INFO - PAD - 8}" fill="#1565c0">New York KZ 12-15 UTC ({12 + local:02d}-{15 + local:02d} your time)</text>')
    # POI candidates: open FVGs and order blocks, extended right
    for z in zones:
        if z["bottom"] > hi_p or z["top"] < lo_p:
            continue
        col = BULL if z["dir"] == 1 else BEAR
        x0 = x(max(0, z.get("bar", z["formed"] - 1) - off)) - cw / 2
        kind = "OB" if z.get("ob") else "FVG"
        out.append(f'<rect x="{x0}" y="{y(z["top"])}" width="{xr - x0}" height="{max(2, y(z["bottom"]) - y(z["top"]))}" fill="{col}" fill-opacity="0.13" stroke="{col}" stroke-dasharray="4 3"/>')
        out.append(f'<text x="{xr + 4}" y="{(y(z["top"]) + y(z["bottom"])) / 2 + 4}" fill="{col}">{"bullish" if z["dir"] == 1 else "bearish"} {kind} {z["bottom"]:.5g}-{z["top"]:.5g}</text>')
    # structure: BOS / CHoCH in view
    for layer, bold in (("swing", True), ("internal", False)):
        st = ind[layer]
        for j in np.flatnonzero(st["event"] != 0):
            jb, pv = j - off, st["pivot"][j] - off
            if jb < 0:
                continue
            d = st["event"][j]
            lvl = float(m["high"][st["pivot"][j]] if d == 1 else m["low"][st["pivot"][j]])
            col = BULL if d == 1 else BEAR
            lab = ("CHoCH" if st["kind"][j] else "BOS") if bold else ("i-CHoCH" if st["kind"][j] else "i-BOS")
            out.append(f'<line x1="{x(max(0, pv))}" x2="{x(jb)}" y1="{y(lvl)}" y2="{y(lvl)}" stroke="{col}" stroke-width="{2 if bold else 1}" stroke-dasharray="{"" if bold else "3 2"}"/>')
            out.append(f'<text x="{x(max(0, pv))}" y="{y(lvl) + (-4 if d == 1 else 12)}" fill="{col}" font-size="{12 if bold else 10}">{lab}</text>')
    # candles
    for i in range(nv):
        col = "#26a69a" if c[i] >= o[i] else "#ef5350"
        out.append(f'<line x1="{x(i)}" x2="{x(i)}" y1="{y(h[i])}" y2="{y(lo_[i])}" stroke="{col}"/>')
        out.append(f'<rect x="{x(i) - cw * 0.35}" y="{y(max(o[i], c[i]))}" width="{cw * 0.7}" height="{max(1, abs(y(o[i]) - y(c[i])))}" fill="{col}"/>')
    # liquidity + triggers
    def hline(v: float, col: str, text: str, dash: str = "6 4", width: float = 1.5) -> None:
        if not np.isfinite(v):
            return
        out.append(f'<line x1="{PAD}" x2="{xr}" y1="{y(v)}" y2="{y(v)}" stroke="{col}" stroke-width="{width}" stroke-dasharray="{dash}"/>')
        out.append(f'<text x="{xr + 4}" y="{y(v) + 4}" fill="{col}" font-weight="bold">{text}</text>')
    pday = datetime.fromtimestamp(int(t[-1]) - 86400, timezone.utc).strftime("%a")
    hline(pdh, "#6a1b9a", f"{pday} HIGH {pdh:.5g} (liquidity)")
    hline(pdl, "#6a1b9a", f"{pday} LOW {pdl:.5g} (liquidity)")
    if np.isfinite(asia[0]):
        hline(asia[0], "#8d6e63", f"Asian HIGH {asia[0]:.5g}", "2 3", 1)
        hline(asia[1], "#8d6e63", f"Asian LOW {asia[1]:.5g}", "2 3", 1)
    if h1_trend >= 0:
        hline(buy_trig, BULL, f"BUY trigger: M15 close &#8805; {buy_trig:.5g}", "", 2.5)
    if h1_trend <= 0:
        hline(sell_trig, BEAR, f"SELL trigger: M15 close &#8804; {sell_trig:.5g}", "", 2.5)
    # plan text
    trig = buy_trig if h1_trend == 1 else sell_trig
    lines = [f"1. Side: {side}.  Last close {c[-1]:.5g}; ATR(14) {a:.2g}.",
             f"2. Wait for an M15 candle to close {'above' if h1_trend == 1 else 'below'} {trig:.5g} (20-candle {'high + ' if h1_trend == 1 else 'low - '}0.3 ATR), with ATR rising (&#8805; 1.1x).",
             "3. Draw the fib on that push: 1 = where it started, 0 = its end. Entry = 0.618, valid 12 h.",
             "4. Need 2 of 3: killzone (blue times) / 0.618 inside an open FVG or OB (boxes) / a sweep of a liquidity line (purple/brown) in the last 20 candles.",
             "5. SL just beyond fib 1 (+0.1 ATR), TP 4 ATR. Risk 0.5% with 3 of 3, 0.25% with 2 of 3. The engine does all of this automatically."]
    for k, txt in enumerate(lines):
        out.append(f'<text x="{PAD}" y="{H - INFO + 24 + 22 * k}" font-size="13">{txt}</text>')
    out.append("</svg>")
    return "\n".join(out)


async def render(paths: list[Path]) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        b = await pw.chromium.launch()
        pg = await b.new_page(viewport={"width": W, "height": H})
        for f in paths:
            await pg.goto(f.resolve().as_uri())
            await pg.screenshot(path=str(f.with_suffix(".png")))
        await b.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/charts/plan_monday")
    ap.add_argument("--local-utc-offset", type=int, default=3)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for sym in SYMBOLS:
        m5 = load_m5(sym, (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(), None)
        m15, h1 = resample(m5, 900), resample(m5, 3600)
        m = {k: v[-300:] for k, v in m15.items()}
        ind = indicators.compute(m)
        f = out / f"{sym}_plan.svg"
        f.write_text(svg(sym, m, ind, int(trend(h1)[-1]), a.local_utc_offset), encoding="utf-8")
        files.append(f)
    asyncio.run(render(files))
    print("\n".join(str(f.with_suffix(".png")) for f in files))


if __name__ == "__main__":
    main()
