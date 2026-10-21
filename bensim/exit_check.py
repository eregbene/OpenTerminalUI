"""Would the in-trade watch (trade_watch RULES) have helped if it CLOSED trades? 3-month backtest, fib618 + Bennett live rules.

    python -m bensim.exit_check

Each backtest trade is re-walked bar by bar: at every check (5 min = live cadence, and 30 min) the H4/H1/M15 swing trend
is taken from completed bars only. A rule exits at the first check where it fires (M5 close, exit side of the spread,
commission kept). benefit = R with the rule - R the trade really made (+ = the rule helped).
Bennett partials / add-ons are ignored in the rule exit (whole position at the first-entry risk) -- small, conservative.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import bennett_strategy as bstrat
from .backtest import Exec, _spread, load_m5, resample, run_symbol
from .live import LIVE_SYMBOLS
from .strategy import trend
from .trade_watch import RULES
from .weekly_check import LIVE as FIB_LIVE

START, END = "2026-07-21", "2026-10-21"
CADENCE = {"5 min": 1, "30 min": 6}  # check every n M5 bars


def _tf_trend(m5: dict, sec: int):
    b = resample(m5, sec)
    return b["t"] + sec, trend(b)  # trend known once the bar has closed


def rescore(args: tuple) -> list[dict]:
    sym, trades = args
    if trades.empty:
        return []
    ex = Exec()
    m5 = load_m5(sym, (pd.Timestamp(START) - pd.Timedelta(days=90)).isoformat(), END)
    tfs = [_tf_trend(m5, s) for s in (14400, 3600, 900)]
    out = []
    for tr in trades.itertuples():
        d, k, xi = tr.dir, int(np.searchsorted(m5["t"], tr.t_entry - 300)), int(np.searchsorted(m5["t"], tr.t_exit - 300))
        risk = (tr.entry - tr.sl) * d
        row = {"symbol": sym, "book": tr.book, "r": tr.r}
        for cname, step in CADENCE.items():
            for rname, rule in RULES.items():
                r_rule = tr.r
                for j in range(k + step, xi, step):
                    now = int(m5["t"][j]) + 300
                    h4, h1, m15 = (int(tt[max(0, np.searchsorted(te, now, side="right") - 1)]) for te, tt in tfs)
                    if rule(d, h4, h1, m15):
                        px = m5["close"][j] + (0 if d == 1 else _spread(sym, now, ex))
                        r_rule = (px - tr.entry) * d / risk - tr.commission_r
                        break
                row[(cname, rname)] = r_rule - tr.r
        out.append(row)
    return out


def main() -> None:
    jobs = [(s, START, END, FIB_LIVE, Exec(), "imbalance") for s in LIVE_SYMBOLS] + \
           [(s, START, END, bstrat.LIVE, Exec(), "bennett") for s in bstrat.LIVE_SYMBOLS]
    with ProcessPoolExecutor(4) as pool:
        res = list(pool.map(run_symbol, jobs))
    by: dict[str, list] = {}
    for j, t in zip(jobs, res):
        if not t.empty:
            by.setdefault(j[0], []).append(t.assign(book="fib618" if j[5] == "imbalance" else "Bennett"))
    with ProcessPoolExecutor(4) as pool:
        rows = [r for part in pool.map(rescore, [(s, pd.concat(v)) for s, v in by.items()]) for r in part]
    df = pd.DataFrame(rows)
    print(f"{START} .. {END}: {len(df)} trades (fib618 {sum(df.book == 'fib618')}, Bennett {sum(df.book == 'Bennett')}), "
          f"as traded avg {df.r.mean():+.3f}R total {df.r.sum():+.1f}R")
    print("benefit = R with the exit rule - R as traded (+ = rule helped); 'fired' = trades where the rule closed early")
    for cname in CADENCE:
        print(f"\n check every {cname}")
        for rname in RULES:
            b = df[(cname, rname)]
            f = b[b != 0]
            t = b.mean() / b.std(ddof=1) * np.sqrt(len(b)) if b.std() > 0 else 0.0
            print(f"  {rname:16} fired {len(f):3}/{len(b)}  avg benefit {b.mean():+.3f}R/trade  total {b.sum():+6.1f}R "
                  f"(${b.sum() * 500:+,.0f})  helped {(f > 0).mean() if len(f) else 0:4.0%} of fired  t {t:+.1f}")


if __name__ == "__main__":
    main()
