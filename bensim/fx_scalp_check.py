"""Forex scalping check (user ask 2026-10-31): the VWAP scalper + our existing strategies on all 6 FX pairs, 3 months.

    python -m bensim.fx_scalp_check

Rules frozen 2026-10-31 BEFORE any run (nothing re-tuned):
  1. VWAP +/-2 sigma mean reversion exactly as bensim.scalp_check (M5, ADX<25, target VWAP, stop 1.5 ATR, 2h time
     stop, bar spread + $7/lot), on EURUSD GBPUSD USDJPY AUDUSD USDCHF USDCAD -- MT5 bridge bars (~2026-07-27..10-30)
  2. existing books through backtest.run_symbol with their live/default params (fib618 live, imbalance, enigma,
     Bennett live), default Exec costs, same 6 pairs, 2026-07-26..2026-10-26 (DB end). Per book: all trades, and the
     scalp-like part (held <= 60 min) vs longer holds -- look-only split, the books are not changed.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import scalp_check
from .backtest import Exec, run_symbol
from .pattern_check import stat
from .tm_check import STRATS

PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD"]
START, END = "2026-07-26", "2026-10-26"


def main() -> None:
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)  # live DEMO beats research CPU; children inherit
    except Exception:
        pass
    print("1. VWAP +/-2 sigma scalper (M5), all FX pairs")
    allr = []
    for sym in PAIRS:
        b = scalp_check.rates(sym, "M5", 20000)
        r = pd.Series(scalp_check.vwap_trades(sym, b), dtype=float)
        allr.append(r)
        print(f"  {sym:7} {stat(r)}")
    print(f"  {'ALL':7} {stat(pd.concat(allr, ignore_index=True))}")

    print(f"\n2. existing strategies, {START} .. {END}, default costs")
    jobs = [(s, START, END, p, Exec(), kind) for p, kind in STRATS.values() for s in PAIRS]
    names = [n for n in STRATS for _ in PAIRS]
    with ProcessPoolExecutor(3) as pool:
        res = list(pool.map(run_symbol, jobs))
    tr = pd.concat([t.assign(strategy=n) for n, t in zip(names, res) if not t.empty], ignore_index=True)
    tr["hold_min"] = (tr.t_exit - tr.t_entry) / 60
    tr.to_csv("logs/fx_scalp_check.csv", index=False)
    for name, g in [("ALL", tr)] + list(tr.groupby("strategy")):
        s = g.hold_min <= 60
        print(f"  {name:9} {stat(g.r)}  median hold {g.hold_min.median():5.0f} min")
        print(f"  {'':9}   held <=60 min: {stat(g.r[s])}   |   longer: {stat(g.r[~s])}")
    print("\n  per pair (all strategies): " + "  ".join(f"{p} {g.r.sum():+.1f}R" for p, g in tr.groupby("symbol")))


if __name__ == "__main__":
    main()
