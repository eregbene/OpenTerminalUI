"""Show the FVG definition on the user's own GBPUSD chart (IC feed via the MT5 bridge): 1D, 4H, 1H.
FVG (fair value gap) = 3 candles where candle 1's wick and candle 3's wick do NOT overlap; the empty space between them is
the FVG (bullish: candle 3 low > candle 1 high; bearish: candle 3 high < candle 1 low). Candle 2 = the displacement candle.
Also outlines the big one-sided candles (body >= 1.5 x the 20-bar average body) = what the user drew as "imbalance".
    python reports/round31/fvg_definition_charts.py
"""
import sys
import time
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
OUT = Path(ROOT + "/reports/round31")
SYM = "GBPUSD"


def draw(tf, n, fname, title):
    from bensim import bridge
    r = pd.DataFrame(bridge.rates(SYM, tf, 0, n))
    fig, ax = plt.subplots(figsize=(15, 7.5))
    ax.set_facecolor("#6b6b6b")
    avg_body = (r.close - r.open).abs().rolling(20, min_periods=5).mean()
    for k, c in r.iterrows():
        up = c.close >= c.open
        col = "#ffffff" if up else "#000000"
        ax.vlines(k, c.low, c.high, color="#222", lw=0.8)
        ax.add_patch(Rectangle((k - 0.35, min(c.open, c.close)), 0.7, max(abs(c.close - c.open), 1e-6), fc=col, ec="#222", lw=0.6))
        if k > 3 and abs(c.close - c.open) >= 1.5 * avg_body[k - 1]:
            ax.add_patch(Rectangle((k - 0.5, min(c.open, c.close)), 1.0, abs(c.close - c.open), fill=False, ec="#ffeb3b", lw=1.6))
    nb = 0
    for i in range(2, len(r)):
        a, c = r.iloc[i - 2], r.iloc[i]
        for kind, lo_, hi_, col in (("bull", a.high, c.low, "#42a5f5"), ("bear", c.high, a.low, "#ef5350")):
            if hi_ <= lo_:
                continue
            # filled later? (price traded through the whole gap)
            later = r.iloc[i + 1:]
            filled = (later.low <= lo_).any() if kind == "bull" else (later.high >= hi_).any()
            ax.add_patch(Rectangle((i - 2 - 0.45, lo_), len(r) - (i - 2), hi_ - lo_, color=col, alpha=0.35 if not filled else 0.18))
            nb += 1
            ax.text(i - 1, hi_ if kind == "bull" else lo_, f" {kind} FVG {lo_:.5f}-{hi_:.5f} ({(hi_ - lo_) * 1e4:.0f}p){' filled' if filled else ' OPEN'}",
                    fontsize=7.5, color="#0d47a1" if kind == "bull" else "#b71c1c", va="bottom" if kind == "bull" else "top")
            for j in (i - 2, i):  # mark candle 1 and candle 3 wicks
                ax.plot(j, a.high if (kind == "bull" and j == i - 2) else c.low if kind == "bull" else (c.high if j == i else a.low), "_",
                        color=col, ms=14, mew=2)
    step = max(1, len(r) // 12)
    ax.set_xticks(range(0, len(r), step))
    ax.set_xticklabels([time.strftime("%m-%d %H:%M" if tf != "D1" else "%m-%d", time.gmtime(int(t))) for t in r.time[::step]], fontsize=8)
    ax.set_xlim(-1, len(r) + 2)
    ax.set_title(f"{SYM} {tf} (IC) - {title}\nblue = bullish FVG, red = bearish FVG (strong = still open, faint = already filled); "
                 f"yellow outline = big one-sided candle (body >= 1.5x average)", fontsize=10)
    fig.tight_layout()
    fig.savefig(OUT / fname, dpi=95)
    plt.close(fig)
    print(OUT / fname, f"{nb} FVGs")


if __name__ == "__main__":
    draw("D1", 45, "fvg_def_1D.png", "your daily chart: where the real 3-candle gaps are")
    draw("H4", 70, "fvg_def_4H.png", "the same drop on 4H (last ~12 days)")
    draw("H1", 90, "fvg_def_1H.png", "the last ~4 days on 1H")
