"""Two examples of the user's pattern for him to confirm, same rules as fill_pattern.py (4H, GBPUSD, IC data):
  1. a DROP: imbalance (big one-sided down candle) above, liquidity (lows) below -> price takes the lows -> fills back UP
  2. a SPIKE UP: imbalance below, liquidity (highs) above -> price takes the highs -> fills back DOWN
    python reports/round32/examples.py [SYMBOL] [TF]
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
OUT = Path(ROOT + "/reports/round32")
SYM = sys.argv[1] if len(sys.argv) > 1 else "GBPUSD"
TF = sys.argv[2] if len(sys.argv) > 2 else "H4"
SEC = {"H1": 3600, "H4": 14400, "D1": 86400}[TF]


def find(b, up):
    """Latest imbalance where the MAJOR liquidity was taken and then the 50% of the imbalance filled (within 100 bars)."""
    O, H, L, C = b.open.to_numpy(), b.high.to_numpy(), b.low.to_numpy(), b.close.to_numpy()
    body = np.abs(C - O)
    avg = pd.Series(body).rolling(20).mean().shift(1).to_numpy()
    for i in range(len(b) - 10, 130, -1):
        big = body[i] >= 1.5 * avg[i] and body[i] >= 0.6 * (H[i] - L[i])
        if not big or (C[i] > O[i]) != up:
            continue
        ce = (O[i] + C[i]) / 2
        if up:   # spike up: liquidity = highest high of the last 120 bars above the candle high
            liq = H[i - 120:i].max()
            if liq <= H[i]:
                continue
            k = next((k for k in range(i + 1, min(i + 101, len(b))) if H[k] >= liq), None)
            f = next((f for f in range(k, min(i + 101, len(b))) if L[f] <= ce), None) if k else None
        else:    # drop: liquidity = lowest low of the last 120 bars below the candle low
            liq = L[i - 120:i].min()
            if liq >= L[i]:
                continue
            k = next((k for k in range(i + 1, min(i + 101, len(b))) if L[k] <= liq), None)
            f = next((f for f in range(k, min(i + 101, len(b))) if H[f] >= ce), None) if k else None
        if k and f and f > k + 2 and not any((L[j] <= ce if up else H[j] >= ce) for j in range(i + 1, k)):
            return i, liq, ce, k, f
    return None


def draw(n, b, i, liq, ce, k, f, up):
    lo_i, hi_i = max(0, i - 60), min(len(b), f + 12)
    w = b.iloc[lo_i:hi_i].reset_index(drop=True)
    x = lambda j: j - lo_i  # noqa: E731
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.set_facecolor("#6b6b6b")
    for j, c in w.iterrows():
        ax.vlines(j, c.low, c.high, color="#222", lw=0.8)
        ax.add_patch(Rectangle((j - 0.35, min(c.open, c.close)), 0.7, max(abs(c.close - c.open), 1e-6),
                               fc="#fff" if c.close >= c.open else "#000", ec="#222", lw=0.5))
    o, c_ = b.open[i], b.close[i]
    ax.add_patch(Rectangle((x(i) - 0.5, min(o, c_)), hi_i - i + 2, abs(o - c_), color="#66bb6a", alpha=0.45))
    ax.text(x(i) + 2, max(o, c_) if not up else min(o, c_), "  imbalance (big one-sided candle body)", color="#1b5e20", fontsize=10,
            va="bottom" if not up else "top", weight="bold")
    ax.axhline(ce, color="#1b5e20", lw=1, ls=":")
    ax.text(len(w) + 1, ce, " 50% of the imbalance", fontsize=8, color="#1b5e20", va="center")
    src = int(np.argmax(b.high[i - 120:i].to_numpy())) + i - 120 if up else int(np.argmin(b.low[i - 120:i].to_numpy())) + i - 120
    ax.add_patch(Rectangle((max(x(src), 0) - 0.5, liq - (0.0008 if not up else 0)), hi_i - max(src, lo_i) + 2, 0.0008, color="#66bb6a", alpha=0.45))
    ax.axhline(liq, color="#b71c1c", lw=1.2, ls="--")
    ax.text(0, liq, f" liquidity {liq:.5f} ({'highs above' if up else 'lows below'}, lowest/highest of the last 120 candles)", fontsize=9,
            color="#b71c1c", va="bottom" if up else "top")
    ax.annotate("1. liquidity taken", (x(k), liq), xytext=(x(k) - 12, liq + (0.0035 if up else -0.0035)), fontsize=10,
                arrowprops=dict(arrowstyle="->", color="#ffeb3b"), color="#ffeb3b")
    ax.annotate("2. fills back into the imbalance", (x(f), ce), xytext=(x(f) + 2, ce + (-0.004 if up else 0.004)), fontsize=10,
                arrowprops=dict(arrowstyle="->", color="#ffeb3b"), color="#ffeb3b")
    step = max(1, len(w) // 12)
    ax.set_xticks(range(0, len(w), step))
    ax.set_xticklabels([time.strftime("%m-%d %H:%M", time.gmtime(int(tt))) for tt in w.t[::step]], fontsize=8)
    ax.set_xlim(-1, len(w) + 8)
    ax.set_title(f"Example {n}: {SYM} {TF} - {'SPIKE UP: imbalance below, liquidity (highs) above -> highs taken -> fills back DOWN' if up else 'DROP: imbalance above, liquidity (lows) below -> lows taken -> fills back UP'}",
                 fontsize=11)
    fig.tight_layout()
    p = OUT / f"example_{n}_{'up' if up else 'down'}.png"
    fig.savefig(p, dpi=95)
    plt.close(fig)
    return p


def main():
    from bensim.backtest import load_m5, resample
    raw = load_m5(SYM, "2025-06-01", "2026-12-31")
    b = pd.DataFrame(resample(raw, SEC))
    for n, up in ((1, False), (2, True)):
        hit = find(b, up)
        if hit is None:
            print("no example found", up)
            continue
        i, liq, ce, k, f = hit
        p = draw(n, b, i, liq, ce, k, f, up)
        print(p, "| imbalance candle", time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(b.t[i]))), f"body {b.open[i]:.5f}->{b.close[i]:.5f}",
              f"| liquidity {liq:.5f} taken", time.strftime("%m-%d %H:%M", time.gmtime(int(b.t[k]))),
              "| 50% fill", time.strftime("%m-%d %H:%M", time.gmtime(int(b.t[f]))))


if __name__ == "__main__":
    main()
