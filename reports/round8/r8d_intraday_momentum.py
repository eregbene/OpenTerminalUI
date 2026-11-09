"""Round 8d: intraday momentum into the London 16:00 fix (Elaut, Frommel & Lampaert 2018, "Intraday momentum in FX
markets"). Written before running. Exact quotes (H1 closes; buy the ask, sell the bid).
  M1  signal = log return from the 21:00 UTC close (previous day) to the 15:00 UTC close; trade 15:00 -> 16:00 UTC in
      the signal's direction (the fix hour). Weekdays.
  M2  signal = 21:00 (prev) -> 09:00 UTC close; trade 09:00 -> 12:00 UTC in the signal's direction.
  Both, every FXCM pair, plus the pooled basket (mean over pairs per day). Grade as round 6, BH over the 2 x 26 tests.
    python reports/round8/r8d_intraday_momentum.py
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

sys.path[:0] = ["D:/Devops/trading/OpenTerminalUI/reports/round6"]
from r6_box import PAIRS, by_split, grade  # noqa: E402

FX = "D:/Devops/trading/OpenTerminalUI/data/bars/fxcm/"
OUT = "D:/Devops/trading/OpenTerminalUI/reports/round8/"
VARIANTS = {"M1_fix_hour": (21, 15, 16), "M2_london_morning": (21, 9, 12)}


def main() -> None:
    res, pooled = {}, {k: [] for k in VARIANTS}
    for sym in PAIRS:
        try:
            z = np.load(FX + f"{sym}_H1.npz")
        except FileNotFoundError:
            continue
        F = pd.DataFrame({"t": pd.to_datetime(z["t"] + 3600, unit="s"), "bid": z["c"], "ask": z["c"] + z["spr"]}).set_index("t")
        F["mid"] = (F.bid + F.ask) / 2
        for name, (s0, s1, x) in VARIANTS.items():
            rows = []
            for day in pd.date_range("2012-01-03", "2026-10-09", freq="B"):
                a = day - pd.Timedelta(days=1) + pd.Timedelta(hours=s0)
                b, e = day + pd.Timedelta(hours=s1), day + pd.Timedelta(hours=x)
                if a not in F.index or b not in F.index or e not in F.index:
                    continue
                sig = np.log(F.mid.at[b] / F.mid.at[a])
                if sig == 0:
                    continue
                d = 1 if sig > 0 else -1
                r = ((F.bid.at[e] - F.ask.at[b]) / F.ask.at[b] if d > 0 else (F.bid.at[b] - F.ask.at[e]) / F.bid.at[b]) * 1e4 - 0.3
                rows.append({"t": day, "r": r})
            R = pd.DataFrame(rows)
            if len(R):
                res[f"{name}|{sym}"] = {**by_split(R)}
                res[f"{name}|{sym}"]["grade"] = grade(res[f"{name}|{sym}"])
                pooled[name].append(R.set_index("t").r.rename(sym))
        print(sym, flush=True)
    for name, parts in pooled.items():
        B = pd.concat(parts, axis=1).mean(axis=1).reset_index()
        B.columns = ["t", "r"]
        res[f"{name}|BASKET"] = {**by_split(B)}
        res[f"{name}|BASKET"]["grade"] = grade(res[f"{name}|BASKET"])
    json.dump(res, open(OUT + "r8d_results.json", "w"), indent=1, default=str)
    keep = {k: v for k, v in res.items() if v["grade"] != "FAILED" or k.endswith("BASKET")}
    for k, v in keep.items():
        print(k, {s: (v[s].get("n"), v[s].get("mean"), v[s].get("t")) for s in ("DISC", "VAL", "LOCK")}, v.get("years_pos"), v["grade"])
    print("grades:", pd.Series([v["grade"] for v in res.values()]).value_counts().to_dict())


if __name__ == "__main__":
    main()
