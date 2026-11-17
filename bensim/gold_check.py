"""The 3 live strategies on GOLD (XAUUSD), rules frozen as they trade live (2026-10-19).

    python -m bensim.gold_check            # 3 months + 5 years (MT5 M5 history starts 2021-10)

fib618 = live config, Bennett = bstrat.LIVE (not traded on gold live), flow morning = EURUSD rule copied to gold
(sell 09:00 UTC, close 12:00 UTC, real spread). The flow ML model is FX-only (yield gaps) -> no gold version.
$ = R x $500 (0.5% of $100k per trade, no compounding).
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import bennett_strategy as bstrat
from .backtest import Exec, _spread, load_m5, run_symbol
from .weekly_check import LIVE as FIB_LIVE

SYM, END = "XAUUSD", "2026-10-19"
PERIODS = {"3 months": "2026-07-19", "5 years": "2021-10-04"}


def morning(start: str) -> pd.DataFrame:
    m, ex = load_m5(SYM, start, END), Exec()
    t = pd.to_datetime(m["t"], unit="s")
    df = pd.DataFrame({"t": t, "c": m["close"], "s": [_spread(SYM, int(x), ex) for x in m["t"]]})
    df = df[df.t.dt.dayofweek < 5].assign(d=lambda x: x.t.dt.normalize())
    a = df[(df.t.dt.hour == 8) & (df.t.dt.minute == 55)].set_index("d")
    b = df[(df.t.dt.hour == 11) & (df.t.dt.minute == 55)].set_index("d")
    j = a.join(b, lsuffix="_in", rsuffix="_out", how="inner")
    move = (j.c_in - j.c_out) - (j.s_in + j.s_out) / 2  # short, net of spread
    risk = 0.0068 * j.c_in  # the morning book's disaster stop (68 bp) as 1R
    return pd.DataFrame({"t": j.index, "r": (move / risk).to_numpy()})


def jobs(start: str):
    yrs = pd.date_range(start, END, freq="365D").strftime("%Y-%m-%d").tolist() + [END]
    for name, p, kind in (("fib618", FIB_LIVE, "imbalance"), ("Bennett", bstrat.LIVE, "bennett")):
        for a, b in zip(yrs[:-1], yrs[1:]):
            yield name, (SYM, a, b, p, Exec(), kind)


def line(name: str, r: pd.Series, t: pd.Series) -> str:
    if r.empty:
        return f"  {name:16} no trades"
    tstat = r.mean() / r.std() * np.sqrt(len(r)) if len(r) > 1 and r.std() > 0 else 0
    eq = (r * 500).cumsum()
    dd = (eq - eq.cummax().clip(lower=0)).min()
    yr = r.groupby(pd.to_datetime(t).dt.year).sum().round(1).to_dict()
    return (f"  {name:16} n={len(r):4}  win {(r > 0).mean() * 100:3.0f}%  avg {r.mean():+.3f}R  t {tstat:+.1f}  "
            f"total {r.sum():+7.1f}R = ${r.sum() * 500:+9,.0f}  maxDD ${dd:,.0f}  by year R {yr}")


def main() -> None:
    for label, start in PERIODS.items():
        js = list(jobs(start))
        with ProcessPoolExecutor(4) as pool:
            res = list(pool.map(run_symbol, [j for _, j in js]))
        print(f"\n{label}: {start} .. {END}  ({SYM})")
        for name in ("fib618", "Bennett"):
            fr = [x for (n, _), x in zip(js, res) if n == name and not x.empty]
            df = pd.concat(fr) if fr else pd.DataFrame({"r": [], "t_entry": []})
            print(line(name, df.r, pd.to_datetime(df.t_entry, unit="s")))
        mo = morning(start)
        print(line("morning short", mo.r, mo.t))


if __name__ == "__main__":
    main()
