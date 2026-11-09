"""Round 3, area F: the existing strategy trades (scratchpad im_out/features.pkl, 9,279 trades, R net of costs, entry-
time features from im_features.py) + round-3 panel conditions at the last H1 bar closed at or before entry.
Pre-registered (PREREG.md): conditions are binary and fixed below; filters are chosen on DISC trades (<= 2018) only:
the <= 3 conditions with |t| > 2 (Welch, n >= 200 each side), applied as keep-if-good / drop-if-bad; then judged on
VAL (2019-22) and LOCK (2023-26) by expectancy, profit factor, max drawdown and total R (not win rate).
    source reports/round3/r3env.sh && python reports/round3/r3_trades.py
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

R3 = "D:/Devops/trading/OpenTerminalUI/reports/round3/"
SP = "D:/ClaudeTemp/claude/14620cf7b9ae7a95/c018d285-be3f-456e-a306-37977c08ec0a/scratchpad/"
PANEL = ["t", "symbol", "rk_gap_24", "rk_gap_120", "vol_pct", "atr6_48", "bbw_pct", "er_120", "st_tr50", "st_tr10", "mtf_h4", "mtf_d1",
         "s_london", "s_ny", "s_overlap", "s_tokyo", "sw_pd", "sw_asia", "fvg_rt", "spr", "ret_24", "vol", "vol_ratio", "compress6"]


def perf(r: np.ndarray) -> dict:
    if len(r) < 20:
        return {"n": int(len(r))}
    eq = np.cumsum(r)
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    return {"n": int(len(r)), "expR": round(float(r.mean()), 4), "pf": round(float(pos / neg), 3) if neg else None,
            "totalR": round(float(r.sum()), 1), "maxDD_R": round(float((eq - np.maximum.accumulate(eq)).min()), 1),
            "win": round(float((r > 0).mean()), 3), "t": round(float(r.mean() / (r.std(ddof=1) / np.sqrt(len(r)))), 2)}


def main() -> None:
    f = pd.read_pickle(SP + "im_out/features.pkl")  # our own file (im_features.py)
    f = f.sort_values("t_entry").reset_index(drop=True)
    f["t"] = pd.to_datetime((f.t_entry // 3600) * 3600, unit="s")  # close of the last H1 bar finished at entry
    p = pd.read_pickle(R3 + "r3_panel.pkl")[PANEL]  # our own file
    f = f.merge(p, on=["t", "symbol"], how="left")
    d = f.dir
    C = {
        "ccy strength agrees (24h gap>=3)": d * f.rk_gap_24 >= 3,
        "ccy strength against (24h gap<=-3)": d * f.rk_gap_24 <= -3,
        "ccy strength agrees (120h gap>=3)": d * f.rk_gap_120 >= 3,
        "vol percentile > 0.8": f.vol_pct > 0.8,
        "vol percentile < 0.2": f.vol_pct < 0.2,
        "ATR6/48 > 1.2 (expansion)": f.atr6_48 > 1.2,
        "Bollinger width pct < 0.2 (squeeze)": f.bbw_pct < 0.2,
        "trend efficiency er_120 > 0.3": f.er_120 > 0.3,
        "swing structure agrees": f.st_tr50 == d,
        "internal structure agrees": f.st_tr10 == d,
        "H4 and D1 trend agree": (f.mtf_h4 == d) & (f.mtf_d1 == d),
        "H4 and D1 trend against": (f.mtf_h4 == -d) & (f.mtf_d1 == -d),
        "London/NY overlap": f.s_overlap == 1,
        "Tokyo only": (f.s_tokyo == 1) & (f.s_london == 0),
        "New York": f.s_ny == 1,
        "liquidity sweep same side this bar": (f.sw_pd == d) | (f.sw_asia == d),
        "FVG first retest same side": f.fvg_rt == d,
        "spread > 1.5 bp": f.spr > 1.5,
        "24h move with trade > 1.5 sd": d * f.ret_24 > 1.5,
        "24h move against trade > 1.5 sd": d * f.ret_24 < -1.5,
        "M15/H1/H4 aligned (align3)": f.align3 == 1,
        "recent H1 CHoCH": f.choch_recent_H1 == 1,
        "stop-side previous-day level swept": f.swept_pd_stopside == 1,
        "FVG behind inside stop (H1)": f.fvg_behind_in_stop_H1 == 1,
        "H4 discount/premium correct side": f.discount_H4 < 0.5,
        "ADX H1 > 25": f.adx_H1 > 25,
        "compressed H1": f.compress_H1 < 1.0,
        "stop < 0.5 ATR": f.stop_atr < 0.5,
        "target > 3R": f.tp_r > 3,
    }
    per = np.where(f.year <= 2018, "DISC", np.where(f.year <= 2022, "VAL", "LOCK"))
    rows = []
    for k, m in C.items():
        m = m.fillna(False).to_numpy(bool)
        row = {"condition": k}
        for sp in ("DISC", "VAL", "LOCK"):
            a, b = f.r[(per == sp) & m], f.r[(per == sp) & ~m]
            t = stats.ttest_ind(a, b, equal_var=False).statistic if len(a) > 1 and len(b) > 1 else np.nan
            row.update({f"{sp}_n": int(len(a)), f"{sp}_expR_with": round(a.mean(), 4) if len(a) else np.nan,
                        f"{sp}_expR_without": round(b.mean(), 4) if len(b) else np.nan, f"{sp}_t": round(float(t), 2)})
        rows.append(row)
    Ct = pd.DataFrame(rows)
    ok = (Ct.DISC_n >= 200) & ((f[per == "DISC"].shape[0] - Ct.DISC_n) >= 200) & (Ct.DISC_t.abs() > 2)
    chosen = Ct[ok].reindex(Ct[ok].DISC_t.abs().sort_values(ascending=False).index).head(3)
    keep = np.ones(len(f), bool)
    for _, c in chosen.iterrows():
        m = C[c.condition].fillna(False).to_numpy(bool)
        keep &= m if c.DISC_t > 0 else ~m
    res = {"chosen_filters_on_DISC": chosen[["condition", "DISC_n", "DISC_expR_with", "DISC_expR_without", "DISC_t"]].to_dict(orient="records")}
    for sp in ("DISC", "VAL", "LOCK"):
        k = per == sp
        res[f"{sp}_all_trades"] = perf(f.r[k].to_numpy())
        res[f"{sp}_filtered"] = perf(f.r[k & keep].to_numpy())
    # ML on trade features: DISC -> LOCK (and DISC+VAL -> LOCK), expectancy of the top half vs all
    feats = [c for c in f.columns if c not in ("book", "symbol", "t_entry", "source", "r", "win", "exit_reason", "mfe_r", "mae_r", "year", "t", "session")]
    X = f[feats].apply(pd.to_numeric, errors="coerce").astype(float)
    ml = {}
    for name, trn, tst in (("DISC->VAL", per == "DISC", per == "VAL"), ("DISC+VAL->LOCK", per != "LOCK", per == "LOCK")):
        m = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, min_samples_leaf=100, random_state=0).fit(X[trn], (f.r[trn] > 0).astype(int))
        pr = m.predict_proba(X[tst])[:, 1]
        cut = np.median(m.predict_proba(X[trn])[:, 1])
        ml[name] = {"AUC": round(float(roc_auc_score((f.r[tst] > 0).astype(int), pr)), 4), "all": perf(f.r[tst].to_numpy()),
                    "top_half": perf(f.r[tst].to_numpy()[pr > cut]), "bottom_half": perf(f.r[tst].to_numpy()[pr <= cut])}
    res["ML_trade_filter"] = ml
    # descriptive: books, sessions, pairs, regimes, MFE of losers
    g = f.assign(per=per)
    res["by_book_period"] = g.groupby(["book", "per"]).r.agg(["count", "mean"]).round(3).reset_index().to_dict(orient="records")
    res["by_session"] = g.groupby("session").r.agg(["count", "mean"]).round(3).to_dict(orient="index")
    pair = g.groupby("symbol").r.agg(["count", "mean"]).round(3).sort_values("mean")
    res["pairs_worst5"], res["pairs_best5"] = pair.head(5).to_dict(orient="index"), pair.tail(5).to_dict(orient="index")
    g["vol_t"] = pd.qcut(g.vol_pct, 3, labels=["low", "mid", "high"])
    g["er_t"] = pd.qcut(g.er_120, 3, labels=["range", "mid", "trend"])
    rg = {}
    for (a, b), x in g.groupby(["vol_t", "er_t"], observed=True):
        rg[f"vol={a}|trend={b}"] = perf(x.sort_values("t_entry").r.to_numpy())
    res["regimes"] = rg
    los = g[g.r < 0]
    res["losers_reaching_+1R_first"] = round(float((los.mfe_r >= 1).mean()), 3)
    res["winners_mae_below_-0.5R"] = round(float((g[g.r > 0].mae_r <= -0.5).mean()), 3)
    Ct.to_csv(R3 + "r3_trade_conditions.csv", index=False)
    json.dump(res, open(R3 + "r3_trades_results.json", "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in res.items() if k not in ("by_book_period", "regimes")}, indent=1, default=str))
    print(Ct.sort_values("DISC_t").to_string())


if __name__ == "__main__":
    main()
