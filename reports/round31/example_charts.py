"""3 example BUY trades of the first-table rule drawn on the zone's own timeframe, so the user can check it is what he means:
the 3 candles that made the imbalance, the gap, the liquidity, the Fibonacci 0.5 (discount below), entry, stop, target, exit.
    python reports/round31/example_charts.py
"""
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + "/reports/round31")
OUT = Path(ROOT + "/reports/round31")
EXAMPLES = [("USDJPY", "H4", "2026-09-14 15:40", "WITH the new filters (BOS, liquidity far, discount, London/NY) - winner"),
            ("XAUUSD", "H4", "2026-09-04 12:30", "WITH the new filters - loser"),
            ("USDCAD", "H4", "2026-09-17 06:45", "WITHOUT: the new filters would SKIP it (zone in premium) - it won anyway")]


def main():
    from two_month_check import TFS, bars_tf, rebuild
    from bensim.backtest import load_m5
    for n, (sym, tf, when, label) in enumerate(EXAMPLES, 1):
        raw = load_m5(sym, "2026-01-01", "2026-12-31")
        tr = pd.DataFrame(rebuild(sym, 1, raw))
        r = tr[(tr.tf == tf) & (tr.t_touch == pd.Timestamp(when, tz="UTC").timestamp())].iloc[0]
        sec = TFS[tf]
        b = bars_tf(raw, sec)
        i = int(np.flatnonzero(b.t.to_numpy() + sec == r.created)[0])
        last = int(np.searchsorted(b.t.to_numpy(), r.t_exit)) + 6
        w = b.iloc[max(0, i - 45):min(len(b), last + 1)].reset_index()
        fig, ax = plt.subplots(figsize=(14, 7))
        for k, c in w.iterrows():
            col = "#26a69a" if c.close >= c.open else "#ef5350"
            ax.vlines(k, c.low, c.high, color=col, lw=1)
            ax.add_patch(Rectangle((k - 0.35, min(c.open, c.close)), 0.7, max(abs(c.close - c.open), 1e-9), color=col))
        xi = lambda bar: int(np.flatnonzero(w["index"].to_numpy() == bar)[0])  # noqa: E731
        # the 3 candles of the imbalance + the gap
        for bar in (i - 2, i - 1, i):
            ax.add_patch(Rectangle((xi(bar) - 0.45, b.low[bar]), 0.9, b.high[bar] - b.low[bar], fill=False, ec="#1565c0", lw=1.2, ls=":"))
        ax.add_patch(Rectangle((xi(i - 2) - 0.45, r.bot), len(w) - xi(i - 2), r.top - r.bot, color="#42a5f5", alpha=0.25))
        ax.text(xi(i - 1), r.top, " IMBALANCE (FVG)", color="#0d47a1", fontsize=9, va="bottom")
        # liquidity (lowest low of the 5 bars before the gap) and the Fibonacci range of the last 50 bars
        liq_bar = (i - 6) + int(np.argmin(b.low[i - 6:i - 1].to_numpy()))
        ax.plot(xi(liq_bar), r.liq, "v", color="#6a1b9a", ms=10)
        rlo, rhi = b.low[i - 50:i + 1].min(), b.high[i - 50:i + 1].max()
        ax.axhspan(rlo, (rlo + rhi) / 2, color="#a5d6a7", alpha=0.12)
        ax.axhline((rlo + rhi) / 2, color="#999", lw=0.8)
        ax.text(0, (rlo + rhi) / 2, " Fib 0.5 (below = discount)", fontsize=8, color="#666", va="bottom")
        ex = len(w) - 1
        for y, lab, col in ((r.tp, f"TP 1:3  {r.tp:.5g}", "#1565c0"), (r.top, f"ENTRY (zone edge)  {r.top:.5g}", "#000"),
                            (r.liq, f"liquidity  {r.liq:.5g}", "#6a1b9a"), (r.sl, f"SL (liquidity - 5 pips)  {r.sl:.5g}", "#c62828")):
            ax.hlines(y, xi(i), ex, color=col, lw=1.3, ls="--")
            ax.text(ex, y, " " + lab, color=col, fontsize=9, va="center")
        te = int(np.searchsorted(w.t.to_numpy() + sec, r.t_touch, "right"))
        tx = int(np.searchsorted(w.t.to_numpy() + sec, r.t_exit, "right"))
        ax.plot(te, r.top, "^", color="#000", ms=12)
        ax.annotate("BUY here", (te, r.top), xytext=(te - 4, r.top - 0.25 * (r.top - r.sl)), fontsize=9, arrowprops=dict(arrowstyle="->"))
        ax.plot(tx, r.tp if r.R > 2.5 else r.sl if r.R < -0.9 else np.nan, "X", color="#ff6f00", ms=12)
        ax.set_xlim(-1, ex + 12)
        ax.set_xticks(range(0, len(w), 8))
        ax.set_xticklabels([pd.to_datetime(w.t[k], unit="s").strftime("%m-%d %H:%M") for k in range(0, len(w), 8)], fontsize=8)
        ax.set_title(f"{sym} {tf} BUY  {pd.to_datetime(r.t_touch, unit='s'):%Y-%m-%d %H:%M} UTC  ->  {r.R:+.2f}R   |   {label}\n"
                     f"BOS {r.bos}, liquidity {r.liq_atr:.1f} ATR under the zone, Fibonacci position {r.fib_pos:.2f}", fontsize=10)
        fig.tight_layout()
        p = OUT / f"example_{n}.png"
        fig.savefig(p, dpi=95)
        plt.close(fig)
        print(p)


if __name__ == "__main__":
    main()
