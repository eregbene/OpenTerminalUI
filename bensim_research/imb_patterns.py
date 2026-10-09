"""Learn-then-test pattern study for the imbalance-video strategy.

Step 1: learn on 2024 -> exclusion rules -> test blind on 2025.
Step 2: learn on 2024+2025 -> exclusion rules -> test blind on 2026-01..2026-08.
Universe: the video's entry and target (first retest of an armed zone, nearest opposite zone), M30/H1/H4,
all 7 instruments. Rule (fixed before running): exclude a bucket when its average net R < 0, it is worse
than the rest of the learn set with Welch t <= -2, and it has >= 50 trades. Keep at most the 5 strongest.

python -m bensim_research.imb_patterns   (needs data/research/artifacts/bensim/imb_zone/wf_pool.csv.gz from imb_wf)
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import data as D
from .imb_wf import DEST
from .run import OUT

ROOT = OUT.parents[1]
FEATURES = {
    "tf": None, "symbol": None, "dir": None, "session": None, "hour4": None, "weekday": None,
    "killzone_formed": None, "htf_aligned": None, "strong_disp": None, "extreme_zone": None,
    "stop_spreads": [0, 8, 15, 25, 1e9], "zone_atr": [0, 0.5, 1.0, 1e9], "run_gaps": [0, 1, 2, 1e9],
    "age_bars": [0, 10, 50, 1e9], "bos_bars": [-1, 5, 20, 1e9], "rr": [0, 3, 6, 1e9],
}


def prep() -> pd.DataFrame:
    p = pd.read_csv(DEST / "wf_pool.csv.gz")
    p = p[p.tmode == "near"].copy()
    ts = pd.to_datetime(p.t_entry, unit="s")
    p["year"] = ts.dt.year
    p["month"] = ts.dt.strftime("%Y-%m")
    p["session"] = D.session_of(p.t_entry.to_numpy())
    p["hour4"] = (ts.dt.hour // 4 * 4).map(lambda h: f"{h:02d}-{h + 4:02d}h UTC")
    p["weekday"] = ts.dt.day_name().str[:3]
    p["dir"] = p.dir.map({1: "buy", -1: "sell"})
    p = p.rename(columns={"kz": "killzone_formed", "htf": "htf_aligned", "disp": "strong_disp", "extreme": "extreme_zone",
                          "minmult_val": "stop_spreads"})
    for f, edges in FEATURES.items():
        if edges:
            p[f] = pd.cut(p[f], edges).astype(str)
    return p


def welch(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3 or len(b) < 3:
        return 0.0
    return float((a.mean() - b.mean()) / np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b)))


def describe(t: pd.DataFrame) -> dict:
    out = {}
    for f in FEATURES:
        rows = []
        for k, g in t.groupby(f):
            rest = t[t[f] != k]
            r = g.r.to_numpy()
            rows.append({"bucket": str(k), "n": len(g), "win%": round(100 * (g.exit_reason == "target").mean(), 1),
                         "avg_net_R": round(float(r.mean()), 3), "avg_gross_R": round(float(g.r_gross.mean()), 3),
                         "t_vs_rest": round(welch(r, rest.r.to_numpy()), 2)})
        out[f] = rows
    return out


def learn(t: pd.DataFrame) -> list[tuple[str, str, float]]:
    cands = []
    for f, rows in describe(t).items():
        for r in rows:
            if r["n"] >= 50 and r["avg_net_R"] < 0 and r["t_vs_rest"] <= -2:
                cands.append((f, r["bucket"], r["t_vs_rest"]))
    return sorted(cands, key=lambda c: c[2])[:5]


def apply(t: pd.DataFrame, rules: list) -> pd.DataFrame:
    m = np.ones(len(t), bool)
    for f, b, _ in rules:
        m &= t[f].astype(str).to_numpy() != b
    return t[m]


def summ(t: pd.DataFrame) -> dict:
    r = t.r.to_numpy()
    if not len(r):
        return {"trades": 0}
    return {"trades": len(r), "win%": round(100 * float((t.exit_reason == "target").mean()), 1), "avg_net_R": round(float(r.mean()), 3),
            "total_R": round(float(r.sum()), 1), "t": round(float(r.mean() / r.std(ddof=1) * np.sqrt(len(r))), 2) if len(r) > 2 else None}


def main() -> None:
    p = prep()
    rep: dict = {"universe": "video entry + nearest-zone target, M30/H1/H4, 7 instruments"}
    steps = (("2024 -> 2025", [2024], [2025]), ("2024+2025 -> 2026", [2024, 2025], [2026]))
    for name, ly, ty in steps:
        L, T = p[p.year.isin(ly)], p[p.year.isin(ty)]
        rules = learn(L)
        LT, TT = apply(L, rules), apply(T, rules)
        rep[name] = {"rules": [f"exclude {f} = {b} (learn t {t:.1f})" for f, b, t in rules],
                     "learn_all": summ(L), "learn_filtered": summ(LT), "test_all": summ(T), "test_filtered": summ(TT),
                     "test_filtered_by_tf": {k: summ(g) for k, g in TT.groupby("tf")},
                     "test_all_by_tf": {k: summ(g) for k, g in T.groupby("tf")},
                     "test_filtered_by_month": {k: summ(g) for k, g in TT.groupby("month")}}
    rep["describe_2024"] = describe(p[p.year == 2024])
    rep["describe_2025"] = describe(p[p.year == 2025])
    (OUT / "imb_patterns_report.json").write_text(json.dumps(rep, indent=1))
    print(json.dumps({k: v for k, v in rep.items() if not k.startswith("describe")}, indent=1))


if __name__ == "__main__":
    main()
