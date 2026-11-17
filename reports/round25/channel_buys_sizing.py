"""Buys-only channel mode, 5 years (2021-10 .. 2026-11, 1,044 trades): sizing options on the 100k account.
  fixed 5 lots | fixed 7 lots | $500 risk | $1,000 risk | MIX: $500 risk capped at 5 lots, $1,000 risk capped at 7 lots
Lots per trade = risk $ / (stop pips x pip value per lot), rounded down to 0.01; capped where stated.
Reports total, worst drawdown (from the running peak), worst calendar year, and whether the 100k would ever have been
wiped (equity <= 0) or hit -10% / -20% from the start.
    python reports/round25/channel_buys_sizing.py
"""
import numpy as np
import pandas as pd

D = "D:/Devops/trading/OpenTerminalUI/reports/round25/"
U = pd.read_csv(D + "channel_untouched_trades.csv")
T = pd.read_csv(D + "two_year_trades.csv")
T = T[T.strategy == "channel CHANNEL"]
a = pd.concat([U, T])
a = a[a.dir == 1].sort_values("t_entry").reset_index(drop=True)
pipd = {"USDJPY": 0.01, "XAUUSD": 0.1}
pv = lambda s, p: 1000 / p if s == "USDJPY" else 10 / p if s in ("USDCAD", "USDCHF") else 10.0  # noqa: E731
stop_pips = (a.entry - a.sl).abs() / a.sym.map(lambda s: pipd.get(s, 1e-4))
loss_per_lot = stop_pips * [pv(s, e) for s, e in zip(a.sym, a.entry)]  # $ lost per lot at -1R
a["y"] = pd.to_datetime(a.t_entry, unit="s").dt.year


def lots(name):
    if name.startswith("fixed"):
        return pd.Series(float(name.split()[1]), index=a.index)
    risk = 500.0 if "$500" in name else 1000.0
    lt = np.floor(risk / loss_per_lot * 100) / 100
    if "cap" in name:
        lt = np.minimum(lt, 5.0 if "$500" in name else 7.0)
    return lt


print("BUYS-ONLY channel, 5 years, 1,044 trades, 100k start")
print(f"{'sizing':34} {'avg lots':>8} {'total $':>11} {'worst DD $':>11} {'worst year $':>13} {'lowest equity':>14}  account")
for name in ("fixed 5 lots", "fixed 7 lots", "$500 risk", "$1,000 risk", "MIX $500 risk cap 5 lots", "MIX $1,000 risk cap 7 lots"):
    lt = lots(name)
    pnl = a.r * loss_per_lot * lt
    eq = 100_000 + np.r_[0, np.cumsum(pnl.values)]
    dd = (np.maximum.accumulate(eq) - eq).max()
    yr = pnl.groupby(a.y).sum()
    low = eq.min()
    state = "WIPED OUT" if low <= 0 else ("down >20% at worst" if low < 80_000 else ("down >10% at worst" if low < 90_000 else "ok"))
    print(f"{name:34} {lt.mean():8.2f} {pnl.sum():+11,.0f} {dd:11,.0f} {yr.min():+13,.0f} {low:14,.0f}  {state}")
    print(f"{'':34} per year: " + ", ".join(f"{y} {v:+,.0f}" for y, v in yr.items()))
