"""Round 8f: risk-on / risk-off spill-over (written before running). After a large S&P 500 day (Yahoo ^GSPC daily close,
known at the US close ~21:00 UTC), trade the JPY crosses (USDJPY, AUDJPY, CADJPY, NZDJPY, EURJPY, GBPJPY) from the
first FXCM H1 close at or after 22:00 UTC that day for 24 h, exact quotes (buy the ask, sell the bid) - 0.3 bp:
  DOWN  S&P daily return <= -1.5 %  -> SELL the crosses (risk-off continuation)
  UP    S&P daily return >= +1.5 %  -> BUY the crosses (risk-on continuation)
Also the reversal readings (opposite side) as separate tests. Basket = mean over the 6 crosses per event.
Grade as round 6; with 4 tests read single passes carefully.
    python reports/round8/r8f_spx_jpy.py
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

sys.path[:0] = ["D:/Devops/trading/OpenTerminalUI/reports/round6"]
from r6_box import by_split, grade  # noqa: E402

FX = "D:/Devops/trading/OpenTerminalUI/data/bars/fxcm/"
OUT = "D:/Devops/trading/OpenTerminalUI/reports/round8/"
CROSSES = ["USDJPY", "AUDJPY", "CADJPY", "NZDJPY", "EURJPY", "GBPJPY"]


def main() -> None:
    r = json.load(open("D:/Devops/trading/OpenTerminalUI/data/macro/xasset/yahoo_GSPC.json"))["chart"]["result"][0]
    spx = pd.Series(r["indicators"]["quote"][0]["close"], index=pd.to_datetime(r["timestamp"], unit="s").normalize()).dropna()
    ret = spx.pct_change()
    events = {"DOWN": ret[ret <= -0.015].index, "UP": ret[ret >= 0.015].index}
    F = {}
    for s in CROSSES:
        z = np.load(FX + f"{s}_H1.npz")
        f = pd.DataFrame({"t": pd.to_datetime(z["t"] + 3600, unit="s"), "bid": z["c"], "ask": z["c"] + z["spr"]}).set_index("t")
        F[s] = f
    res = {}
    for name, days in events.items():
        for reading, sign in (("continuation", -1 if name == "DOWN" else 1), ("reversal", 1 if name == "DOWN" else -1)):
            rows = []
            for d in days:
                if d < pd.Timestamp("2012-01-01"):
                    continue
                legs = []
                for s in CROSSES:
                    f = F[s]
                    i = f.index.searchsorted(d + pd.Timedelta(hours=22))
                    if i >= len(f):
                        continue
                    a = f.index[i]
                    j = f.index.searchsorted(a + pd.Timedelta(hours=24))
                    if j >= len(f) or (f.index[j] - a) > pd.Timedelta(hours=30) or (a - d) > pd.Timedelta(hours=60):
                        continue
                    b = f.index[j]
                    rr = ((f.bid.at[b] - f.ask.at[a]) / f.ask.at[a] if sign > 0 else (f.bid.at[a] - f.ask.at[b]) / f.bid.at[a]) * 1e4 - 0.3
                    legs.append(rr)
                if legs:
                    rows.append({"t": d, "r": float(np.mean(legs))})
            R = pd.DataFrame(rows)
            key = f"{name}_{reading}"
            res[key] = {**by_split(R), "events": int(len(R))}
            res[key]["grade"] = grade(res[key])
            print(key, {s: (res[key][s].get("n"), res[key][s].get("mean"), res[key][s].get("t")) for s in ("DISC", "VAL", "LOCK")}, res[key]["years_pos"], res[key]["grade"])
    json.dump(res, open(OUT + "r8f_results.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
