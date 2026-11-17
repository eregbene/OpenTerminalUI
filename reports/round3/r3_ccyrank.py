"""Round 3, area A/H: reproduce the round-2 lead (model-predicted currency ranking -> strongest vs weakest) on the
round-3 split, with the same HGB settings as r3_ml (R3 features, target = 12h move / vol). P1 train 2012-18 ->
test 2019-22; P2 train 2012-22 -> test 2023-26. Currency score = mean prediction over its pairs (+ base, - quote).
Decision grid fixed in advance: hold h in {6, 12, 24, 48}; decisions every h hours from 00 UTC (non-overlapping);
for h = 12 also the 06/18 UTC grid. Trade the strongest/weakest pair if it is one of the 25, net of the real spread
+ slippage + swap (r3_tests.EXTRA). A second variant trades the 2 strongest vs 2 weakest (up to 4 pairs).
    source reports/round3/r3env.sh && python reports/round3/r3_ccyrank.py
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

import r3_ml as M
import r3_tests as T3

CCY = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]


def main() -> None:
    d = T3.load()
    d["y"] = (d.long_12 - d.short_12) / 2 / d.vol
    d = d[np.isfinite(d.y) & np.isfinite(d.long_12)].reset_index(drop=True)
    yr, tt = d.year.to_numpy(), d.t.to_numpy()
    X = d[M.ALLF].astype(np.float32)
    syms = set(d.symbol.unique())
    res, rows = {}, []
    for P, ((a, b), (c, e)) in M.PROT.items():
        cut = np.datetime64(f"{c}-01-01") - np.timedelta64(48, "h")
        tr = np.flatnonzero((yr >= a) & (yr <= b) & (tt < cut))[::6]
        te = np.flatnonzero((yr >= c) & (yr <= e))
        m = HistGradientBoostingRegressor(**M.HGB).fit(X.iloc[tr], d.y.to_numpy()[tr])
        D = d.iloc[te][["t", "symbol", "wk", "year"] + [f"{s}_{h}" for h in (6, 12, 24, 48) for s in ("long", "short")]].copy()
        D["pred"] = m.predict(X.iloc[te])
        sc = []
        for cc in CCY:
            s = np.where(D.symbol.str[:3] == cc, 1.0, np.where(D.symbol.str[3:] == cc, -1.0, np.nan))
            sc.append(pd.Series(D.pred.to_numpy() * s).groupby(D.t.to_numpy()).mean().rename(cc))
        S = pd.concat(sc, axis=1).dropna()
        Dk = D.set_index(["t", "symbol"])
        for h, grid in ((6, (0, 6, 12, 18)), (12, (0, 12)), (12, (6, 18)), (24, (0,)), (48, (0,))):
            ts = S.index[np.isin(pd.DatetimeIndex(S.index).hour, grid)]
            if h == 48:
                ts = ts[(pd.DatetimeIndex(ts).dayofyear % 2) == 0]
            for variant, k in (("1v1", 1), ("2v2", 2)):
                for t in ts:
                    r_ = S.loc[t].sort_values(ascending=False)
                    top, bot = list(r_.index[:k]), list(r_.index[-k:])
                    for hi in top:
                        for lo in bot:
                            pair, sgn = (hi + lo, 1) if hi + lo in syms else ((lo + hi, -1) if lo + hi in syms else (None, 0))
                            if pair is None or (t, pair) not in Dk.index:
                                continue
                            x = Dk.loc[(t, pair)]
                            net = (x[f"long_{h}"] if sgn > 0 else x[f"short_{h}"]) - T3.EXTRA[h]
                            if np.isfinite(net):
                                rows.append({"P": P, "h": h, "grid": "/".join(map(str, grid)), "variant": variant, "t": t, "pair": pair,
                                             "net": float(net), "wk": int(x.wk), "year": int(x.year)})
        print(P, "done", flush=True)
    R = pd.DataFrame(rows)
    for (P, h, g, v), x in R.groupby(["P", "h", "grid", "variant"]):
        res[f"{P}|{h}h|{g}|{v}"] = {**M.wstat(x.net.to_numpy(), x.wk.to_numpy()), "by_year": x.groupby("year").net.mean().round(2).to_dict(),
                                    "top_pairs": x.pair.value_counts().head(4).to_dict()}
    R.to_csv(T3.R3 + "r3_ccyrank_trades.csv", index=False)
    json.dump(res, open(T3.R3 + "r3_ccyrank_results.json", "w"), indent=1, default=str)
    for k, v in res.items():
        print(k, {kk: v.get(kk) for kk in ("n", "bp", "t", "p", "pf")}, v["by_year"])


if __name__ == "__main__":
    main()
