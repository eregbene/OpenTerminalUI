"""Do fib618 / Bennett trades entered AGAINST the H4 (or H1/M15) swing trend do worse? (live losses 2026-10-22 hint)

    python -m bensim.entry_filter_check [start]      # default 3 months; 2021-10-21 = 5 years

Trend = strategy.trend on completed bars at the entry time (same as trade_watch). Split R by: H4 with/against,
number of H4/H1/M15 agreeing with the trade (0-3). A filter only counts if it holds in both halves of the period.
"""
from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import bennett_strategy as bstrat
from .backtest import Exec, load_m5, run_symbol
from .exit_check import _tf_trend
from .live import LIVE_SYMBOLS
from .weekly_check import LIVE as FIB_LIVE

START, END = (sys.argv[1] if len(sys.argv) > 1 else "2026-07-21"), "2026-10-22"


def tag(args) -> pd.DataFrame:
    sym, tr = args
    m5 = load_m5(sym, (pd.Timestamp(START) - pd.Timedelta(days=90)).isoformat(), END)
    tfs = [_tf_trend(m5, s) for s in (14400, 3600, 900)]
    at = lambda tf, now: int(tf[1][max(0, np.searchsorted(tf[0], now, side="right") - 1)])  # noqa: E731
    tr = tr.copy()
    tr[["h4", "h1", "m15"]] = [[at(tf, int(t)) for tf in tfs] for t in tr.t_entry]
    tr["agree"] = (tr[["h4", "h1", "m15"]].to_numpy() == tr.dir.to_numpy()[:, None]).sum(1)
    tr["h4_with"] = tr.h4 == tr.dir
    return tr


def line(name: str, r: pd.Series) -> str:
    t = r.mean() / r.std() * np.sqrt(len(r)) if len(r) > 2 and r.std() > 0 else 0.0
    return f"  {name:22} n {len(r):5}  avg {r.mean():+.3f}R  t {t:+.1f}  total {r.sum():+7.1f}R"


def main() -> None:
    yrs = pd.date_range(START, END, freq="365D").strftime("%Y-%m-%d").tolist() + [END]
    jobs = [(s, a, b, p, Exec(), k) for s_list, p, k in ((LIVE_SYMBOLS, FIB_LIVE, "imbalance"), (bstrat.LIVE_SYMBOLS, bstrat.LIVE, "bennett"))
            for s in s_list for a, b in zip(yrs[:-1], yrs[1:])]
    with ProcessPoolExecutor(4) as pool:
        res = list(pool.map(run_symbol, jobs))
    by: dict[str, list] = {}
    for j, t in zip(jobs, res):
        if not t.empty:
            by.setdefault(j[0], []).append(t.assign(book="fib618" if j[5] == "imbalance" else "Bennett"))
    with ProcessPoolExecutor(4) as pool:
        tr = pd.concat(list(pool.map(tag, [(s, pd.concat(v)) for s, v in by.items()])), ignore_index=True)
    mid = tr.t_entry.median()
    print(f"{START} .. {END}: {len(tr)} trades (fib618 {sum(tr.book == 'fib618')}, Bennett {sum(tr.book == 'Bennett')})")
    for lab, g in (("ALL", tr), ("1st half", tr[tr.t_entry < mid]), ("2nd half", tr[tr.t_entry >= mid])):
        print(f"\n {lab}")
        print(line("all trades", g.r))
        print(line("H4 WITH the trade", g.r[g.h4_with]))
        print(line("H4 AGAINST", g.r[~g.h4_with]))
        for k in range(4):
            print(line(f"{k} of H4/H1/M15 agree", g.r[g.agree == k]))


if __name__ == "__main__":
    main()
