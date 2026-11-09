"""Round 6: four "outside the box" tests (written before running; nothing tuned on the result).
Data: FXCM H1, 25 pairs, 2012-2026 (mid = bid + half the real spread). Splits DISC <= 2018, VAL 2019-22, LOCK 2023-26.
Costs: half the real spread at entry + half at exit + 0.3 bp slippage. Stats: week-clustered t, Benjamini-Hochberg over
every DISC test; graded PROVEN / PROMISING / FAILED exactly as round 3 (DISC BH, VAL p < 0.05, LOCK p < 0.05).

T1  EXPECTED-MOVE BRACKET (trade size, not direction). An expected-move model trained ONLY on 2012-2018 (skills.
    expected_move features, same HGB settings) predicts the 12 h |move|. When prediction >= 3 x round-trip cost: a
    buy-stop 0.5 ATR24 above and a sell-stop 0.5 ATR24 below the close, valid 12 bars, one-cancels-other; stop = the
    other trigger (1 ATR), target 2 ATR, exit after 24 bars; stop first on a shared bar; a fill bar touching both
    triggers = loss. Controls: the same bracket when prediction < 1.5 x cost (LOW) and on every 6th hour (ALL).
    One trade per pair at a time. Result in R (1 R = 1 ATR24).
T2  GOTOBI. Japanese settlement days (5th, 10th, 15th, 20th, 25th, last day; weekend -> previous weekday; Japanese
    holidays not modelled): buy USDJPY at 08:00 JST (23:00 UTC prior day), sell at 10:00 JST (01:00 UTC), after the
    09:55 fix. Control: the same hours on all other weekdays. Result in bp.
T5  FLIPPED SFLOW. Every historical sflow entry-A trade, mirrored (sell where it bought, stop and target mirrored around
    the entry, same 10-day hold), simulated on H1 bars; the original is re-simulated the same way as a check of the
    simulator. Result in R (risk = |entry - stop|), net of the trade's own recorded cost.
T6  CONSENSUS. Each historical trade of the nine strategies (r4_hist.load) is tagged by how many OTHER strategies
    entered the SAME pair within +-6 h: same direction (agree) / opposite (disagree). Outcome = the trade's result
    standardised within its strategy (z). Tests: agree >= 1 vs none, disagree >= 1 vs none.
    python reports/round6/r6_box.py
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.path[:0] = ["D:/Devops/trading/bensim_phase20_dev", "D:/Devops/trading/OpenTerminalUI", "D:/Devops/trading/OpenTerminalUI/reports/round4"]
from skills import expected_move as EM  # noqa: E402

OUT = "D:/Devops/trading/OpenTerminalUI/reports/round6/"
FX = "D:/Devops/trading/OpenTerminalUI/data/bars/fxcm/"
D_ = "D:/Devops/trading/OpenTerminalUI/data/"
PAIRS = [p for p in EM.PAIRS if p != "XAUUSD"]
SPLIT = lambda y: np.where(y <= 2018, "DISC", np.where(y <= 2022, "VAL", "LOCK"))  # noqa: E731


def bars(sym: str) -> dict | None:
    try:
        z = np.load(FX + f"{sym}_H1.npz")
    except FileNotFoundError:
        return None
    half = z["spr"] / 2
    c = z["c"] + half
    return {"t": z["t"].astype(np.int64), "open": z["o"] + half, "high": z["h"] + half, "low": z["l"] + half, "close": c, "spr_bp": z["spr"] / c * 1e4}


def wstat(r: np.ndarray, t: np.ndarray) -> dict:
    r = np.asarray(r, float)
    if len(r) < 20:
        return {"n": int(len(r))}
    wk = (np.asarray(t, "datetime64[s]").astype(np.int64) // (7 * 86400))
    s = pd.Series(r).groupby(wk).sum()
    tt = s.mean() / (s.std(ddof=1) / np.sqrt(len(s))) if s.std() > 0 else 0.0
    return {"n": int(len(r)), "mean": round(float(r.mean()), 4), "t": round(float(tt), 2), "p": float(stats.t.sf(tt, len(s) - 1)),
            "win": round(float((r > 0).mean()), 3), "total": round(float(r.sum()), 1)}


def by_split(df: pd.DataFrame, col: str = "r") -> dict:
    y = pd.to_datetime(df.t).dt.year.to_numpy()
    sp = SPLIT(y)
    return {s: wstat(df[col].to_numpy()[sp == s], pd.to_datetime(df.t).to_numpy()[sp == s]) for s in ("DISC", "VAL", "LOCK")} | \
           {"years_pos": f"{int((df.groupby(y)[col].mean() > 0).sum())}/{len(set(y))}"}


# ------------------------------------------------------------------ T1 expected-move bracket
def t1() -> dict:
    from sklearn.ensemble import HistGradientBoostingRegressor
    d, _ = EM.dataset()
    d = d[d.symbol != "XAUUSD"]
    tr = d[(d.year <= 2018) & d.y12.notna()].iloc[::3]
    m = HistGradientBoostingRegressor(loss="quantile", quantile=0.5, **EM.HGB).fit(tr[EM.FEATS], tr.y12)
    rows = {"HIGH": [], "LOW": [], "ALL": []}
    for sym in PAIRS:
        b = bars(sym)
        if b is None:
            continue
        f = EM.features(b, sym)
        ok = f[EM.FEATS].notna().all(axis=1).to_numpy()
        pred = np.full(len(f), np.nan)
        pred[ok] = m.predict(f.loc[ok, EM.FEATS])
        h, lo, c, sp = b["high"], b["low"], b["close"], b["spr_bp"]
        rng = pd.Series(h - lo).rolling(24, min_periods=12).mean().to_numpy()
        cost = sp + 0.3
        ratio = pred / cost
        n = len(c)
        for kind, cond in (("HIGH", ratio >= 3), ("LOW", ratio < 1.5), ("ALL", (np.arange(n) % 6) == 0)):
            busy = -1
            for i in np.flatnonzero(cond & np.isfinite(ratio) & np.isfinite(rng)):
                if i <= busy or i + 40 >= n:
                    continue
                A = rng[i]
                up, dn = c[i] + 0.5 * A, c[i] - 0.5 * A
                fill, side, j = None, 0, i + 1
                while j <= i + 12:
                    hu, hd = h[j] >= up, lo[j] <= dn
                    if hu and hd:
                        fill, side = j, 0
                        break
                    if hu or hd:
                        fill, side = j, (1 if hu else -1)
                        break
                    j += 1
                if fill is None:
                    continue
                entry = up if side >= 0 else dn
                if side == 0:
                    r_gross = -1.0
                    k = fill
                else:
                    stop, tgt = (dn, entry + 2 * A) if side > 0 else (up, entry - 2 * A)
                    r_gross, k = None, fill
                    for k in range(fill + 1, min(fill + 25, n)):
                        if (side > 0 and lo[k] <= stop) or (side < 0 and h[k] >= stop):
                            r_gross = -1.0
                            break
                        if (side > 0 and h[k] >= tgt) or (side < 0 and lo[k] <= tgt):
                            r_gross = 2.0
                            break
                    if r_gross is None:
                        r_gross = side * (c[k] - entry) / A
                cost_r = (sp[fill] / 2 + sp[k] / 2 + 0.3) / (A / c[i] * 1e4)
                rows[kind].append({"t": pd.Timestamp(int(b["t"][i]) + 3600, unit="s"), "symbol": sym, "r": r_gross - cost_r, "pred_ratio": ratio[i]})
                busy = k
        print("T1", sym, {k: len(v) for k, v in rows.items()}, flush=True)
    out = {}
    for kind, v in rows.items():
        df = pd.DataFrame(v)
        out[kind] = by_split(df) if len(df) else {}
    return out


# ------------------------------------------------------------------ T2 gotobi
def t2() -> dict:
    b = bars("USDJPY")
    t = pd.to_datetime(b["t"], unit="s")
    df = pd.DataFrame({"t": t, "o": b["open"], "spr": b["spr_bp"]}).set_index("t")
    days = pd.date_range("2012-01-01", "2026-10-31", freq="D")
    goto = set()
    for d in days:
        last = (d + pd.offsets.MonthEnd(0)).normalize() == d
        if d.day in (5, 10, 15, 20, 25) or last:
            g = d
            while g.weekday() >= 5:
                g -= pd.Timedelta(days=1)
            goto.add(g.normalize())
    rows = []
    for d in days:
        if d.weekday() >= 5:
            continue
        a, z = d - pd.Timedelta(hours=1), d + pd.Timedelta(hours=1)  # 23:00 UTC prior day, 01:00 UTC
        if a not in df.index or z not in df.index:
            continue
        ea = df.at[a, "o"] * (1 + df.at[a, "spr"] / 2e4)
        eb = df.at[z, "o"] * (1 - df.at[z, "spr"] / 2e4)
        rows.append({"t": d, "goto": d.normalize() in goto, "r": (eb - ea) / ea * 1e4 - 0.3})
    G = pd.DataFrame(rows)
    return {"GOTOBI": by_split(G[G.goto]), "CONTROL_other_days": by_split(G[~G.goto]),
            "diff_t_all_years": round(float(stats.ttest_ind(G[G.goto].r, G[~G.goto].r, equal_var=False).statistic), 2)}


# ------------------------------------------------------------------ T5 flipped sflow
def t5() -> dict:
    sf = pd.concat([pd.read_csv(D_ + "sflow_fxcm_ours_2012_2021.csv"), pd.read_csv(D_ + "sflow_5y.csv")])
    sf = sf[sf.kind == "A"].dropna(subset=["entry", "stop", "target"])
    cache, rows = {}, []
    for r in sf.itertuples():
        if r.symbol not in cache:
            cache[r.symbol] = bars(r.symbol) if r.symbol != "XAUUSD" else None
        b = cache[r.symbol]
        if b is None:
            continue
        i0 = int(np.searchsorted(b["t"], int(r.fill_t) - 3600, side="right"))  # the H1 bar containing the fill
        if i0 >= len(b["t"]) - 2:
            continue
        risk = abs(r.entry - r.stop)
        if risk <= 0:
            continue
        res = {}
        for name, d, stop, tgt in (("orig", int(r.dir), r.stop, r.target), ("flip", -int(r.dir), 2 * r.entry - r.stop, 2 * r.entry - r.target)):
            out = None
            for k in range(i0 + 1, min(i0 + 241, len(b["t"]))):
                if (d > 0 and b["low"][k] <= stop) or (d < 0 and b["high"][k] >= stop):
                    out = -1.0
                    break
                if (d > 0 and b["high"][k] >= tgt) or (d < 0 and b["low"][k] <= tgt):
                    out = abs(tgt - r.entry) / risk
                    break
            if out is None:
                out = d * (b["close"][min(i0 + 240, len(b["t"]) - 1)] - r.entry) / risk
            res[name] = out - (r.cost_r if np.isfinite(r.cost_r) else 0.0)
        rows.append({"t": pd.Timestamp(int(r.fill_t), unit="s"), "symbol": r.symbol, "recorded": r.r, **res})
    F = pd.DataFrame(rows)
    return {"n": int(len(F)), "simulator_check_corr_orig_vs_recorded": round(float(F[["orig", "recorded"]].corr().iloc[0, 1]), 3),
            "orig_sim": by_split(F, "orig"), "flipped": by_split(F, "flip"), "recorded_original": by_split(F, "recorded")}


# ------------------------------------------------------------------ T6 consensus between strategies
def t6() -> dict:
    import r4_hist
    S = r4_hist.load()
    allt = []
    for name, T in S.items():
        z = (T.r - T.r.mean()) / T.r.std()
        allt.append(pd.DataFrame({"strategy": name, "symbol": T.symbol.to_numpy(), "dir": T.dir.to_numpy(), "t": T.t.to_numpy(), "z": z.to_numpy()}))
    A = pd.concat(allt, ignore_index=True).sort_values("t").reset_index(drop=True)
    agree, disagree = np.zeros(len(A), int), np.zeros(len(A), int)
    win = np.timedelta64(6, "h")
    for sym, g in A.groupby("symbol"):
        tt, dd, ss = g.t.to_numpy(), g.dir.to_numpy(), g.strategy.to_numpy()
        lo = np.searchsorted(tt, tt - win, side="left")
        hi = np.searchsorted(tt, tt + win, side="right")
        for n_, (i, a, b_) in enumerate(zip(g.index, lo, hi)):
            k = n_
            others = [(dd[j], ss[j]) for j in range(a, b_) if j != k and ss[j] != ss[k]]
            agree[i] = len({s for d_, s in others if d_ == dd[k]})
            disagree[i] = len({s for d_, s in others if d_ != dd[k]})
    A["agree"], A["disagree"] = agree, disagree
    out = {"trades": int(len(A)), "share_with_agreement": round(float((A.agree > 0).mean()), 3), "share_with_disagreement": round(float((A.disagree > 0).mean()), 3)}
    for name, m in (("AGREE>=1", A.agree >= 1), ("NONE", (A.agree == 0) & (A.disagree == 0)), ("DISAGREE>=1", A.disagree >= 1), ("AGREE>=2", A.agree >= 2)):
        out[name] = by_split(A[m].rename(columns={"z": "r"}))
    sp = SPLIT(pd.to_datetime(A.t).dt.year.to_numpy())
    for lab, m in (("agree_vs_none", A.agree >= 1), ("disagree_vs_none", A.disagree >= 1)):
        base = (A.agree == 0) & (A.disagree == 0)
        out[lab + "_t"] = {s: round(float(stats.ttest_ind(A.z[m & (sp == s)], A.z[base & (sp == s)], equal_var=False).statistic), 2) for s in ("DISC", "VAL", "LOCK")}
    out["by_strategy_agree_vs_none_mean_z"] = {s: [round(float(g.z[g.agree >= 1].mean()), 3) if (g.agree >= 1).any() else None,
                                                  round(float(g.z[(g.agree == 0) & (g.disagree == 0)].mean()), 3), int((g.agree >= 1).sum())]
                                               for s, g in A.groupby("strategy")}
    return out


def grade(sp: dict) -> str:
    d, v, lk = sp.get("DISC", {}), sp.get("VAL", {}), sp.get("LOCK", {})
    if not d.get("n"):
        return "NO DATA"
    if d.get("mean", -1) > 0 and d.get("p", 1) < 0.01 and v.get("mean", -1) > 0 and v.get("p", 1) < 0.05 and lk.get("mean", -1) > 0 and lk.get("p", 1) < 0.05:
        return "PROVEN"
    if d.get("mean", -1) > 0 and v.get("mean", -1) > 0 and lk.get("mean", -1) > 0 and min(v.get("p", 1), lk.get("p", 1)) < 0.10:
        return "PROMISING"
    return "FAILED"


def main() -> None:
    res = {}
    for name, fn in (("T2_gotobi", t2), ("T5_flip_sflow", t5), ("T6_consensus", t6), ("T1_expected_move_bracket", t1)):
        try:
            res[name] = fn()
        except Exception as exc:  # noqa: BLE001
            import traceback
            res[name] = {"error": repr(exc), "trace": traceback.format_exc()[-800:]}
        json.dump(res, open(OUT + "r6_results.json", "w"), indent=1, default=str)
        print("DONE", name, flush=True)
    res["_grades"] = {"T1 bracket HIGH": grade(res.get("T1_expected_move_bracket", {}).get("HIGH", {})),
                      "T1 bracket ALL (control)": grade(res.get("T1_expected_move_bracket", {}).get("ALL", {})),
                      "T2 gotobi": grade(res.get("T2_gotobi", {}).get("GOTOBI", {})),
                      "T5 flipped sflow": grade(res.get("T5_flip_sflow", {}).get("flipped", {}))}
    json.dump(res, open(OUT + "r6_results.json", "w"), indent=1, default=str)
    print(json.dumps(res["_grades"], indent=1))


if __name__ == "__main__":
    main()
