"""JeaFx structure as a FILTER on the live strategies (2026-10-19; rules frozen before running).

    python -m bensim.structure_filter --start 2026-07-19 --end 2026-10-18

For every trade of fib618 (live config), Bennett (LIVE) and the EURUSD morning short (flow book), read sflow's H1
structure at the last H1 close before the entry:
  agree     keep only trades in the direction of the H1 structure phase (close-only BOS, internal swings ignored)
  no_consol drop trades taken while H1 is in "no man's land" (both live levels only wicked since the last BOS)
Prints base vs kept vs dropped. 3 months = a first look; nothing is adopted without a 5-year check.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import bennett_strategy as bstrat
from . import sflow
from .backtest import Exec, load_m5, resample, run_symbol
from .combo import morning
from .weekly_check import LIVE as FIB_LIVE, replay


def h1_state(sym: str, start: str, end: str) -> tuple[np.ndarray, np.ndarray]:
    """(H1 close times, (phase, consol) per bar)."""
    hb = resample(load_m5(sym, (pd.Timestamp(start) - pd.Timedelta(days=60)).strftime("%Y-%m-%d"), end), 3600)
    st: list = []
    sflow.structure(hb, state=st)
    return hb["t"] + 3600, np.array(st)


def tag(trades: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """trades: symbol, dir, t (entry, epoch s), r -> adds phase, consol (state of the last H1 bar closed by t)."""
    out = []
    for sym, g in trades.groupby("symbol"):
        tc, st = h1_state(sym, start, end)
        j = np.searchsorted(tc, g.t.to_numpy(), side="right") - 1
        out.append(g.assign(phase=st[j, 0], consol=st[j, 1].astype(bool)))
    return pd.concat(out)


def report(name: str, t: pd.DataFrame) -> None:
    def s(x: pd.DataFrame) -> str:
        return f"n={len(x):3} avg {x.r.mean():+.3f} total {x.r.sum():+6.1f}" if len(x) else "n=  0"
    agree = t.phase == t.dir
    print(f"{name:22} base {s(t)} | agree: kept {s(t[agree])}, dropped {s(t[~agree])} | "
          f"no_consol: kept {s(t[~t.consol])}, dropped {s(t[t.consol])}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-07-19")
    ap.add_argument("--end", default="2026-10-18")
    a = ap.parse_args()
    fib = replay(FIB_LIVE, a.start, a.end)
    fib = fib.assign(t=fib.t_entry)[["symbol", "dir", "t", "r"]]
    with ProcessPoolExecutor(4) as pool:
        bn = pd.concat([x for x in pool.map(run_symbol, [(s, a.start, a.end, bstrat.LIVE, Exec(), "bennett")
                                                         for s in bstrat.LIVE_SYMBOLS]) if not x.empty])
    bn = bn.assign(t=bn.t_entry)[["symbol", "dir", "t", "r"]]
    am = morning()
    am = am[(am.day >= a.start) & (am.day < a.end)]
    am = pd.DataFrame({"symbol": "EURUSD", "dir": -1, "t": (am.day + pd.Timedelta(hours=9)).astype("int64") // 10**9,
                       "r": am.bp.to_numpy()})  # bp, not R: compare kept vs dropped only
    print(f"{a.start} .. {a.end}; H1 structure from sflow (close-only BOS, >=1 ATR pull-backs); morning short in bp")
    for name, t in (("fib618 (live config)", fib), ("Bennett (LIVE)", bn), ("EURUSD morning (bp)", am)):
        report(name, tag(t, a.start, a.end))


if __name__ == "__main__":
    main()
