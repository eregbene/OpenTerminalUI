"""Round 11 (PREREG.md): Flow ML walk-forward BASE vs +POLICY (central-bank policy-rate features), same seeds.
Run from the OpenTerminalUI root:  python reports/round11/r11_policy.py   (read-only; writes reports/round11/)"""
from __future__ import annotations

import json
import pickle
import sys

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
from bensim.edge_miner import FEATURES, PATH  # noqa: E402

ROOT = "D:/Devops/trading/OpenTerminalUI/"
OUT = ROOT + "reports/round11/"
LIVE5 = ["EURUSD", "USDJPY", "USDCAD", "EURJPY", "CADJPY"]
AUD = ["AUDJPY", "AUDUSD", "EURAUD", "AUDNZD", "AUDCAD", "AUDCHF"]
SEEDS, H, LATE = (0, 7, 13), 6, {20, 21, 22, 23, 0}
GAPS = ["gap_level", "gap_d5", "gap_d20", "gap_d60"]
POL = ["pol_gap", "pol_d60", "exp_gap", "pol_last"]
USD_BP = {"EUR": 11.6, "GBP": 13.3, "NZD": 5.8, "CAD": 7.2, "AUD": 6.5, "USD": 10.0, "CHF": 12.5}


def add_macro(f: pd.DataFrame) -> pd.DataFrame:
    y = pd.read_csv(ROOT + "data/macro/yields_ext/yields_2y_plus.csv", index_col=0, parse_dates=True).ffill(limit=5).shift(1)
    p = pd.read_csv(ROOT + "data/macro/policy/policy_rates_daily.csv", index_col=0, parse_dates=True)
    p = p.reindex(pd.date_range(p.index.min(), p.index.max(), freq="D")).ffill().shift(1)  # 1-day lag
    last = p.diff().replace(0, np.nan).apply(np.sign).ffill()  # sign of each bank's latest move
    day = f.t.dt.normalize()
    for c in GAPS + POL:
        f[c] = np.nan
    for sym in f.symbol.unique():
        b, q, k = sym[:3], sym[3:], (f.symbol == sym).to_numpy()
        d = day[k]
        if b in y and q in y:
            g = y[b] - y[q]
            f.loc[k, "gap_level"] = g.reindex(d, method="ffill").to_numpy()
            for n in (5, 20, 60):
                f.loc[k, f"gap_d{n}"] = (g - g.shift(n)).reindex(d, method="ffill").to_numpy()
        pg = p[b] - p[q]
        f.loc[k, "pol_gap"] = pg.reindex(d).to_numpy()
        f.loc[k, "pol_d60"] = (pg - pg.shift(84)).reindex(d).to_numpy()  # ~60 business days in calendar days
        f.loc[k, "pol_last"] = (last[b] - last[q]).reindex(d).to_numpy()
        if b in y and q in y:
            e = (y[b] - p[b].reindex(y.index, method="ffill")) - (y[q] - p[q].reindex(y.index, method="ffill"))
            f.loc[k, "exp_gap"] = e.reindex(d, method="ffill").to_numpy()
    return f


def trades(df, pl, ps, thr):
    out = []
    for sym, g in df.assign(pl=pl, ps=ps).groupby("symbol"):
        busy = pd.Timestamp(0)
        for x in g.itertuples():
            if x.t < busy or x.t.hour in LATE or max(x.pl, x.ps) <= thr:
                continue
            side = "long" if x.pl >= x.ps else "short"
            r = getattr(x, f"{side}_{H}")
            if np.isfinite(r):
                out.append((sym, x.t, x.year, x.t.hour, side, r))
                busy = x.t + pd.Timedelta(hours=H)
    return pd.DataFrame(out, columns=["symbol", "t", "year", "hour", "side", "bp"])


def atr_flag(T: pd.DataFrame) -> pd.Series:
    out = pd.Series(np.nan, index=T.index)
    for s, g in T.groupby("symbol"):
        z = np.load(ROOT + f"data/bars/fxcm/{s}_H1.npz")
        r = pd.Series(z["h"] - z["l"])
        a = pd.Series((r.rolling(6).mean() / r.rolling(48).mean()).to_numpy(), index=pd.to_datetime(z["t"] + 3600, unit="s"))
        out[g.index] = a.reindex(a.index.union(g.t)).ffill().loc[g.t].to_numpy()
    return out


