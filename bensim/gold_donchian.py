"""Gold "Stairway to Heaven" (r/algotrading 1unk44b, u/UniversalJS, user ask 2026-10-30) -- 3-month test.

    python -m bensim.gold_donchian [start] [end]     # default 2026-07-26 .. 2026-10-26 (DB end)

His post: Donchian-style breakout entry, single fixed trailing stop, one position at a time, fixed risk, no target.
He gives no channel length / timeframe / trail distance, so these are OURS, frozen 2026-10-30 before any result:
  * XAUUSD M5 (DB, bid) -> M15 bars; channel = highest high / lowest low of the previous 20 M15 bars
  * entry: an M15 close above the channel high -> BUY at that close (ask); below the low -> SELL (bid). All hours
  * stop: entry -/+ 2 x ATR(14, M15); after every M15 close it trails to close -/+ 2 x ATR (only tightens)
  * exit only on the stop (long on bid, short on ask; gap through -> the bar's open, + 0.1 pip slip). No target
  * one position at a time; a new signal only after the exit. R = P&L / initial risk
  * costs: backtest spread model, $7/lot commission. ponytail: overnight swap not modelled -- add if holds run days
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from .backtest import Exec, _spread, load_m5, resample
from .pattern_check import stat
from .strategy import atr, pip

SYM = "XAUUSD"
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
START = ARGS[0] if ARGS else "2026-07-26"
END = ARGS[1] if len(ARGS) > 1 else "2026-10-26"
COMM_PER_OZ = 7.0 / 100
N, K = 20, 2.0


def trades(m: dict, t0: int, ex: Exec) -> pd.DataFrame:
    a = atr(m)
    slip = ex.slippage_pips * pip(SYM)
    out, pos = [], None
    for j in range(N + 15, len(m["t"])):
        t = int(m["t"][j])
        sp = _spread(SYM, t, ex)
        if pos:  # stop check on this bar (stop fixed within the bar)
            d, e, sl, risk, t_in = pos
            o, hi, lo = m["open"][j] + (sp if d == -1 else 0), m["high"][j] + (sp if d == -1 else 0), m["low"][j] + (sp if d == -1 else 0)
            if (lo <= sl) if d == 1 else (hi >= sl):
                px = (min(o, sl) if d == 1 else max(o, sl)) - d * slip
                out.append({"t": t_in, "dir": d, "r": ((px - e) * d - COMM_PER_OZ) / risk, "hours": (t - t_in) / 3600})
                pos = None
            else:
                c = m["close"][j]
                pos = (d, e, max(sl, c - K * a[j]) if d == 1 else min(sl, c + K * a[j]), risk, t_in)
                continue
        if t < t0:
            continue
        c, hh, ll = m["close"][j], m["high"][j - N:j].max(), m["low"][j - N:j].min()
        d = 1 if c > hh else -1 if c < ll else 0
        if d:
            e = c + (sp if d == 1 else 0)
            sl = c - d * K * a[j]
            pos = (d, e, sl, abs(e - sl), t + 900)
    return pd.DataFrame(out)


def main() -> None:
    lo = (pd.Timestamp(START, tz="UTC") - pd.Timedelta(days=10)).isoformat()
    m = resample(load_m5(SYM, lo, END), 900)
    tr = trades(m, int(pd.Timestamp(START, tz="UTC").timestamp()), Exec())
    print(f"{SYM} Donchian {N} x M15 + {K} ATR trail, {START} .. {END} ($250/R)")
    print(f"  ALL    {stat(tr.r)}  = ${tr.r.sum() * 250:+,.0f}   avg win {tr.r[tr.r > 0].mean():+.2f}R  "
          f"avg loss {tr.r[tr.r <= 0].mean():+.2f}R  median hold {tr.hours.median():.1f}h")
    for name, g in tr.groupby("dir"):
        print(f"  {'long ' if name == 1 else 'short'}  {stat(g.r)}")
    eq = tr.r.cumsum()
    print(f"  max drawdown {(eq - eq.cummax()).min():+.1f}R   by month R {tr.groupby(pd.to_datetime(tr.t, unit='s').dt.month).r.sum().round(1).to_dict()}")


if __name__ == "__main__":
    main()
