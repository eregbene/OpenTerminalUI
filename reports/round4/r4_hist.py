"""Round 4 Parts 2, 3, 5, 8: historical forensic profile of each of the nine live strategies, one evaluation per
strategy on ITS OWN historical trade series (no universal model). Read-only over existing research outputs:
  fib618 / imbalance / enigma / bennett : scratchpad im_out trades (FXCM M5 2012-2020 + IC M5 2021-2026, R net of costs)
                                          + im features (MFE/MAE, structure) + lifecycle variants (lc_out)
  sflow    : data/sflow_fxcm_ours_2012_2021.csv + data/sflow_5y.csv, entry kind A (live), R net
  parallax : data/parallax_untouched.csv, variant 'author', target -100 (live), R net
  cta      : data/cta_untouched.csv, variant v2 (live), R net
  flow_ml  : data/macro/ensemble_trades.csv (walk-forward 2016+, 6h, bp net of the real spread)
  flow_am  : rebuilt here from FXCM EURUSD H1: sell at the 09:00 UTC bar open (bid), buy back at the 12:00 bar open
             (ask = bid + that hour's real spread), weekdays, - 0.3 bp slippage (bp net)
Pre-registered (written before running): periods DISC <= 2018 (flow_ml: 2016-18), VAL 2019-22, LOCK 2023-26.
Intelligence filters: the SAME fixed condition list per strategy family (below), chosen on DISC only (|t| > 2,
n >= 50 each side, Benjamini-Hochberg over all strategy x condition DISC tests), then judged on VAL and LOCK.
Stats: week-clustered t, block-bootstrap CI (bensim.validate), PSR and DSR (trials = every experiment logged in
rounds 1-4, 2,200 conservative), PBO (CSCV) across the variants each family has on disk.
    python reports/round4/r4_hist.py
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
from bensim import validate as V  # noqa: E402

OUT = "D:/Devops/trading/OpenTerminalUI/reports/round4/"
SP = "D:/ClaudeTemp/claude/14620cf7b9ae7a95/c018d285-be3f-456e-a306-37977c08ec0a/scratchpad/"
D = "D:/Devops/trading/OpenTerminalUI/data/"
TRIALS = 2200
PANEL = ["t", "symbol", "vol_pct", "er_120", "atr6_48", "mtf_h4", "mtf_d1", "st_tr50", "st_tr10", "rk_gap_24", "s_tokyo", "s_london",
         "s_ny", "s_overlap", "spr", "bbw_pct", "ret_24"]


def per(y: pd.Series, first_disc_year: int = 2012) -> np.ndarray:
    return np.where(y <= 2018, "DISC", np.where(y <= 2022, "VAL", "LOCK"))


def load() -> dict[str, pd.DataFrame]:
    out = {}
    im = pd.concat([pd.read_csv(SP + "im_out/trades_fxcm.csv"), pd.read_csv(SP + "im_out/trades_ic.csv")], ignore_index=True)
    feat = pd.read_pickle(SP + "im_out/features.pkl")[["book", "symbol", "t_entry", "mfe_r", "mae_r", "stop_atr", "tp_r"]]  # our own file
    im = im.merge(feat, on=["book", "symbol", "t_entry"], how="left")
    for b, g in im.groupby("book"):
        out[b] = pd.DataFrame({"symbol": g.symbol, "dir": g.dir, "t": pd.to_datetime(g.t_entry, unit="s"), "t_exit": pd.to_datetime(g.t_exit, unit="s"),
                               "r": g.r, "exit_reason": g.exit_reason, "mfe": g.mfe_r, "mae": g.mae_r, "unit": "R", "src": g.source})
    sf = pd.concat([pd.read_csv(D + "sflow_fxcm_ours_2012_2021.csv").assign(src="fxcm"), pd.read_csv(D + "sflow_5y.csv").assign(src="ic")])
    sf = sf[sf.kind == "A"]
    out["sflow"] = pd.DataFrame({"symbol": sf.symbol, "dir": sf.dir, "t": pd.to_datetime(sf.fill_t, unit="s"), "t_exit": pd.to_datetime(sf.exit_t, unit="s"),
                                 "r": sf.r, "exit_reason": sf.how, "cost_r": sf.cost_r, "unit": "R", "src": sf.src})
    px = pd.read_csv(D + "parallax_untouched.csv")
    px = px[(px.variant == "author") & (px.target == -100.0)]
    out["parallax"] = pd.DataFrame({"symbol": px.symbol, "dir": px.dir, "t": pd.to_datetime(px.t_fill, unit="s"), "t_exit": pd.to_datetime(px.t_exit, unit="s"),
                                    "r": px.r, "unit": "R", "src": "fxcm"})
    ct = pd.read_csv(D + "cta_untouched.csv")
    ct = ct[ct.variant == "v2"]
    out["cta"] = pd.DataFrame({"symbol": ct.symbol, "dir": ct.dir, "t": pd.to_datetime(ct.t_fill, unit="s"), "t_exit": pd.to_datetime(ct.t_exit, unit="s"),
                               "r": ct.r, "unit": "R", "src": ct.source})
    e = pd.read_csv(D + "macro/ensemble_trades.csv", parse_dates=["t"])
    out["flow_ml"] = pd.DataFrame({"symbol": e.symbol, "dir": np.where(e.side == "long", 1, -1), "t": e.t, "t_exit": e.t + pd.Timedelta(hours=6),
                                   "r": e.bp, "unit": "bp", "src": "fxcm"})
    z = np.load(D + "bars/fxcm/EURUSD_H1.npz")
    b = pd.DataFrame({"t": pd.to_datetime(z["t"], unit="s"), "o": z["o"], "c": z["c"], "spr": z["spr"]})
    b["prev_spr"] = b.spr.shift(1)  # spread at the close of the 08:00 / 11:00 bar = at 09:00 / 12:00
    b["d"] = b.t.dt.floor("D")
    a9 = b[b.t.dt.hour == 9].set_index("d")
    a12 = b[b.t.dt.hour == 12].set_index("d")
    j = a9[["t", "o"]].join(a12[["o", "prev_spr"]], rsuffix="_12", how="inner")
    j = j[j.t.dt.dayofweek < 5]
    net = (j.o - (j.o_12 + j.prev_spr)) / j.o * 1e4 - 0.3
    out["flow_am"] = pd.DataFrame({"symbol": "EURUSD", "dir": -1, "t": j.t.to_numpy(), "t_exit": j.t.to_numpy() + np.timedelta64(3, "h"), "r": net.to_numpy(),
                                   "unit": "bp", "src": "fxcm"})
    for k, v in out.items():
        v["year"] = v.t.dt.year
        v["per"] = per(v.year)
        out[k] = v.dropna(subset=["r"]).sort_values("t").reset_index(drop=True)
    return out


def wk_t(r: np.ndarray, t: pd.Series) -> tuple[float, float]:
    if len(r) < 20:
        return np.nan, np.nan
    s = pd.Series(r).groupby((t.astype("int64") // 10**9 // (7 * 86400)).to_numpy()).sum()
    tt = s.mean() / (s.std(ddof=1) / np.sqrt(len(s))) if s.std() > 0 else 0.0
    return float(tt), float(stats.t.sf(tt, len(s) - 1))


def perf(g: pd.DataFrame) -> dict:
    r = g.r.to_numpy()
    if len(r) < 10:
        return {"n": int(len(r))}
    eq = np.cumsum(r)
    pos, neg = r[r > 0].sum(), -r[r < 0].sum()
    t, p = wk_t(r, g.t)
    hold = (g.t_exit - g.t).dt.total_seconds() / 3600
    return {"n": int(len(r)), "exp": round(float(r.mean()), 4), "win": round(float((r > 0).mean()), 3), "pf": round(float(pos / neg), 3) if neg else None,
            "total": round(float(r.sum()), 1), "max_dd": round(float((eq - np.maximum.accumulate(eq)).min()), 1), "t": round(t, 2), "p": round(p, 4),
            "avg_hold_h": round(float(hold.mean()), 1)}


_P: list = []


def panel_join(T: pd.DataFrame) -> pd.DataFrame:
    """Round-3 panel row of the last H1 bar closed at or before entry (panel t = bar close)."""
    if not _P:
        _P.append(pd.read_pickle("D:/Devops/trading/OpenTerminalUI/reports/round3/r3_panel.pkl")[PANEL].rename(columns={"t": "th"}))  # our own file
    return T.assign(th=T.t.dt.floor("h")).merge(_P[0], on=["th", "symbol"], how="left")


def conditions(T: pd.DataFrame, name: str) -> dict[str, pd.Series]:
    d = T.dir
    C = {"vol pct > 0.8": T.vol_pct > 0.8, "vol pct < 0.2": T.vol_pct < 0.2, "ATR expanding (6/48 > 1.2)": T.atr6_48 > 1.2,
         "trending (er_120 > 0.3)": T.er_120 > 0.3, "ranging (er_120 < 0.1)": T.er_120 < 0.1, "H4+D1 trend with trade": (T.mtf_h4 == d) & (T.mtf_d1 == d),
         "H4+D1 trend against trade": (T.mtf_h4 == -d) & (T.mtf_d1 == -d), "swing structure with trade": T.st_tr50 == d,
         "ccy strength with trade (gap >= 3)": d * T.rk_gap_24 >= 3, "ccy strength against (gap <= -3)": d * T.rk_gap_24 <= -3,
         "London/NY overlap": T.s_overlap == 1, "Tokyo only": (T.s_tokyo == 1) & (T.s_london == 0), "spread > 1.5 bp": T.spr > 1.5,
         "24h move with trade > 1 sd": d * T.ret_24 > 1, "24h move against trade > 1 sd": d * T.ret_24 < -1}
    if name == "fib618":
        C["gold (XAUUSD)"] = T.symbol == "XAUUSD"
    if name in ("parallax", "cta"):
        C["Bollinger squeeze (bbw pct < 0.2)"] = T.bbw_pct < 0.2
    if name == "flow_am":
        C = {k: v for k, v in C.items() if not k.startswith(("London", "Tokyo"))}
        for k_, dw in (("Monday", 0), ("Friday", 4)):
            C[k_] = T.t.dt.dayofweek == dw
    return C


def session(T: pd.DataFrame) -> pd.Series:
    return np.where(T.s_overlap == 1, "overlap", np.where(T.s_london == 1, "london", np.where(T.s_ny == 1, "new_york", np.where(T.s_tokyo == 1, "tokyo", "late"))))


def main() -> None:
    S = load()
    res, ctests = {}, []
    for name, T in S.items():
        T = panel_join(T)
        T["session"] = session(T)
        T["vol_t"] = pd.cut(T.vol_pct, [0, 1 / 3, 2 / 3, 1.01], labels=["low", "mid", "high"])
        T["trend_t"] = pd.cut(T.er_120, [-1, 0.1, 0.25, 2], labels=["range", "mid", "trend"])
        R = {"unit": T.unit.iloc[0], "span": f"{T.t.min().date()} .. {T.t.max().date()}", "all": perf(T)}
        R.update({p: perf(T[T.per == p]) for p in ("DISC", "VAL", "LOCK")})
        R["by_year"] = T.groupby("year").r.mean().round(3).to_dict()
        R["years_positive"] = f"{int((T.groupby('year').r.mean() > 0).sum())}/{T.year.nunique()}"
        rec = T[T.year >= 2024]
        R["recent_2024_26"] = perf(rec)
        pr = T.groupby("symbol").r.agg(["count", "mean"]).query("count >= 30").sort_values("mean")
        R["worst_pairs"], R["best_pairs"] = pr.head(3).round(3).to_dict(orient="index"), pr.tail(3).round(3).to_dict(orient="index")
        R["pairs_positive"] = f"{int((pr['mean'] > 0).sum())}/{len(pr)}"
        ss = T.groupby("session").r.agg(["count", "mean"]).query("count >= 30").sort_values("mean")
        R["sessions"] = ss.round(3).to_dict(orient="index")
        rg = T.groupby(["vol_t", "trend_t"], observed=True).r.agg(["count", "mean"]).query("count >= 30").sort_values("mean")
        R["regimes_worst"], R["regimes_best"] = {f"{a}/{b}": v for (a, b), v in rg.head(2).round(3).to_dict(orient="index").items()}, \
                                                {f"{a}/{b}": v for (a, b), v in rg.tail(2).round(3).to_dict(orient="index").items()}
        # statistics: PSR / DSR on per-trade returns, block bootstrap of the daily series
        r = T.r.to_numpy()
        sr = r.mean() / r.std(ddof=1)
        sk, ku = float(stats.skew(r)), float(stats.kurtosis(r, fisher=False))
        R["psr_vs_0"] = round(V.psr(sr, len(r), sk, ku), 4)
        sr_star = V.dsr_threshold(list(np.random.default_rng(0).normal(0, 1 / np.sqrt(len(r)), 50)), TRIALS)  # unskilled-trial SR spread
        R["dsr"] = round(V.psr(sr, len(r), sk, ku, sr_star), 4)
        daily = T.groupby(T.t_exit.dt.floor("D")).r.sum()
        R["bootstrap"] = V.block_bootstrap(daily.to_numpy())
        if T.unit.iloc[0] == "R" and "mfe" in T and T.mfe.notna().sum() > 50:
            L, W = T[T.r < 0], T[T.r > 0]
            R["management"] = {"median_mfe_R": round(float(T.mfe.median()), 2), "median_mae_R": round(float(T.mae.median()), 2),
                               "losers_never_above_0.3R (wrong direction/entry)": round(float((L.mfe < 0.3).mean()), 3),
                               "losers_that_reached_+1R (management)": round(float((L.mfe >= 1).mean()), 3),
                               "winners_first_-0.5R_against (tight-stop risk)": round(float((W.mae <= -0.5).mean()), 3),
                               "winner_capture_r_over_mfe": round(float((W.r / W.mfe.replace(0, np.nan)).median()), 3)}
        if "exit_reason" in T:
            R["exit_mix"] = T.exit_reason.value_counts(normalize=True).round(3).head(5).to_dict()
        if "cost_r" in T:
            R["cost_R_per_trade"] = round(float(T.cost_r.mean()), 3)
        # intelligence filters (Part 5): DISC tests of this strategy's condition set
        for k, m in conditions(T, name).items():
            m = m.fillna(False).to_numpy(bool)
            row = {"strategy": name, "condition": k}
            for p in ("DISC", "VAL", "LOCK"):
                sel = (T.per == p).to_numpy()
                a, b = T.r[sel & m], T.r[sel & ~m]
                tt = stats.ttest_ind(a, b, equal_var=False) if len(a) >= 2 and len(b) >= 2 else None
                row.update({f"{p}_n": int(len(a)), f"{p}_with": round(float(a.mean()), 3) if len(a) else np.nan,
                            f"{p}_without": round(float(b.mean()), 3) if len(b) else np.nan,
                            f"{p}_t": round(float(tt.statistic), 2) if tt is not None else np.nan, f"{p}_p": float(tt.pvalue) if tt is not None else np.nan})
            ctests.append(row)
        res[name] = R
        print(name, R["all"], R["DISC"], R["VAL"], R["LOCK"], flush=True)
    C = pd.DataFrame(ctests)
    p = C.DISC_p.fillna(1).to_numpy()
    o, m = np.argsort(p), len(p)
    ok = p[o] <= 0.05 * np.arange(1, m + 1) / m
    bh = np.zeros(m, bool)
    if ok.any():
        bh[o[: np.max(np.flatnonzero(ok)) + 1]] = True
    C["DISC_bh"] = bh
    C["eligible"] = (C.DISC_n >= 50) & C.DISC_t.abs().gt(2)
    C["confirmed"] = C.eligible & C.DISC_bh & (np.sign(C.VAL_t) == np.sign(C.DISC_t)) & (C.VAL_p < 0.05) & (np.sign(C.LOCK_t) == np.sign(C.DISC_t)) & (C.LOCK_p < 0.10)
    C.to_csv(OUT + "r4_filter_tests.csv", index=False)
    # PBO across the variants each family has on disk
    pbo = {}
    px = pd.read_csv(D + "parallax_untouched.csv")
    px["day"] = pd.to_datetime(px.t_exit, unit="s").dt.floor("D")
    mat = px.pivot_table(index="day", columns=["variant", "target"], values="r", aggfunc="sum").fillna(0)
    pbo["parallax (6 variants x 3 targets)"] = V.pbo_cscv(mat)
    ct = pd.read_csv(D + "cta_untouched.csv")
    ct["day"] = pd.to_datetime(ct.t_exit, unit="s").dt.floor("D")
    pbo["cta (v1, v2)"] = V.pbo_cscv(ct.pivot_table(index="day", columns="variant", values="r", aggfunc="sum").fillna(0))
    sf = pd.concat([pd.read_csv(D + "sflow_fxcm_ours_2012_2021.csv"), pd.read_csv(D + "sflow_5y.csv")])
    sf["day"] = pd.to_datetime(sf.exit_t, unit="s").dt.floor("D")
    pbo["sflow (entry kinds A-D + placebo)"] = V.pbo_cscv(sf.pivot_table(index="day", columns="kind", values="r", aggfunc="sum").fillna(0))
    lcv = pd.read_csv(SP + "lc_out/variants_by_book.csv")
    res["_lifecycle_variants_best"] = (lcv.sort_values("t", ascending=False).groupby(["book", "period"]).head(1)
                                       [["book", "period", "variant", "orig_avg", "var_avg", "t", "p"]].round(3).to_dict(orient="records"))
    res["_pbo"] = pbo
    res["_filters_confirmed"] = C[C.confirmed].to_dict(orient="records")
    res["_filters_disc_eligible"] = C[C.eligible][["strategy", "condition", "DISC_n", "DISC_with", "DISC_without", "DISC_t", "DISC_bh", "VAL_t", "LOCK_t"]].to_dict(orient="records")
    json.dump(res, open(OUT + "r4_hist_results.json", "w"), indent=1, default=str)
    print("PBO", pbo)
    print("eligible filters:", len(res["_filters_disc_eligible"]), "confirmed:", len(res["_filters_confirmed"]))


if __name__ == "__main__":
    main()
