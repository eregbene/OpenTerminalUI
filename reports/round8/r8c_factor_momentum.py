"""Round 8c: FACTOR momentum (Zhang 2022, J. Financial Economics: currency momentum is mostly factor momentum).
Written before running. Exact quotes only (H1 bar closes; bid / ask = bid + that close's spread).
F1 DOLLAR FACTOR TSMOM: daily dollar factor = mean over EURUSD GBPUSD AUDUSD NZDUSD USDJPY USDCHF USDCAD of the USD-long
   return. Every Friday's last close: sign of the factor's past k trading-day return (k = 21, 63, 126) -> hold the whole
   7-pair dollar basket long / short USD until next Friday. Costs: a leg pays its real spread (+0.3 bp) only when the
   position changes (new or flipped). Weekly bp of the basket (mean of the 7 legs).
F2 CARRY TIMING: the round-6f-EXACT carry basket (markup 0.7 %, flat size): hold month m only if the basket's previous
   3 months summed > 0, else flat (no cost when flat). Monthly bp.
Splits DISC <= 2018 / VAL 2019-22 / LOCK 2023-26, t on the weekly / monthly series, grade as round 6.
    python reports/round8/r8c_factor_momentum.py
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd

sys.path[:0] = ["D:/Devops/trading/OpenTerminalUI/reports/round6"]
from r6_box import by_split, grade  # noqa: E402
import r6f_carry_exact as CE  # noqa: E402

OUT = "D:/Devops/trading/OpenTerminalUI/reports/round8/"
FX = "D:/Devops/trading/OpenTerminalUI/data/bars/fxcm/"
USDLEG = {"EURUSD": -1, "GBPUSD": -1, "AUDUSD": -1, "NZDUSD": -1, "USDJPY": 1, "USDCHF": 1, "USDCAD": 1}  # +1 = long pair is long USD


def daily(sym: str) -> pd.DataFrame:
    z = np.load(FX + f"{sym}_H1.npz")
    F = pd.DataFrame({"t": pd.to_datetime(z["t"] + 3600, unit="s"), "bid": z["c"], "ask": z["c"] + z["spr"]}).set_index("t")
    D = F.groupby(F.index.normalize()).last()
    D["mid"] = (D.bid + D.ask) / 2
    return D


def f1() -> dict:
    Ds = {s: daily(s) for s in USDLEG}
    idx = sorted(set.intersection(*[set(d.index) for d in Ds.values()]))
    mid = pd.DataFrame({s: Ds[s].mid.reindex(idx) for s in USDLEG})
    fac = pd.Series(np.mean([np.log(mid[s]).diff() * g for s, g in USDLEG.items()], axis=0), index=mid.index)
    fridays = [d for d in mid.index if d.weekday() == 4]
    res = {}
    for k in (21, 63, 126):
        cum = fac.rolling(k).sum()
        rows, pos = [], 0
        for a, z in zip(fridays[:-1], fridays[1:]):
            if not np.isfinite(cum.loc[a]):
                continue
            new = 1 if cum.loc[a] > 0 else -1
            legs = []
            for s, g in USDLEG.items():
                d = new * g  # +1 = long the pair
                b0, a0, b1 = Ds[s].bid.get(a), Ds[s].ask.get(a), Ds[s].bid.get(z)
                a1 = Ds[s].ask.get(z)
                if None in (b0, a0, b1, a1) or not all(np.isfinite([b0, a0, b1, a1])):
                    continue
                m0, m1 = (b0 + a0) / 2, (b1 + a1) / 2
                gross = d * (m1 - m0) / m0 * 1e4
                cost = ((a0 - b0) / m0 * 1e4 + 0.3) if new != pos else 0.0  # pay the spread only when the leg changes
                legs.append(gross - cost)
            if legs:
                rows.append({"t": a, "r": float(np.mean(legs)), "flip": new != pos})
            pos = new
        R = pd.DataFrame(rows)
        res[f"k{k}"] = {**by_split(R), "weeks": int(len(R)), "flips_per_year": round(float(R.flip.sum() / (len(R) / 52)), 1),
                        "pct_per_year": round(float(R.r.mean() * 52 / 100), 2), "by_year_pct": (R.groupby(R.t.dt.year).r.sum() / 100).round(2).to_dict()}
        res[f"k{k}"]["grade"] = grade(res[f"k{k}"])
    return res


def f2() -> dict:
    R = CE.monthly(0.7, "FLAT")
    B = R.groupby("t").r.mean().sort_index()
    prev3 = B.rolling(3).sum().shift(1)
    on = prev3 > 0
    T = pd.DataFrame({"t": B.index, "r": np.where(on, B.to_numpy(), 0.0), "on": on.to_numpy()})
    T = T[prev3.notna().to_numpy()]
    base = pd.DataFrame({"t": B.index, "r": B.to_numpy()})[prev3.notna().to_numpy()]
    eq_t, eq_b = T.r.cumsum(), base.r.cumsum()
    out = {"TIMED": {**by_split(T), "pct_per_year": round(float(T.r.mean() * 12 / 100), 2), "max_dd_bp": round(float((eq_t - eq_t.cummax()).min()), 0),
                     "months_on": round(float(T.on.mean()), 2), "by_year_pct": (T.groupby(pd.to_datetime(T.t).dt.year).r.sum() / 100).round(2).to_dict()},
           "ALWAYS": {**by_split(base), "pct_per_year": round(float(base.r.mean() * 12 / 100), 2), "max_dd_bp": round(float((eq_b - eq_b.cummax()).min()), 0)}}
    out["TIMED"]["grade"], out["ALWAYS"]["grade"] = grade(out["TIMED"]), grade(out["ALWAYS"])
    return out


def main() -> None:
    res = {"F1_dollar_tsmom": f1(), "F2_carry_timing": f2()}
    json.dump(res, open(OUT + "r8c_results.json", "w"), indent=1, default=str)
    for name, v in res.items():
        for k, x in v.items():
            print(name, k, {s: (x[s].get("n"), x[s].get("mean"), x[s].get("t")) for s in ("DISC", "VAL", "LOCK")}, x.get("pct_per_year"), "%/yr", x.get("max_dd_bp", ""), x["grade"])


if __name__ == "__main__":
    main()
