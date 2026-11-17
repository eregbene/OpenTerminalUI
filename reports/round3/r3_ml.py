"""Round 3, areas E (nonlinear / ML), G (opportunity scoring) and H (portfolio). Pre-registered in PREREG.md:
fixed hyper-parameters, no tuning. P1 = train DISC (2012-18) -> test VAL (2019-22); P2 = train DISC+VAL -> test LOCK
(2023-26). Training rows: every 6th hour, purge 48h before the test start. Target: 12h gross move / hourly vol (y);
classifiers: sign of the 12h move. Trades: top 2% |signal| (threshold from the training predictions), one open trade
per pair (12h), net of the real spread + 0.3 bp slippage + 0.25 bp swap.
Models: OWN = candles-only HGB (round-1 baseline) | R3 = HGB on all round-3 features | R3_ADD = same HGB with no feature
interactions (interaction value = R3 - R3_ADD) | LOGIT | RF | TREE (depth 4, rules printed).
Scoring prototype: p_dir (HGB classifier, calibration checked), E|move| bp (HGB regressor), cost bp (entry spread +
extras), E[net] = (2 p_dir - 1) E|move| - cost, P(target) / P(stop) of the 1-ATR / 2-ATR bracket (HGB classifiers),
score = percentile of E[net] in the training predictions. Decision: TRADE (E[net] > 0 and score >= 90), MONITOR
(top-10% direction confidence but E[net] <= 0), REJECT (rest).
Portfolio: R3 trades and the strongest-vs-weakest rule: all pairs / caps (max 4 open, max 2 per currency) / daily P&L,
Sharpe, max drawdown, currency concentration.
    source reports/round3/r3env.sh && python reports/round3/r3_ml.py
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.cluster import KMeans
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.tree import DecisionTreeClassifier, export_text

import r3_tests as T3

R3 = T3.R3
EXTRA = 0.3 + 0.25
OWN = ["ret_1", "ret_3", "ret_6", "ret_12", "ret_24", "ret_72", "ret_120", "body_0", "body_1", "body_2", "uwick", "lwick", "streak",
       "rpos_24", "rpos_120", "dhi_120", "dlo_120", "pos_prev_day", "pos_today", "pday_ret", "pos_pweek", "wk_gap", "hour", "dow",
       "days_to_month_end", "day_of_month", "base_id", "quote_id"]  # = mi_run.OWN (round-1 baseline)
NEWF = ["vol_ratio", "vol_pct", "compress6", "size_0", "size_1", "pday_rng", "nr7", "vol", "atr6_48", "bbw_pct", "bb_out", "z24",
        "er_24", "er_120", "ac1_240", "rpos_480", "st_tr10", "st_tr50", "st_age10", "st_age50", "st_bos10", "st_choch10", "st_bos50",
        "st_choch50", "sw_pd", "sw_pw", "sw_asia", "sw_lon", "sw_eq", "sw3", "sw12", "brk_pd", "brk_pw", "brk_asia", "brk_lon", "fb_pd",
        "fvg_new", "fvg_rt", "fvg_size", "fvg_disp", "mtf_h4", "mtf_d1", "s_tokyo", "s_london", "s_ny", "s_overlap", "lon_open",
        "ny_open", "rollover", "month_end", "week_open", "rk_base_24", "rk_quote_24", "rk_gap_24", "rk_base_120", "rk_quote_120",
        "rk_gap_120", "str_diff_24", "str_diff_120", "spr"]
ALLF = OWN + NEWF
HGB = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=1000, l2_regularization=1.0, random_state=0)
PROT = {"P1": ((2012, 2018), (2019, 2022)), "P2": ((2012, 2022), (2023, 2026))}
NJ = max(1, (os.cpu_count() or 4) // 2)  # half the cores: the live engine keeps the rest


def wstat(r: np.ndarray, wk: np.ndarray) -> dict:
    if len(r) < 30:
        return {"n": int(len(r))}
    s = pd.Series(r).groupby(wk).sum()
    t = s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    return {"n": int(len(r)), "bp": round(float(r.mean()), 2), "t": round(float(t), 2), "p": round(float(stats.t.sf(t, len(s) - 1)), 4),
            "win": round(float((r > 0).mean()), 3), "pf": round(float(pos / neg), 3) if neg > 0 else None}


def ic_by_year(p, y, yr) -> float:
    return float(np.mean([stats.spearmanr(p[yr == Y], y[yr == Y]).statistic for Y in np.unique(yr)]))


def trades(d, idx, sig, thr, sym_start):
    k = idx[np.abs(sig) > thr]
    sg = pd.Series(sig, index=idx)
    k = T3.take(sym_start, k, 12)
    s = np.sign(sg.loc[k].to_numpy())
    r = np.where(s > 0, d.long_12.to_numpy()[k], d.short_12.to_numpy()[k]) - EXTRA
    return k, s, r


def main() -> None:
    d = T3.load()
    d["y"] = (d.long_12 - d.short_12) / 2 / d.vol
    d["up"] = (d.y > 0).astype(int)
    d["mv"] = ((d.long_12 - d.short_12) / 2).abs()  # gross |12h move| bp
    d["cost"] = d.spr + EXTRA  # estimate known at entry (entry spread, same at exit)
    ok = np.isfinite(d.y) & np.isfinite(d.long_12) & np.isfinite(d.b12_l)
    d = d[ok].reset_index(drop=True)
    sym = d.symbol.to_numpy()
    sym_start = np.flatnonzero(np.r_[True, sym[1:] != sym[:-1]])[np.cumsum(np.r_[True, sym[1:] != sym[:-1]]) - 1]
    yr, wk, tt = d.year.to_numpy(), d.wk.to_numpy(), d.t.to_numpy()
    X = d[ALLF].astype(np.float32)
    res, pf_trades, score_rows = {}, [], []
    for P, ((a, b), (c, e)) in PROT.items():
        cut = np.datetime64(f"{c}-01-01") - np.timedelta64(48, "h")
        tr = np.flatnonzero((yr >= a) & (yr <= b) & (tt < cut))[::6]
        te = np.flatnonzero((yr >= c) & (yr <= e))
        y, up = d.y.to_numpy(), d.up.to_numpy()
        R = {}
        fits = {
            "OWN": (HistGradientBoostingRegressor(**HGB), OWN, "reg"),
            "R3": (HistGradientBoostingRegressor(**HGB), ALLF, "reg"),
            "R3_ADD": (HistGradientBoostingRegressor(**HGB, interaction_cst="no_interactions"), ALLF, "reg"),
            "LOGIT": (make_pipeline(StandardScaler(), LogisticRegression(C=0.1, max_iter=500)), ALLF, "clf"),
            "RF": (RandomForestClassifier(n_estimators=200, max_depth=8, min_samples_leaf=500, max_samples=0.3, n_jobs=NJ, random_state=0), ALLF, "clf"),
            "HGBC": (HistGradientBoostingClassifier(**HGB), ALLF, "clf"),
        }
        med = X.iloc[tr].median()
        preds = {}
        for name, (m, cols, kind) in fits.items():
            Xtr, Xte = X.iloc[tr][cols], X.iloc[te][cols]
            if name in ("LOGIT", "RF"):
                Xtr, Xte = Xtr.fillna(med[cols]), Xte.fillna(med[cols])
            if kind == "reg":
                m.fit(Xtr, y[tr])
                ptr, pte = m.predict(Xtr), m.predict(Xte)
                auc = None
            else:
                m.fit(Xtr, up[tr])
                ptr, pte = m.predict_proba(Xtr)[:, 1] - 0.5, m.predict_proba(Xte)[:, 1] - 0.5
                auc = round(float(roc_auc_score(up[te], pte)), 4)
            thr = np.quantile(np.abs(ptr[yr[tr] == b]), 0.98)
            k, s, r = trades(d, te, pte, thr, sym_start)
            preds[name] = pte
            R[name] = {"IC": round(ic_by_year(pte, y[te], yr[te]), 4), "AUC": auc, "trades": wstat(r, wk[k]),
                       "trades_by_year": pd.Series(r).groupby(yr[k]).mean().round(2).to_dict()}
            if name in ("R3", "OWN"):
                pf_trades.append(pd.DataFrame({"P": P, "model": name, "i": k, "s": s, "r": r}))
            print(P, name, R[name]["IC"], auc, R[name]["trades"], flush=True)
        R["interaction_value_IC"] = round(R["R3"]["IC"] - R["R3_ADD"]["IC"], 4)
        R["R3_minus_OWN_IC"] = round(R["R3"]["IC"] - R["OWN"]["IC"], 4)
        # interpretable tree
        tree = DecisionTreeClassifier(max_depth=4, min_samples_leaf=5000, random_state=0).fit(X.iloc[tr][ALLF].fillna(med), up[tr])
        R["TREE_AUC"] = round(float(roc_auc_score(up[te], tree.predict_proba(X.iloc[te][ALLF].fillna(med))[:, 1])), 4)
        R["TREE_rules"] = export_text(tree, feature_names=ALLF, decimals=2)
        imp = pd.Series(fits["RF"][0].feature_importances_, index=ALLF).sort_values(ascending=False)
        R["RF_top15"] = imp.head(15).round(4).to_dict()
        # regimes (KMeans on DISC-era training rows): does the R3 signal work everywhere?
        rc = ["vol_pct", "er_120", "atr6_48", "bbw_pct", "ac1_240"]
        sc = StandardScaler().fit(X.iloc[tr][rc].fillna(med[rc]))
        km = KMeans(6, n_init=5, random_state=0).fit(sc.transform(X.iloc[tr][rc].fillna(med[rc])))
        lab = km.predict(sc.transform(X.iloc[te][rc].fillna(med[rc])))
        R["regime_IC"] = {int(g): {"share": round(float((lab == g).mean()), 3), "IC": round(float(stats.spearmanr(preds["R3"][lab == g], y[te][lab == g]).statistic), 4),
                                   "centre": dict(zip(rc, np.round(sc.inverse_transform(km.cluster_centers_[g:g + 1])[0], 2)))} for g in range(6)}
        # ---- scoring prototype (G) ----
        Xtr, Xte = X.iloc[tr], X.iloc[te]
        mv = HistGradientBoostingRegressor(**HGB).fit(Xtr, d.mv.to_numpy()[tr])
        pdir = preds["HGBC"] + 0.5
        emv = mv.predict(Xte)
        cost = d.cost.to_numpy()[te]
        enet = (2 * np.abs(pdir - 0.5)) * emv - cost
        side = np.where(pdir >= 0.5, 1, -1)
        bl, bs = d.b12_l.to_numpy(), d.b12_s.to_numpy()
        tp_l, sl_l = (bl >= 1.5).astype(int), (bl <= -0.9).astype(int)
        tp_s, sl_s = (bs >= 1.5).astype(int), (bs <= -0.9).astype(int)
        ptp_l = HistGradientBoostingClassifier(**HGB).fit(Xtr, tp_l[tr]).predict_proba(Xte)[:, 1]
        ptp_s = HistGradientBoostingClassifier(**HGB).fit(Xtr, tp_s[tr]).predict_proba(Xte)[:, 1]
        psl_l = HistGradientBoostingClassifier(**HGB).fit(Xtr, sl_l[tr]).predict_proba(Xte)[:, 1]
        psl_s = HistGradientBoostingClassifier(**HGB).fit(Xtr, sl_s[tr]).predict_proba(Xte)[:, 1]
        ptp, psl = np.where(side > 0, ptp_l, ptp_s), np.where(side > 0, psl_l, psl_s)
        realised = np.where(side > 0, d.long_12.to_numpy()[te], d.short_12.to_numpy()[te]) - EXTRA
        tp_real = np.where(side > 0, tp_l[te], tp_s[te])
        sl_real = np.where(side > 0, sl_l[te], sl_s[te])
        # training-side reference for the score percentile
        enet_tr = (2 * np.abs(fits["HGBC"][0].predict_proba(Xtr)[:, 1] - 0.5)) * mv.predict(Xtr) - d.cost.to_numpy()[tr]
        score = np.searchsorted(np.sort(enet_tr), enet) / len(enet_tr) * 100
        conf_cut = np.quantile(np.abs(fits["HGBC"][0].predict_proba(Xtr)[:, 1] - 0.5), 0.9)
        band = np.where((enet > 0) & (score >= 90), "TRADE", np.where(np.abs(pdir - 0.5) >= conf_cut, "MONITOR", "REJECT"))
        S = pd.DataFrame({"pdir": pdir, "side": side, "emv": emv, "mv": d.mv.to_numpy()[te], "cost": cost, "enet": enet, "score": score,
                          "ptp": ptp, "psl": psl, "tp": tp_real, "sl": sl_real, "net": realised, "band": band, "wk": wk[te], "year": yr[te]})
        S["dec"] = pd.qcut(S.score.rank(method="first"), 10, labels=False)
        up_te = up[te]
        cal = pd.DataFrame({"p": pdir, "u": up_te}).groupby(pd.qcut(pdir, 10, labels=False, duplicates="drop")).mean().round(4)
        R["G_calibration_direction"] = cal.to_dict(orient="list")
        R["G_move_IC"] = round(float(stats.spearmanr(emv, S.mv).statistic), 4)
        R["G_ptp_AUC"] = round(float(roc_auc_score(tp_real, ptp)), 4)
        R["G_psl_AUC"] = round(float(roc_auc_score(sl_real, psl)), 4)
        R["G_score_deciles"] = S.groupby("dec").agg(score=("score", "mean"), enet=("enet", "mean"), net=("net", "mean"), tp=("tp", "mean"),
                                                     ptp=("ptp", "mean"), sl=("sl", "mean"), psl=("psl", "mean")).round(3).to_dict(orient="list")
        bands = {}
        for bn, g in S.groupby("band"):
            # every hour in a band (overlapping) -> clustered by week; descriptive of the band's average opportunity
            bands[bn] = {"share": round(len(g) / len(S), 4), **wstat(g.net.to_numpy(), g.wk.to_numpy())}
        R["G_bands_all_hours"] = bands
        tk = np.flatnonzero(band == "TRADE")
        kk = T3.take(sym_start, te[tk], 12)
        pos = pd.Series(np.arange(len(te)), index=te).loc[kk].to_numpy()
        R["G_TRADE_nonoverlap"] = wstat(realised[pos], wk[te][pos])
        R["G_TRADE_by_year"] = pd.Series(realised[pos]).groupby(yr[te][pos]).mean().round(2).to_dict()
        res[P] = R
    # ---- portfolio (H) on the LOCK (P2) and VAL (P1) R3 trades + strongest-vs-weakest ----
    PT = pd.concat(pf_trades, ignore_index=True)
    d["tb"] = d.tb_24
    A = []
    for P, ((a, b), (c, e)) in PROT.items():
        te = np.flatnonzero((yr >= c) & (yr <= e) & (d.tb.to_numpy() != 0))
        k = T3.take(sym_start, te, 12)
        s = d.tb.to_numpy()[k]
        A.append(pd.DataFrame({"P": P, "model": "STRONG_WEAK", "i": k, "s": s,
                               "r": np.where(s > 0, d.long_12.to_numpy()[k], d.short_12.to_numpy()[k]) - EXTRA}))
    PT = pd.concat([PT, *A], ignore_index=True)
    PT["t"], PT["sym"] = tt[PT.i], sym[PT.i]
    port = {}
    for (P, mdl), g in PT.groupby(["P", "model"]):
        g = g.sort_values("t")
        for cap_name, (mx, mc) in {"uncapped": (99, 99), "cap4_ccy2": (4, 2)}.items():
            open_, kept = [], []
            for row in g.itertuples():
                open_ = [o for o in open_ if o[0] > row.t]
                ccys = [o[1] for o in open_] + [o[2] for o in open_]
                b_, q_ = row.sym[:3], row.sym[3:]
                if len(open_) >= mx or ccys.count(b_) >= mc or ccys.count(q_) >= mc:
                    continue
                open_.append((row.t + np.timedelta64(12, "h"), b_, q_))
                kept.append(row.Index)
            k = g.loc[kept]
            day = pd.Series(k.r.to_numpy(), index=pd.DatetimeIndex(k.t).floor("D")).groupby(level=0).sum()
            eq = day.cumsum()
            dd = float((eq - eq.cummax()).min()) if len(eq) else 0.0
            ccy = pd.concat([k.sym.str[:3], k.sym.str[3:]]).value_counts(normalize=True).round(3).head(4).to_dict()
            port[f"{P}|{mdl}|{cap_name}"] = {"trades": int(len(k)), "bp_per_trade": round(float(k.r.mean()), 2) if len(k) else None,
                                             "total_bp": round(float(k.r.sum()), 1), "sharpe_daily_ann": round(float(day.mean() / day.std() * np.sqrt(252)), 2) if len(day) > 2 else None,
                                             "max_dd_bp": round(dd, 1), "top_currency_share": ccy}
    res["H_portfolio"] = port
    json.dump(res, open(R3 + "r3_ml_results.json", "w"), indent=1, default=str)
    PT.to_csv(R3 + "r3_ml_trades.csv", index=False)
    for P in PROT:
        r = res[P]
        print(P, {k: r[k] for k in ("R3_minus_OWN_IC", "interaction_value_IC", "TREE_AUC", "G_move_IC", "G_ptp_AUC", "G_psl_AUC")})
        print(" bands", r["G_bands_all_hours"], "\n TRADE", r["G_TRADE_nonoverlap"])
    for k, v in port.items():
        print(k, v)


if __name__ == "__main__":
    main()
