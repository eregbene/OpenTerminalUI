"""Round 7: Flow ML on ALL 25 pairs (written before running; the live model is unchanged).
Replicates bensim.macro_yields.ml_ensemble exactly (edge-miner features + 2y yield gaps, NaN where a currency has no
yields; HGB long and short models; yearly walk-forward, train on years < Y-1; 6 h holds; one trade per pair; bp net of
the real spread as in edge_wf.trades) but with 3 seeds (0, 7, 13) instead of 5 to halve the CPU time, and the
predictions scored on all 25 pairs instead of the live 5. Writes only reports/round7/ (never data/macro/).
Thresholds (fixed): GLOBAL = 95th pct of the previous year's predictions over all 25 pairs; PAIR = each pair's own
95th pct of its previous-year predictions. Groups: LIVE5 (EURUSD USDJPY USDCAD EURJPY CADJPY, = reproduction) and
NEW20 (the others). Also the live late-hour rule (no entries at 20-00 UTC) applied to both.
Judged by halves 2016-20 / 2021-26 and per pair; week-clustered t.
    OMP_NUM_THREADS=4 python reports/round7/r7_flowml_pairs.py
"""
from __future__ import annotations

import json
import pickle
import sys

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import HistGradientBoostingRegressor

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
from bensim.edge_miner import FEATURES, PATH  # noqa: E402

OUT = "D:/Devops/trading/OpenTerminalUI/reports/round7/"
LIVE5 = ["EURUSD", "USDJPY", "USDCAD", "EURJPY", "CADJPY"]
H, SEEDS = 6, (0, 7, 13)
LATE = {20, 21, 22, 23, 0}


def trades(df: pd.DataFrame, pl: np.ndarray, ps: np.ndarray, thr) -> pd.DataFrame:
    out = []
    for sym, g in df.assign(pl=pl, ps=ps).groupby("symbol"):
        th = thr[sym] if isinstance(thr, dict) else thr
        busy = pd.Timestamp(0)
        for x in g.itertuples():
            if x.t < busy or max(x.pl, x.ps) <= th:
                continue
            side = "long" if x.pl >= x.ps else "short"
            r = getattr(x, f"{side}_{H}")
            if np.isfinite(r):
                out.append((sym, x.t, x.year, x.t.hour, side, r))
                busy = x.t + pd.Timedelta(hours=H)
    return pd.DataFrame(out, columns=["symbol", "t", "year", "hour", "side", "bp"])


def wstat(g: pd.DataFrame) -> dict:
    if len(g) < 30:
        return {"n": int(len(g))}
    s = g.groupby(g.t.dt.to_period("W")).bp.sum()
    t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
    return {"n": int(len(g)), "bp": round(float(g.bp.mean()), 2), "t": round(float(t), 2), "p": round(float(stats.t.sf(t, len(s) - 1)), 4),
            "total": round(float(g.bp.sum()), 0), "win": round(float((g.bp > 0).mean()), 3)}


def main() -> None:
    with open(PATH, "rb") as fh:  # our own cache from edge_miner build
        f = pickle.load(fh)
    y = pd.read_csv("D:/Devops/trading/OpenTerminalUI/data/macro/yields_2y.csv", index_col=0, parse_dates=True).ffill(limit=5).shift(1)
    day = f.t.dt.normalize()
    for c in ("level", "d5", "d20", "d60"):
        f[f"gap_{c}"] = np.nan
    for sym in f.symbol.unique():
        b, q = sym[:3], sym[3:]
        if b in y and q in y:
            g, k = y[b] - y[q], f.symbol == sym
            f.loc[k, "gap_level"] = g.reindex(day[k], method="ffill").to_numpy()
            for n in (5, 20, 60):
                f.loc[k, f"gap_d{n}"] = (g - g.shift(n)).reindex(day[k], method="ffill").to_numpy()
    feats = FEATURES + ["gap_level", "gap_d5", "gap_d20", "gap_d60"]
    T = {"GLOBAL": [], "PAIR": []}
    for yr in range(2016, int(f.year.max()) + 1):
        tr = f[f.year < yr - 1]
        tr = tr[np.isfinite(tr[f"long_{H}"]) & np.isfinite(tr[f"short_{H}"])]
        prev, cur = f[f.year == yr - 1], f[f.year == yr]
        pl_prev = ps_prev = pl = ps = 0.0
        for s in SEEDS:
            mk = lambda yy: HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,  # noqa: E731
                                                          min_samples_leaf=2000, l2_regularization=1.0, random_state=s).fit(tr[feats], yy)
            ml_, ms_ = mk(np.clip(tr[f"long_{H}"], -300, 300)), mk(np.clip(tr[f"short_{H}"], -300, 300))
            pl_prev, ps_prev = pl_prev + ml_.predict(prev[feats]) / len(SEEDS), ps_prev + ms_.predict(prev[feats]) / len(SEEDS)
            pl, ps = pl + ml_.predict(cur[feats]) / len(SEEDS), ps + ms_.predict(cur[feats]) / len(SEEDS)
        mx = np.maximum(pl_prev, ps_prev)
        thr_g = float(np.quantile(mx, 0.95))
        thr_p = {sym: float(np.quantile(mx[(prev.symbol == sym).to_numpy()], 0.95)) for sym in prev.symbol.unique()}
        T["GLOBAL"].append(trades(cur, pl, ps, thr_g))
        T["PAIR"].append(trades(cur, pl, ps, {s_: thr_p.get(s_, np.inf) for s_ in cur.symbol.unique()}))
        print(yr, {k: len(v[-1]) for k, v in T.items()}, flush=True)
    res = {}
    for k, v in T.items():
        D = pd.concat(v, ignore_index=True)
        D.to_csv(OUT + f"r7_trades_{k}.csv", index=False)
        D["group"] = np.where(D.symbol.isin(LIVE5), "LIVE5", "NEW20")
        r = {}
        for grp in ("LIVE5", "NEW20"):
            for late, lab in ((False, "all_hours"), (True, "no_late_hours")):
                g = D[(D.group == grp) & (~D.hour.isin(LATE) if late else True)]
                r[f"{grp}|{lab}"] = {"all": wstat(g), "2016-20": wstat(g[g.year <= 2020]), "2021-26": wstat(g[g.year >= 2021]),
                                     "years_pos": f"{int((g.groupby('year').bp.mean() > 0).sum())}/{g.year.nunique()}"}
        pp = D[~D.hour.isin(LATE)].groupby(["symbol"]).apply(lambda g: pd.Series({"n": len(g), "bp": g.bp.mean(), "first": g[g.year <= 2020].bp.mean(),
                                                                                     "second": g[g.year >= 2021].bp.mean()}), include_groups=False).round(2)
        r["per_pair_no_late"] = pp.sort_values("bp").to_dict(orient="index")
        r["new20_pairs_positive_both_halves"] = [s_ for s_, x in pp.iterrows() if s_ not in LIVE5 and x["first"] > 0 and x["second"] > 0]
        res[k] = r
        for kk, x in r.items():
            if "|" in kk:
                print(k, kk, x["all"], "16-20", x["2016-20"].get("bp"), "21-26", x["2021-26"].get("bp"), "yrs+", x["years_pos"])
        print(k, "NEW20 positive in both halves:", r["new20_pairs_positive_both_halves"])
    json.dump(res, open(OUT + "r7_results.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
