"""Buys-only channel mode over all 5 years (untouched 2021-10..2024-11 + 2-year 2024-11..2026-11): drawdown and streaks."""
import itertools

import numpy as np
import pandas as pd

D = "D:/Devops/trading/OpenTerminalUI/reports/round25/"
U = pd.read_csv(D + "channel_untouched_trades.csv")
T = pd.read_csv(D + "two_year_trades.csv")
T = T[T.strategy == "channel CHANNEL"]
a = pd.concat([U, T])
a = a[a.dir == 1].sort_values("t_entry")
pipd = {"USDJPY": 0.01, "XAUUSD": 0.1}
pv = lambda s, p: 1000 / p if s == "USDJPY" else 10 / p if s in ("USDCAD", "USDCHF") else 10.0  # noqa: E731
a["u1"] = a.r * (a.entry - a.sl).abs() / a.sym.map(lambda s: pipd.get(s, 1e-4)) * [pv(s, e) for s, e in zip(a.sym, a.entry)]
for lab, x in (("$500 risk", a.r * 500), ("7 lots", a.u1 * 7)):
    eq = np.r_[0, np.cumsum(x.values)]
    print(f"{lab:10} total ${eq[-1]:+,.0f}   worst drawdown ${(np.maximum.accumulate(eq) - eq).max():,.0f}")
print("longest losing streak:", max(len(list(g)) for k, g in itertools.groupby(a.r < 0) if k), "trades")
a["y"] = pd.to_datetime(a.t_entry, unit="s").dt.year
print("per year R:", a.groupby("y").r.sum().round(1).to_dict(), "| trades per year:", a.groupby("y").size().to_dict())