def stats_(g: pd.DataFrame) -> dict:
    if len(g) < 30:
        return {"n": len(g)}
    w = g.groupby(g.t.dt.to_period("W")).bp.sum()
    d = g.groupby(g.t.dt.normalize()).bp.sum().reindex(pd.bdate_range("2016-01-01", g.t.max()), fill_value=0.0)
    u = g.bp * g.symbol.str[:3].map(USD_BP)
    m = u.groupby(g.t.dt.to_period("M")).sum()
    c = m.cumsum()
    per = pd.cut(g.year, [2015, 2018, 2022, 2026], labels=["DISC", "VAL", "LOCK"])
    return {"n": len(g), "bp": round(g.bp.mean(), 2), "t_wk": round(w.mean() / (w.std(ddof=1) / np.sqrt(len(w))), 2),
            "sharpe": round(d.mean() / d.std() * np.sqrt(252), 2), **g.groupby(per, observed=False).bp.mean().round(2).to_dict(),
            "years_pos": int((g.groupby("year").bp.sum() > 0).sum()),
            **{f"usd_{L}lot": {"per_year": int(u.sum() * L / 10.8), "worst_month": int(m.min() * L), "max_dd": int((c - c.cummax()).min() * L)} for L in (1, 5, 7)}}


def main() -> None:
    f = add_macro(pickle.load(open(ROOT + str(PATH), "rb")))
    books = {"BASE": FEATURES + GAPS, "POLICY": FEATURES + GAPS + POL}
    T = {k: [] for k in books}
    for yr in range(2016, int(f.year.max()) + 1):
        tr = f[(f.year < yr - 1) & np.isfinite(f[f"long_{H}"]) & np.isfinite(f[f"short_{H}"])]
        prev, cur = f[f.year == yr - 1], f[f.year == yr]
        for name, feats in books.items():
            plp = psp = pl = ps = 0.0
            for s in SEEDS:
                mk = lambda yy: HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,  # noqa: E731
                                                              min_samples_leaf=2000, l2_regularization=1.0, random_state=s).fit(tr[feats], yy)
                ml_, ms_ = mk(np.clip(tr[f"long_{H}"], -300, 300)), mk(np.clip(tr[f"short_{H}"], -300, 300))
                plp, psp = plp + ml_.predict(prev[feats]) / len(SEEDS), psp + ms_.predict(prev[feats]) / len(SEEDS)
                pl, ps = pl + ml_.predict(cur[feats]) / len(SEEDS), ps + ms_.predict(cur[feats]) / len(SEEDS)
            T[name].append(trades(cur, pl, ps, float(np.quantile(np.maximum(plp, psp), 0.95))))
        print(yr, {k: len(v[-1]) for k, v in T.items()}, flush=True)
    res = {}
    for name, v in T.items():
        D = pd.concat(v, ignore_index=True)
        D["atr"] = atr_flag(D)
        D.to_csv(OUT + f"r11_trades_{name}.csv", index=False)
        for grp, sel in (("LIVE5", D.symbol.isin(LIVE5)), ("OTHER20", ~D.symbol.isin(LIVE5)), ("AUD6", D.symbol.isin(AUD))):
            res[f"{name}|{grp}"] = stats_(D[sel])
            res[f"{name}|{grp}|ATR1.2"] = stats_(D[sel & (D.atr > 1.2)])
    verdict = {}
    for grp in ("LIVE5", "OTHER20"):
        b, p = res[f"BASE|{grp}"], res[f"POLICY|{grp}"]
        verdict[grp] = {"a_periods": all(p[x] > b[x] for x in ("DISC", "VAL", "LOCK")), "b_sharpe": p["sharpe"] > b["sharpe"], "c_t2": p["t_wk"] >= 2}
    res["verdict"] = verdict
    res["PASS"] = all(all(v.values()) for v in verdict.values())
    json.dump(res, open(OUT + "r11_results.json", "w"), indent=1, default=str)
    for k, v in res.items():
        print(k, v)


if __name__ == "__main__":
    main()
