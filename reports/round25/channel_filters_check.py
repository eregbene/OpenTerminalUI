"""Round 25c (2026-11-09): the 4 filters SEEN in the 2-year channel trades (channel_wins.log), checked on the UNTOUCHED
2021-10-12 .. 2024-11-06 trades, exactly as written before this run:
  F1 buys only | F2 skip 13-17 UTC entries | F3 skip Thursdays | F4 skip the 1st attempt of a same-pair same-direction
  run (entries < 3 days apart) | ALL = F1+F2+F3+F4
PASS for a filter = on the untouched period its R/trade beats the unfiltered R/trade by >= +0.10R AND its total R > 0.
Engine R (bennett_channel own stop), $ at $500 risk and fixed 1/5/7 lots.
    python reports/round25/channel_filters_check.py
"""
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "XAUUSD", "USDJPY", "USDCHF", "USDCAD"]
OUT = "D:/Devops/trading/OpenTerminalUI/reports/round25/"


def job(a):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    from bensim import bennett_channel as bc
    sym, s, e = a
    return [dict(x, sym=sym) for x in bc.run(sym, s, e)]


def pipval(sym, price):
    return 1000 / price if sym == "USDJPY" else 10 / price if sym in ("USDCAD", "USDCHF") else 10.0


def filters(c: pd.DataFrame) -> dict:
    c = c.sort_values("time").copy()
    run = c.groupby(["sym", "dir"]).time.transform(lambda s: (s.diff().dt.total_seconds().fillna(1e9) > 3 * 86400).cumsum())
    c["n"] = c.groupby([c.sym, c.dir, run]).cumcount() + 1
    f1, f2, f3, f4 = c.dir == 1, ~c.time.dt.hour.between(13, 17), c.time.dt.dayofweek != 3, c.n > 1
    return {"none": c, "F1 buys only": c[f1], "F2 skip 13-17 UTC": c[f2], "F3 skip Thursday": c[f3],
            "F4 skip 1st attempt": c[f4], "ALL four": c[f1 & f2 & f3 & f4]}


if __name__ == "__main__":
    from bensim.strategy import pip
    with ProcessPoolExecutor(3) as pool:
        rows = sum(pool.map(job, [(s, "2021-10-12", "2024-11-07") for s in SYMS]), [])
    U = pd.DataFrame(rows)
    U.to_csv(OUT + "channel_untouched_trades.csv", index=False)
    T = pd.read_csv(OUT + "two_year_trades.csv")
    T = T[T.strategy == "channel CHANNEL"]
    for name, c in (("2-YEAR (where the filters were found)", T), ("UNTOUCHED 2021-10 .. 2024-11 (the real test)", U)):
        c = c.copy()
        c["time"] = pd.to_datetime(c.t_entry, unit="s")
        c["usd1"] = c.r * (c.entry - c.sl).abs() / c.sym.map(pip) * [pipval(s, e) for s, e in zip(c.sym, c.entry)]
        base = c.r.mean()
        print(f"\n===== {name}")
        for k, g in filters(c).items():
            t = g.r.mean() / g.r.std() * np.sqrt(len(g)) if len(g) > 1 else 0
            ok = k != "none" and g.r.mean() - base >= 0.10 and g.r.sum() > 0
            u = g.usd1.sum()
            print(f"  {k:20} {len(g):5} tr win {np.mean(g.r > 0):4.0%} {g.r.mean():+.3f}R t {t:+.2f} total {g.r.sum():+7.1f}R | "
                  f"${g.r.sum() * 500:+9,.0f} @$500 | 1/5/7 lots ${u:+9,.0f} / ${5 * u:+10,.0f} / ${7 * u:+10,.0f}"
                  f"{'  PASS' if ok and 'UNTOUCHED' in name else ''}")
