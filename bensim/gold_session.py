"""Gold time-of-day anomaly (Blose & Gondhalekar 2014/2018: overnight up, day down; CBS thesis: up in Asian hours;
UZH thesis: drops around the London fix). User ask 2026-10-30 -- 3-month test.

    python -m bensim.gold_session [start] [end]     # default 2026-07-26 .. 2026-10-26 (DB end)

Rules (frozen 2026-10-30 before any result was seen), XAUUSD M5 (DB, bid, UTC):
  1. ASIA LONG: buy at 22:10 UTC (after the 20:55-22:05 rollover spread), sell at 07:00 UTC next morning.
     One trade per weekday morning (Monday's starts Sunday evening; first bar at/after 22:10 if the market opens later)
  2. DAY SHORT: sell at 07:00 UTC, buy back at 20:50 UTC (before rollover), Mon-Fri
  3. look-only: average move from 15 min before to 15 min after the London AM (10:30) and PM (15:00) fix, London time
Costs: long buys on the ask / sells on the bid (backtest spread model), $7/lot commission. Results in basis points of
price (net) and $ for 1 lot (100 oz).
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from .backtest import Exec, _spread, load_m5

SYM = "XAUUSD"
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
START = ARGS[0] if ARGS else "2026-07-26"
END = ARGS[1] if len(ARGS) > 1 else "2026-10-26"
COMM_PER_OZ = 7.0 / 100


def hold(m5: dict, ex: Exec, d: int, t_in: int, t_out: int) -> tuple[float, float] | None:
    """(net bp, net $ per 100 oz) for a trade from the first bar starting at/after t_in to the close of the bar ending at t_out."""
    t = m5["t"]
    i = int(np.searchsorted(t, t_in))
    j = int(np.searchsorted(t, t_out)) - 1  # last bar starting before t_out
    if i >= len(t) or j <= i or t[i] >= t_out:
        return None
    a, b = m5["open"][i], m5["close"][j]
    sa, sb = _spread(SYM, int(t[i]), ex), _spread(SYM, int(t[j]) + 300, ex)
    e = a + (sa if d == 1 else 0)
    x = b + (sb if d == -1 else 0)
    usd = ((x - e) * d - COMM_PER_OZ) * 100
    return usd / 100 / e * 1e4, usd


def line(name: str, rows: list) -> str:
    r = pd.Series([x[0] for x in rows if x])
    u = sum(x[1] for x in rows if x)
    t = r.mean() / r.std() * np.sqrt(len(r)) if len(r) > 2 and r.std() > 0 else 0.0
    return (f"  {name:28} n {len(r):3}  up {(r > 0).mean():4.0%}  avg {r.mean():+6.1f}bp  t {t:+.1f}  "
            f"total {r.sum():+7.0f}bp  = ${u:+,.0f} per 1 lot")


def main() -> None:
    lo = (pd.Timestamp(START, tz="UTC") - pd.Timedelta(days=4)).isoformat()
    m5, ex = load_m5(SYM, lo, END), Exec()
    days = pd.date_range(START, END, freq="B", tz="UTC", inclusive="left")
    asia, day, gross = [], [], []
    for dd in days:
        d0 = int(dd.timestamp())
        prev = d0 - 86400  # previous evening (Sunday evening for Monday)
        asia.append(hold(m5, ex, 1, prev + 22 * 3600 + 600, d0 + 7 * 3600))
        day.append(hold(m5, ex, -1, d0 + 7 * 3600, d0 + 20 * 3600 + 50 * 60))
        g = hold(m5, Exec(raw_spread_pips={}, commission_per_lot_rt=0.0), 1, prev + 22 * 3600 + 600, d0 + 7 * 3600)
        gross.append(g)
    print(f"{SYM} time of day {START} .. {END}")
    print(line("1. ASIA LONG 22:10->07:00", asia))
    print(line("2. DAY SHORT 07:00->20:50", day))
    print(line("   (asia long, no costs)", gross))
    t = pd.to_datetime(m5["t"], unit="s", utc=True).tz_convert("Europe/London")
    for name, hh, mm in (("AM fix 10:30", 10, 30), ("PM fix 15:00", 15, 0)):
        rows = []
        for dd in days:
            k = np.flatnonzero((t.normalize() == dd.tz_convert("Europe/London").normalize()) &
                               (t.hour * 60 + t.minute == hh * 60 + mm - 15))
            if len(k) and k[0] + 6 < len(m5["t"]):
                a, b = m5["open"][k[0]], m5["close"][k[0] + 5]
                rows.append(((b - a) / a * 1e4, (b - a) * 100))
        print(line(f"3. look: {name} +/-15m gross", rows))


def fix_short(start: str = "2021-10-01", end: str = END) -> None:
    """5y check (frozen 2026-10-30 after the 3m look): SELL at the 10:15 London bar open, BUY back at the 10:40 bar close
    (10:45), every weekday, net of spread + commission. Pass = negative-for-the-short... i.e. short positive in most years."""
    m5, ex = load_m5(SYM, start, end), Exec()
    t = pd.to_datetime(m5["t"], unit="s", utc=True).tz_convert("Europe/London")
    for name, hh, mm in (("AM fix short 10:15->10:45", 10, 15), ("PM fix short 14:45->15:15 (look)", 14, 45)):
        k = np.flatnonzero((t.hour * 60 + t.minute == hh * 60 + mm) & (t.weekday < 5))
        k = k[(k + 5 < len(m5["t"]))]
        k = k[m5["t"][k + 5] - m5["t"][k] == 1500]  # 6 contiguous bars
        e = m5["open"][k]
        x = m5["close"][k + 5] + np.array([_spread(SYM, int(v) + 300, ex) for v in m5["t"][k + 5]])
        bp = pd.Series(((e - x) - COMM_PER_OZ) / e * 1e4, index=t[k].year)
        gross = pd.Series((m5["open"][k] - m5["close"][k + 5]) / e * 1e4, index=t[k].year)
        print(f"\n{name}  ({start} .. {end}, net bp per trade, short P&L)")
        for y, g in bp.groupby(level=0):
            tt = g.mean() / g.std() * np.sqrt(len(g))
            print(f"  {y}  n {len(g):3}  win {(g > 0).mean():4.0%}  avg {g.mean():+5.2f}bp  t {tt:+.1f}  "
                  f"(gross {gross[gross.index == y].mean():+5.2f}bp)")
        tt = bp.mean() / bp.std() * np.sqrt(len(bp))
        print(f"  ALL   n {len(bp)}  win {(bp > 0).mean():4.0%}  avg {bp.mean():+5.2f}bp  t {tt:+.1f}  "
              f"total {bp.sum():+.0f}bp")


if __name__ == "__main__":
    fix_short() if "--fix5y" in sys.argv else main()
