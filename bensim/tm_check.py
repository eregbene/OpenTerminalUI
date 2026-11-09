"""Does the time machine help? (user ask 2026-10-29) -- 3 months, 4 strategies, 5 pairs + gold.

    python -m bensim.tm_check [start] [end] [--fx]   # default 2026-07-26 .. 2026-10-26 (DB end); --fx = no gold
Both time-machine modes are computed per trade: bensim (direction removed, tm_*) and repo (direction kept, rp_*).

Rules (frozen 2026-10-29 before any result was seen):
  * trades: backtest replays of fib618 (live config), imbalance book, enigma (H4 FVG book), Bennett (live config) on
    EURUSD GBPUSD USDJPY USDCHF USDCAD XAUUSD -- every strategy on all 6, default Exec costs
  * at each trade's entry: timemachine.run(sym, dir, entry, sl, tp, t_entry, n=2000, seed 19) -- pool = 26 weeks
    BEFORE the entry only (no lookahead)
  * the manager rule under test: REFUSE when target-first < 10%. Compare refused vs kept (avg R, total R)
  * also look-only: R by target-first bucket and by stop-inside-first-hour bucket
  * the rule is worth keeping only if refused trades are clearly worse than kept (refused total < 0 and avg below kept);
    3 months is small -- a pass here means "check 5y", not "switch on"
"""
from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import bennett_strategy as bstrat
from . import books, timemachine
from .backtest import Exec, run_symbol
from .pattern_check import stat
from .weekly_check import LIVE as FIB_LIVE

ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
START = ARGS[0] if ARGS else "2026-07-26"
END = ARGS[1] if len(ARGS) > 1 else "2026-10-26"
SYMS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD"] + ([] if "--fx" in sys.argv else ["XAUUSD"])
STRATS = {"fib618": (FIB_LIVE, "imbalance"), "imbalance": (books.IMB, "imbalance"),
          "enigma": (books.ENIGMA, "htf_fvg"), "Bennett": (bstrat.LIVE, "bennett")}


def tm(x: dict) -> dict:
    r = timemachine.run(x["symbol"], int(x["dir"]), x["entry"], x["sl"], x["tp"], int(x["t_entry"]), n=2000)
    q = timemachine.run(x["symbol"], int(x["dir"]), x["entry"], x["sl"], x["tp"], int(x["t_entry"]), n=2000, repo=True)
    return {"tm_target": r["target"], "tm_stop": r["stop"], "tm_open": r["open"], "tm_stop_1h": r["stop_1h"],
            "rp_target": q["target"], "rp_avg_r": q["avg_r"]}


def main() -> None:
    try:
        import psutil
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)  # live DEMO beats research CPU; children inherit
    except Exception:
        pass
    jobs = [(s, START, END, p, Exec(), kind) for p, kind in STRATS.values() for s in SYMS]
    names = [n for n in STRATS for _ in SYMS]
    with ProcessPoolExecutor(3) as pool:
        res = list(pool.map(run_symbol, jobs))
    tr = pd.concat([t.assign(strategy=n) for n, t in zip(names, res) if not t.empty], ignore_index=True)
    with ProcessPoolExecutor(3) as pool:
        tr = tr.join(pd.DataFrame(list(pool.map(tm, tr.to_dict("records")))))
    tr.to_csv(f"logs/tm_check_{START}{'_fx' if '--fx' in sys.argv else ''}.csv", index=False)

    print(f"{START} .. {END}: {len(tr)} trades, {len(SYMS)} symbols, time machine 2,000 replays seed 19 at each entry")
    for name, g in [("ALL", tr)] + list(tr.groupby("strategy")):
        ref = g.tm_target < timemachine_min()
        print(f"\n### {name}: {stat(g.r)}")
        print(f"   REFUSED (<10% target first): {stat(g.r[ref])}")
        print(f"   kept                       : {stat(g.r[~ref])}")
        b = pd.cut(g.tm_target, [-0.01, 0.10, 0.25, 0.40, 1.0])
        print("   by target-first: " + " | ".join(f"{iv}: n{len(x)} {x.r.mean():+.2f}R" for iv, x in g.groupby(b, observed=True)))
        s = pd.cut(g.tm_stop_1h, [-0.01, 0.05, 0.15, 1.0])
        print("   by stop<1h     : " + " | ".join(f"{iv}: n{len(x)} {x.r.mean():+.2f}R" for iv, x in g.groupby(s, observed=True)))
    print("\nREPO MODE (60 days, direction kept) -- look-only: R split by the replay's average R at entry")
    for name, g in [("ALL", tr)] + list(tr.groupby("strategy")):
        fav = g.rp_avg_r > 0
        print(f"   {name:9} repo favours trade: {stat(g.r[fav])}\n   {'':9} repo against      : {stat(g.r[~fav])}")
    print("\nper symbol (ALL strategies): refused / kept avg R")
    for sym, g in tr.groupby("symbol"):
        ref = g.tm_target < timemachine_min()
        print(f"   {sym:7} refused n{ref.sum():3} {g.r[ref].mean():+.2f}R | kept n{(~ref).sum():3} {g.r[~ref].mean():+.2f}R")


def timemachine_min() -> float:
    return 0.10  # the rule that was tested (manager gate, removed after this test)


if __name__ == "__main__":
    main()
