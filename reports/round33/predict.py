"""Round 33 (2026-11-11): can a model PREDICT what an imbalance (big one-sided candle, round 32 definition) does next?
User: "every imbalance in the past 2 years -- is there a place to buy or sell and it fills and we profit ... or follow it to the
liquidity ... how will we know if the liquidity holds or breaks".
Three decisions (a drop; a spike up = mirrored):
  FILL    at the candle close, BUY toward the fill: target 50% of the body, stop = candle low - 0.25 ATR
  FOLLOW  at the candle close, SELL with the move to the liquidity: target = the nearest swing low (NEAR liquidity) - 1 pip,
          stop = the candle open + 1 pip (a full fill = wrong)
  HOLD    when price first reaches the NEAR liquidity: BUY (it holds -> fills back), target 50% of the body, stop = liquidity - 0.5 ATR
  BREAK   same moment, SELL (it breaks): target = liquidity - 1 x (body), stop = liquidity + 0.5 ATR
Features (all known at the decision moment): timeframe, pair, candle size / shape, run of big candles, 5/20-bar move, position in
the 50-bar range (Fibonacci), distance to NEAR / MAJOR liquidity, how many equal lows sit at the liquidity, H4 and D1 structure
trend, hour, weekday, news +-2h, volatility regime, whether the candle left a 3-candle FVG; for HOLD/BREAK also bars to the touch.
Model: gradient boosting, TRAINED ONLY on 2021-10..2024-11; TESTED on 2024-11..2026-11 (the 2 years, never seen).
Rule chosen on the training years only: take a trade when the predicted R > the threshold that was best in time-split CV.
    python reports/round33/predict.py
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + "/reports/round28")
OUT = Path(ROOT + "/reports/round33")
SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "XAUUSD", "USDJPY", "USDCHF", "USDCAD"]
TFS = {"M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "H12": 43200, "D1": 86400}
SPLIT = pd.Timestamp("2024-11-07", tz="UTC").timestamp()
CCY = {"EURUSD": ["EUR", "USD"], "GBPUSD": ["GBP", "USD"], "AUDUSD": ["AUD", "USD"], "XAUUSD": ["USD"], "USDJPY": ["USD", "JPY"],
       "USDCHF": ["USD", "CHF"], "USDCAD": ["USD", "CAD"]}
SPR = {s: float(np.mean(v)) for s, v in json.loads(Path(ROOT + "/data/costs/spread_samples_2026-11-03.json").read_text())["spread_pips"].items()}
DECISIONS = ("FILL", "FOLLOW", "HOLD", "BREAK")


def pip(sym):
    return 0.01 if sym == "USDJPY" else 0.1 if sym == "XAUUSD" else 1e-4


def first(mask):
    return int(np.argmax(mask)) if mask.any() else -1


def sim(hi, lo, cl, z, end, entry, sl, tp, long):
    if long:
        a, b = first(lo[z:end] <= sl), first(hi[z:end] >= tp)
    else:
        a, b = first(hi[z:end] >= sl), first(lo[z:end] <= tp)
    A, B = (a if a >= 0 else 10**9), (b if b >= 0 else 10**9)
    risk = abs(entry - sl)
    if A == B == 10**9:
        return (cl[end - 1] - entry) * (1 if long else -1) / risk
    return -1.0 if A <= B else abs(tp - entry) / risk


def run_sym(sym):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # below normal
    from zone_map import structure
    from bensim import db
    from bensim.backtest import load_m5, resample
    raw = load_m5(sym, "2021-01-01", "2026-12-31")
    s_ts, e_ts = pd.Timestamp("2021-10-04", tz="UTC").timestamp(), pd.Timestamp("2026-11-03", tz="UTC").timestamp()
    buf, cost_px = (1.0 if sym == "XAUUSD" else pip(sym)), (SPR.get(sym, 0.5) + 0.2) * pip(sym)
    with db.connect() as c:
        news = np.array(sorted(r[0] for r in c.execute("select extract(epoch from ts)::bigint from economic_events where impact ilike 'high%%' "
                                                       "and currency = any(%s)", [CCY[sym]]).fetchall()), np.int64)
    rows = []
    for d in (1, -1):
        m = raw if d == 1 else {"t": raw["t"], "open": -raw["open"], "high": -raw["low"], "low": -raw["high"], "close": -raw["close"]}
        t, hi, lo, cl = m["t"], m["high"], m["low"], m["close"]
        htf = {}
        for name, sec in (("H4", 14400), ("D1", 86400)):
            bb = resample(m, sec)
            htf[name] = (bb["t"] + sec, structure(bb)[2])
        for tf, sec in TFS.items():
            b = resample(m, sec)
            O, H, L, C, T = b["open"], b["high"], b["low"], b["close"], b["t"]
            body = np.abs(C - O)
            avg = pd.Series(body).rolling(20).mean().shift(1).to_numpy()
            tr = np.r_[np.nan, np.maximum(H[1:], C[:-1]) - np.minimum(L[1:], C[:-1])]
            atr = pd.Series(tr).rolling(14).mean().to_numpy()
            atr100 = pd.Series(atr).rolling(100).mean().to_numpy()
            ii = np.arange(3, len(L) - 3)
            piv = ii[(L[ii] <= L[ii - 1]) & (L[ii] <= L[ii - 2]) & (L[ii] <= L[ii - 3]) & (L[ii] < L[ii + 1]) & (L[ii] < L[ii + 2]) & (L[ii] < L[ii + 3])]
            win = 100 * sec
            for i in range(130, len(T)):
                ct = T[i] + sec
                if not (s_ts <= ct < e_ts and O[i] > C[i] and body[i] >= 1.5 * avg[i] and body[i] >= 0.6 * (H[i] - L[i])
                        and np.isfinite(atr[i]) and np.isfinite(atr100[i])):
                    continue
                z0 = np.searchsorted(t, ct)
                end = min(np.searchsorted(t, ct + win), len(t))
                if z0 >= end - 2:
                    continue
                a = atr[i]
                cand = piv[(piv + 3 <= i) & (piv >= i - 120)]
                below = L[cand][L[cand] < L[i]]
                near = below.max() if len(below) else np.nan
                major = L[i - 120:i].min()
                ce = (O[i] + C[i]) / 2
                k_n = np.searchsorted(news, ct)
                hrs = (ct % 86400) // 3600
                row = dict(sym=sym, dir=d, tf=tf, created=ct,
                           body_atr=body[i] / a, body_frac=body[i] / (H[i] - L[i]), lower_wick=(C[i] - L[i]) / (H[i] - L[i]),
                           run=int(sum(body[i - k] >= 1.5 * avg[i - k] and O[i - k] > C[i - k] for k in (1, 2, 3))),
                           ret5=(C[i] - C[i - 5]) / a, ret20=(C[i] - C[i - 20]) / a,
                           range_pos=(C[i] - L[i - 50:i + 1].min()) / max(H[i - 50:i + 1].max() - L[i - 50:i + 1].min(), 1e-12),
                           near_atr=(L[i] - near) / a if np.isfinite(near) else np.nan,
                           major_atr=(L[i] - major) / a if major < L[i] else -1.0,
                           eq_lows=int(np.sum(np.abs(L[cand] - near) <= 0.2 * a)) if np.isfinite(near) else 0,
                           trend_H4=int(htf["H4"][1][max(np.searchsorted(htf["H4"][0], ct, "right") - 1, 0)]),
                           trend_D1=int(htf["D1"][1][max(np.searchsorted(htf["D1"][0], ct, "right") - 1, 0)]),
                           hour=int(hrs), dow=int(((ct // 86400) + 3) % 7),
                           news=bool(any(0 <= j < len(news) and abs(news[j] - ct) <= 7200 for j in (k_n - 1, k_n))),
                           vol_regime=a / atr100[i], left_fvg=bool(H[i] < L[i - 2]), tf_sec=sec)
                # FILL: buy at the close, target 50% of the body, stop under the candle low
                sl = L[i] - 0.25 * a
                row["R_FILL"] = sim(hi, lo, cl, z0, end, C[i], sl, ce, True) - cost_px / (C[i] - sl)
                # FOLLOW: sell at the close to the NEAR liquidity, stop above the candle open
                if np.isfinite(near):
                    sl, tp = O[i] + buf, near - buf
                    row["R_FOLLOW"] = sim(hi, lo, cl, z0, end, C[i], sl, tp, False) - cost_px / (sl - C[i])
                    row["follow_rr"] = (C[i] - tp) / (sl - C[i])
                    k = first(lo[z0:end] <= near)
                    if k >= 0:
                        k += z0
                        row["bars_to_liq"] = (t[k] - ct) / sec
                        row["filled_before_liq"] = bool((hi[z0:k] >= ce).any())
                        sl = near - 0.5 * a
                        row["R_HOLD"] = sim(hi, lo, cl, k, end, near, sl, ce, True) - cost_px / (near - sl)
                        sl, tp = near + 0.5 * a, near - body[i]
                        row["R_BREAK"] = sim(hi, lo, cl, k, end, near, sl, tp, False) - cost_px / (sl - near)
                rows.append(row)
    return rows


FEATS = ["tf_sec", "sym_code", "dir", "body_atr", "body_frac", "lower_wick", "run", "ret5", "ret20", "range_pos", "near_atr", "major_atr",
         "eq_lows", "trend_H4", "trend_D1", "hour", "dow", "news", "vol_regime", "left_fvg"]


def tstat(x):
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def main():
    from sklearn.ensemble import HistGradientBoostingRegressor
    f = OUT / "events.pkl"  # our own local cache
    if not f.exists():
        with ProcessPoolExecutor(4) as ex:
            rows = [r for part in ex.map(run_sym, SYMS) for r in part]
        pd.DataFrame(rows).to_pickle(f)
    ev = pd.read_pickle(f)
    ev["sym_code"] = ev.sym.astype("category").cat.codes
    for c in ("news", "left_fvg", "filled_before_liq"):
        if c in ev:
            ev[c] = ev[c].astype(float)
    train, test = ev[ev.created < SPLIT], ev[ev.created >= SPLIT]
    print(f"Round 33: predict what an imbalance does next. {len(ev)} imbalances (15m..1D, 7 pairs, both directions)")
    print(f"train 2021-10..2024-11: {len(train)} | TEST (never seen) 2024-11..2026-11: {len(test)}\n")
    print("1) WITHOUT a model (take every one) -- the base rate, R per trade")
    for dcs in DECISIONS:
        col = f"R_{dcs}"
        print(f"   {dcs:7s} " + " | ".join(f"{tf} {test[test.tf == tf][col].mean():+.3f}" for tf in TFS))
    print("\n2) WITH the model (trained on 2021-24 only; threshold chosen on 2021-24 only), scored on 2024-26")
    print(f"   {'decision':8s} {'TF':4s} {'test trades':>11s} {'win%':>6s} {'R/trade':>8s} {'total R':>8s} {'t':>5s} {'$ at $1k':>10s} | top 10% of predictions: R/trade, total")
    feats = FEATS + ["bars_to_liq", "filled_before_liq"]
    summary = []
    for dcs in DECISIONS:
        col = f"R_{dcs}"
        fs = feats if dcs in ("HOLD", "BREAK") else FEATS
        tr_ = train[train[col].notna()]
        te_ = test[test[col].notna()]
        # time-split CV inside the training years to pick the threshold
        cut = tr_.created.quantile(0.6)
        a, b = tr_[tr_.created < cut], tr_[tr_.created >= cut]
        m1 = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=200, random_state=1)
        m1.fit(a[fs], a[col].clip(-1, 6))
        pb = m1.predict(b[fs])
        best_thr, best = None, 0
        for q in (0.5, 0.7, 0.8, 0.9, 0.95):
            thr = np.quantile(pb, q)
            r = b[col][pb > thr]
            if len(r) >= 100 and r.sum() > best:
                best_thr, best = (q, thr), r.sum()
        m = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=200, random_state=1)
        m.fit(tr_[fs], tr_[col].clip(-1, 6))
        p = m.predict(te_[fs])
        te_ = te_.assign(pred=p)
        if best_thr is None:
            print(f"   {dcs:8s} the training years found NO threshold with positive R -> no trades")
            continue
        thr_full = np.quantile(m.predict(tr_[fs]), best_thr[0])
        for tf in list(TFS) + ["ALL"]:
            x = te_ if tf == "ALL" else te_[te_.tf == tf]
            sel = x[x.pred > thr_full][col]
            top = x[x.pred >= x.pred.quantile(0.9)][col]
            if len(sel) < 20:
                continue
            print(f"   {dcs:8s} {tf:4s} {len(sel):11d} {100 * (sel > 0).mean():5.1f}% {sel.mean():+8.3f} {sel.sum():+8.1f} {tstat(sel):5.1f} "
                  f"{1000 * sel.sum():+10,.0f} | {top.mean():+.3f}, {top.sum():+.1f}")
            if tf == "ALL":
                summary.append((dcs, len(sel), sel.mean(), sel.sum(), tstat(sel)))
        # decile check: does a higher prediction mean a better result on the unseen years?
        dec = pd.qcut(te_.pred, 10, labels=False, duplicates="drop")
        print(f"   {dcs:8s} R/trade by prediction decile (low -> high, 2024-26): " + " ".join(f"{v:+.2f}" for v in te_.groupby(dec)[col].mean()))
        if dcs in ("HOLD", "BREAK"):
            imp = pd.Series(np.abs(np.corrcoef(np.c_[te_[fs].fillna(-9).to_numpy(), te_[col].to_numpy()].T)[-1, :-1]), index=fs).sort_values(ascending=False)
            print(f"   {dcs:8s} factors most linked to the result: " + ", ".join(f"{k} {v:.2f}" for k, v in imp.head(5).items()))
        print()
    print("3) HOLD OR BREAK at the liquidity (2024-26, no model): " + ", ".join(
        f"{tf}: holds->fills {100 * (test[(test.tf == tf)].R_HOLD > 0).mean():.0f}% / breaks->runs {100 * (test[(test.tf == tf)].R_BREAK > 0).mean():.0f}%"
        for tf in TFS))
    print("\nVERDICT (model chosen on 2021-24, judged on unseen 2024-26; pass = total > 0 and t >= 2):")
    for dcs, n, avg, tot, t_ in summary:
        print(f"   {dcs:7s} {n:6d} trades {avg:+.3f} R/trade total {tot:+.1f}R t {t_:.1f} -> {'PASS' if tot > 0 and t_ >= 2 else 'FAIL'}")


if __name__ == "__main__":
    main()
