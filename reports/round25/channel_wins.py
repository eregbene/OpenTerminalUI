"""When did the Bennett channel mode win over the 2 years (2024-11 .. 2026-11), and is there a pattern that separates
winners from losers? Reads two_year_trades.csv (engine R, own stop). Descriptive only -- any filter found here must be
checked on the UNTOUCHED 2021-10 .. 2024-11 trades before it means anything.
    python reports/round25/channel_wins.py
"""
import numpy as np
import pandas as pd

T = pd.read_csv("D:/Devops/trading/OpenTerminalUI/reports/round25/two_year_trades.csv")
c = T[T.strategy == "channel CHANNEL"].copy()
c["time"] = pd.to_datetime(c.t_entry, unit="s")
c["month"] = c.time.dt.to_period("M")
c["hour"] = c.time.dt.hour
c["wday"] = c.time.dt.day_name().str[:3]
c["rr"] = (c.tp - c.entry).abs() / (c.entry - c.sl).abs()
c["win"] = c.r > 0
print(f"{len(c)} trades, {c.win.sum()} winners ({c.win.mean():.0%}), total {c.r.sum():+.1f}R; winners {c[c.win].r.sum():+.1f}R, losers {c[~c.win].r.sum():+.1f}R")
big = c[c.r >= 5].sort_values("time")
print(f"\nBIG winners (>= 5R): {len(big)} trades = {big.r.sum():+.1f}R")
for x in big.itertuples():
    print(f"  {x.time:%Y-%m-%d %H:%M}  {x.sym}  {'BUY ' if x.dir == 1 else 'SELL'}  entry {x.entry:.5f}  +{x.r:.1f}R  (planned {x.rr:.1f}R)")
print("\nR by month:")
m = c.groupby("month").agg(trades=("r", "size"), wins=("win", "sum"), R=("r", "sum")).round(1)
print(m.to_string())
for col in ("sym", "dir", "wday"):
    g = c.groupby(col).agg(trades=("r", "size"), win=("win", "mean"), R=("r", "sum"), avg=("r", "mean")).round(2)
    print(f"\nby {col}:\n{g.to_string()}")
c["hour_bin"] = pd.cut(c.hour, [-1, 6, 12, 17, 23], labels=["00-06 Asia", "07-12 London", "13-17 NY", "18-23 late"])
print("\nby hour (UTC):\n", c.groupby("hour_bin", observed=True).agg(trades=("r", "size"), win=("win", "mean"), R=("r", "sum")).round(2).to_string())
c["rr_bin"] = pd.qcut(c.rr, 4)
print("\nby planned reward:risk:\n", c.groupby("rr_bin", observed=True).agg(trades=("r", "size"), win=("win", "mean"), R=("r", "sum")).round(2).to_string())
# streaks of the same pair/direction within 3 days: first attempt vs re-entries
c = c.sort_values("time")
c["attempt"] = c.groupby(["sym", "dir"]).time.transform(lambda s: (s.diff().dt.total_seconds().fillna(1e9) > 3 * 86400).cumsum())
c["n_in_run"] = c.groupby(["sym", "dir", "attempt"]).cumcount() + 1
print("\nby attempt number in a run of same-pair same-direction entries (<3 days apart):\n",
      c.groupby(c.n_in_run.clip(upper=5)).agg(trades=("r", "size"), win=("win", "mean"), R=("r", "sum")).round(2).to_string())
