"""Two clean FVG examples on the user's GBPUSD drop (4H, IC feed): candles 1-2-3 numbered, the gap shaded, and the user's own
daily 'imbalance' box (read off his screenshot: ~1.3361-1.3453 and ~1.3267-1.3348) dashed for comparison.
    python reports/round31/fvg_two_examples.py
"""
import sys
import time
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
OUT = Path(ROOT + "/reports/round31")
USER_BOXES = [(1.3361, 1.3453, "your daily box 1"), (1.3267, 1.3348, "your daily box 2")]


def main():
    from bensim import bridge
    r = pd.DataFrame(bridge.rates("GBPUSD", "H4", 0, 40)).reset_index(drop=True)
    gaps = [(i, r.high[i], r.low[i - 2]) for i in range(2, len(r)) if r.high[i] < r.low[i - 2] and r.low[i - 2] - r.high[i] >= 0.0010]
    big2 = sorted(sorted(gaps[-5:], key=lambda g: g[2] - g[1])[-2:])  # the 2 biggest of the last 5 gaps, in time order
    for n, (i, lo_, hi_) in enumerate(big2, 1):
        w = r.iloc[max(0, i - 12):].reset_index()
        fig, ax = plt.subplots(figsize=(12, 7))
        ax.set_facecolor("#6b6b6b")
        for k, c in w.iterrows():
            ax.vlines(k, c.low, c.high, color="#222", lw=0.9)
            ax.add_patch(Rectangle((k - 0.35, min(c.open, c.close)), 0.7, max(abs(c.close - c.open), 1e-6),
                                   fc="#fff" if c.close >= c.open else "#000", ec="#222"))
        x1 = int(w.index[w["index"] == i - 2][0])
        for j, lab in ((x1, "1"), (x1 + 1, "2"), (x1 + 2, "3")):
            c = w.iloc[j]
            ax.add_patch(Rectangle((j - 0.48, c.low), 0.96, c.high - c.low, fill=False, ec="#ffeb3b", lw=1.5, ls=":"))
            ax.text(j, c.high + 0.0006, lab, ha="center", fontsize=13, color="#ffeb3b", weight="bold")
        ax.add_patch(Rectangle((x1 - 0.5, lo_), len(w) - x1 + 3, hi_ - lo_, color="#ef5350", alpha=0.45))
        ax.text(x1 + 3, (lo_ + hi_) / 2, f"FVG {lo_:.5f} - {hi_:.5f} ({(hi_ - lo_) * 1e4:.0f} pips)\n= gap between candle 1 LOW and candle 3 HIGH",
                fontsize=10, color="#fff", va="center")
        ax.hlines(r.low[i - 2], x1 - 0.5, x1 + 2.5, color="#ffeb3b", lw=2)
        ax.hlines(r.high[i], x1 + 1.5, x1 + 2.5, color="#ffeb3b", lw=2)
        for a, b, lab in USER_BOXES:
            ax.add_patch(Rectangle((-0.5, a), len(w) + 3, b - a, fill=False, ec="#a5d6a7", lw=1.2, ls="--"))
            ax.text(len(w) + 2.4, b, lab, fontsize=8, color="#a5d6a7", va="top", ha="right")
        later = r.iloc[i + 1:]
        back = (later.high >= lo_).any()
        ax.set_xticks(range(0, len(w), 3))
        ax.set_xticklabels([time.strftime("%m-%d %H:%M", time.gmtime(int(t))) for t in w.time[::3]], fontsize=8)
        ax.set_xlim(-1, len(w) + 3)
        ax.set_title(f"Example {n}: GBPUSD 4H BEARISH FVG (candle 2 = the big down candle)  -  price back into it since: {'YES' if back else 'NOT YET'}",
                     fontsize=11)
        fig.tight_layout()
        fig.savefig(OUT / f"fvg_example_{n}.png", dpi=95)
        plt.close(fig)
        print(OUT / f"fvg_example_{n}.png", f"{lo_:.5f}-{hi_:.5f}")


if __name__ == "__main__":
    main()
