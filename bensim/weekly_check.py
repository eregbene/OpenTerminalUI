"""Friday forward check: live fib618 config vs base vs the frozen CANDIDATE, on weeks the candidate has never seen.

    python -m bensim.weekly_check                      # last completed Mon-Fri week (or this week from Friday 21:00 UTC)
    python -m bensim.weekly_check --week 2026-10-12    # the week starting that Monday

CANDIDATE (frozen 2026-10-14, chosen on 5y Nov 2021 - Oct 2026 -- do NOT tune): fib618 base (zone_mode fib, no
confirmations, all 7 symbols) + only trades with liquidity >= 1R ahead (chk_liq_ahead_r >= 1 or none found) + target
2R (stop as base; trades planned below 2R keep their own target). 5y: 1,497 tr +0.100R, 5/6 years +, DD 35R.
Judge after ~50 candidate trades; the 5y number is the bar to beat, the base (+0.038R) the floor.
Results append to data/forward_check.csv (one row per week and version, rerunning a week replaces it). No orders, ever.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import SYMBOLS, Exec, run_symbol, strategy
from .trade_path import paths

OUT = Path("data/forward_check.csv")
BASE = strategy.Params(zone_mode="fib")
LIVE = strategy.Params(zone_mode="fib", confirm_any=("killzone", "poi", "sweep"), confirm_min=2)  # = live.PARAMS


def replay(p: strategy.Params, start: str, end: str) -> pd.DataFrame:
    with ProcessPoolExecutor(2) as pool:  # 2 workers: the live engine keeps the CPU
        fr = [x for x in pool.map(run_symbol, [(s, start, end, p, Exec(), "imbalance") for s in SYMBOLS]) if not x.empty]
    return pd.concat(fr, ignore_index=True) if fr else pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", default=None, help="Monday of the week (YYYY-MM-DD)")
    a = ap.parse_args()
    now = pd.Timestamp.now(tz="UTC")
    mon = pd.Timestamp(a.week, tz="UTC") if a.week else (now - pd.Timedelta(days=now.weekday())).normalize()
    if not a.week and now < mon + pd.Timedelta(days=4, hours=21):
        mon -= pd.Timedelta(days=7)  # week not finished yet -> the previous one
    start, end = mon.strftime("%Y-%m-%d"), (mon + pd.Timedelta(days=5)).strftime("%Y-%m-%d")
    print(f"week {start} .. {end} (entries in this week; exits may run past Friday)")
    base, live = replay(BASE, start, end), replay(LIVE, start, end)
    rows = {"live config": live, "base": base}
    if len(base):
        base = base.reset_index(drop=True)
        cand = base[base.chk_liq_ahead_r.fillna(99) >= 1]
        if len(cand):
            cand = cand.assign(r=paths(cand)["tgt2"].to_numpy())
        rows["CANDIDATE"] = cand
    out = []
    for name, t in rows.items():
        n = len(t)
        r = t.r.to_numpy() if n else np.array([])
        out.append({"week": start, "version": name, "trades": n, "wins": int((r > 0).sum()), "R": round(float(r.sum()), 2),
                    "avg_R": round(float(r.mean()), 3) if n else 0.0})
        print(f"  {name:12} {n:3} trades  {r.sum():+6.2f}R" + (f"  avg {r.mean():+.3f}R" if n else ""))
    hist = pd.read_csv(OUT) if OUT.exists() else pd.DataFrame()
    if len(hist):
        hist = hist[hist.week != start]
    hist = pd.concat([hist, pd.DataFrame(out)], ignore_index=True).sort_values(["week", "version"])
    hist.to_csv(OUT, index=False)
    tot = hist.groupby("version").agg(weeks=("week", "nunique"), trades=("trades", "sum"), R=("R", "sum"))
    tot["avg_R"] = (tot.R / tot.trades.where(tot.trades > 0)).round(3)
    print(f"\nRUNNING TOTAL since the freeze ({OUT}):\n{tot.to_string()}")
    c = tot.loc["CANDIDATE"] if "CANDIDATE" in tot.index else None
    if c is not None:
        left = max(0, 50 - int(c.trades))
        print(f"\nCANDIDATE: {int(c.trades)} trades so far" + (f", {left} more before judging (bar: +0.10R 5y, floor: +0.038R base)" if left
              else " -- enough to judge: compare its avg R with +0.10R (5y) and +0.038R (base)"))


if __name__ == "__main__":
    main()
