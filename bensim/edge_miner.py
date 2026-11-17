"""Edge miner: 25 FX pairs x 15 years of hourly decisions -> what really pays, found on the past, proven on locked years.

    python -m bensim.edge_miner build      # features + net outcomes -> data/edge_miner.pkl
    python -m bensim.edge_miner scan       # single-condition scan (discovery -> validation -> locked test)
    python -m bensim.edge_miner model      # pooled gradient-boosting model, same split + pair hold-out + placebo

Split (fixed before any result was seen): DISCOVERY 2012-2017 | VALIDATION 2018-2019 | LOCKED TEST 2020-now.
Prices are MID (bid close + half the real spread of that hour, FXCM H1); every outcome pays half the REAL spread at
entry and half at exit, so the 21-22 UTC rollover cannot fake an edge again. All features use only data up to the
decision close. Outcomes in basis points (bp) of price; "net long" / "net short" already include costs.
"""
from __future__ import annotations

import argparse
import os
import pickle

import numpy as np
import pandas as pd

from .fxcm_history import OUT, load

PATH = "data/edge_miner.pkl"  # our own cache, written by build() (never loaded from elsewhere)
H = (1, 3, 6, 12, 24)
CCY = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]
HOME = {"JPY": (0, 8), "AUD": (22, 6), "NZD": (21, 5), "EUR": (7, 15), "CHF": (7, 15), "GBP": (7, 15), "USD": (13, 21), "CAD": (13, 21)}
DISC, VAL = 2017, 2019  # discovery <= 2017 < validation <= 2019 < locked test


def _in_home(hour: np.ndarray, c: str) -> np.ndarray:
    a, b = HOME[c]
    return ((hour >= a) & (hour < b)) if a < b else ((hour >= a) | (hour < b))


def features(sym: str, m: dict[str, np.ndarray]) -> pd.DataFrame:
    """Per-pair inputs from H1 bars m = {t (UTC open, s), high, low, close (bid), spr (price)}; used by build() AND by
    the live engine (bensim.flow_live), so research and live see exactly the same numbers."""
    t = pd.Series(pd.to_datetime(m["t"] + 3600, unit="s"))  # decision time = the bar's close
    mid = m["close"] + m["spr"] / 2
    spr_bp = m["spr"] / m["close"] * 1e4
    hi, lo = m["high"] + m["spr"] / 2, m["low"] + m["spr"] / 2
    s = pd.Series(np.log(mid))
    r1 = s.diff() * 1e4
    vol = r1.rolling(24 * 20, min_periods=200).std()  # hourly bp sd over ~1 month
    f = pd.DataFrame({"t": t, "symbol": sym, "mid": mid, "spr": spr_bp, "vol": vol})
    for n in (1, 3, 6, 12, 24, 72, 120):
        f[f"ret_{n}"] = (s - s.shift(n)) * 1e4 / (vol * np.sqrt(n))  # vol-normalised past move
    f["vol_ratio"] = r1.rolling(24, min_periods=12).std() / vol
    f["hour"], f["dow"] = t.dt.hour, t.dt.dayofweek
    bdays_left = (t + pd.offsets.BMonthEnd(0)).dt.normalize()
    f["days_to_month_end"] = np.busday_count(t.dt.date.to_numpy().astype("datetime64[D]"), bdays_left.dt.date.to_numpy().astype("datetime64[D]"))
    f["day_of_month"] = t.dt.day
    # yesterday's range (UTC day) and today's range so far
    day = t.dt.floor("D")
    g = pd.DataFrame({"day": day, "hi": hi, "lo": lo})
    dh, dl = g.groupby("day").hi.max(), g.groupby("day").lo.min()
    pdh, pdl = day.map(dh.shift(1)).to_numpy(), day.map(dl.shift(1)).to_numpy()
    f["pos_prev_day"] = (mid - pdl) / np.maximum(pdh - pdl, 1e-12)
    cum_hi, cum_lo = g.groupby("day").hi.cummax().to_numpy(), g.groupby("day").lo.cummin().to_numpy()
    f["pos_today"] = (mid - cum_lo) / np.maximum(cum_hi - cum_lo, 1e-12)
    f["base_home"] = _in_home(f.hour.to_numpy(), sym[:3]).astype(int)
    f["quote_home"] = _in_home(f.hour.to_numpy(), sym[3:]).astype(int)
    return f


def _pair(sym: str) -> pd.DataFrame:
    f = features(sym, load(sym, "H1"))
    s = pd.Series(np.log(f.mid.to_numpy()))
    for h in H:  # forward outcomes, net of real costs (half spread now + half spread at exit)
        fwd = (s.shift(-h) - s) * 1e4
        cost = (f.spr.to_numpy() + f.spr.shift(-h).to_numpy()) / 2
        f[f"long_{h}"] = fwd - cost
        f[f"short_{h}"] = -fwd - cost
    return f


def add_strength(f: pd.DataFrame) -> pd.DataFrame:
    """Currency strength (all pairs at the same time) + currency ids; shared by build() and the live engine."""
    for n in (24, 120):
        piv = f.pivot_table(index="t", columns="symbol", values=f"ret_{n}")
        strength = {}
        for c in CCY:
            parts = [piv[p] if p.startswith(c) else -piv[p] for p in piv.columns if c in (p[:3], p[3:])]
            strength[c] = pd.concat(parts, axis=1).mean(axis=1)
        st = pd.DataFrame(strength)
        f[f"str_base_{n}"] = st.reindex(f.t).to_numpy()[np.arange(len(f)), [CCY.index(s[:3]) for s in f.symbol]]
        f[f"str_quote_{n}"] = st.reindex(f.t).to_numpy()[np.arange(len(f)), [CCY.index(s[3:]) for s in f.symbol]]
        f[f"str_diff_{n}"] = f[f"str_base_{n}"] - f[f"str_quote_{n}"]
    f["year"] = f.t.dt.year
    f["base_id"] = f.symbol.str[:3].map(CCY.index)  # currency identity: lets a model learn currency-specific habits
    f["quote_id"] = f.symbol.str[3:].map(CCY.index)
    return f


def build() -> pd.DataFrame:
    pairs = sorted(p.stem.split("_")[0] for p in OUT.glob("*_H1.npz"))
    f = add_strength(pd.concat([_pair(s) for s in pairs], ignore_index=True))
    f = f[f.dow < 5].dropna(subset=["vol", "ret_120"])
    with open(PATH, "wb") as fh:
        pickle.dump(f, fh)
    print(f"{len(f)} rows, {f.symbol.nunique()} pairs, {f.t.min():%Y-%m} .. {f.t.max():%Y-%m}")
    return f


FEATURES = ["ret_1", "ret_3", "ret_6", "ret_12", "ret_24", "ret_72", "ret_120", "vol_ratio", "hour", "dow",
            "days_to_month_end", "day_of_month", "pos_prev_day", "pos_today", "base_home", "quote_home", "spr",
            "str_base_24", "str_quote_24", "str_diff_24", "str_base_120", "str_quote_120", "str_diff_120", "base_id", "quote_id"]


def _load() -> pd.DataFrame:
    with open(PATH, "rb") as fh:
        return pickle.load(fh)


def scan() -> None:
    """Single conditions: each feature in deciles (cut on discovery), x horizon x side. A condition is a candidate when,
    in DISCOVERY, its mean net outcome > 0 with t > 5 (strict: thousands of tests, overlapping hours) and it is positive
    in >= 5 of 6 years; it then has to stay positive in VALIDATION; only those are shown on the LOCKED TEST."""
    f = _load()
    disc = f[f.year <= DISC]
    rows = []
    for feat in FEATURES:
        q = np.unique(np.nanquantile(disc[feat], np.linspace(0, 1, 11)))
        if len(q) < 3:
            continue
        b = np.clip(np.searchsorted(q, f[feat].to_numpy(), side="right") - 1, 0, len(q) - 2)
        for k in range(len(q) - 1):
            sel = b == k
            for h in H:
                for side in ("long", "short"):
                    col = f"{side}_{h}"
                    x = f.loc[sel, [col, "year", "t"]].dropna()
                    d, v, te = x[x.year <= DISC], x[(x.year > DISC) & (x.year <= VAL)], x[x.year > VAL]
                    if len(d) < 2000:
                        continue
                    # one observation per pair-day-ish: daily means reduce the overlap problem of hourly rows
                    dm = d.groupby(d.t.dt.floor("D"))[col].mean()
                    t_d = dm.mean() / dm.std() * np.sqrt(len(dm))
                    yrs = d.groupby("year")[col].mean()
                    rows.append({"feature": feat, "bin": f"[{q[k]:.3g},{q[k + 1]:.3g})", "h": h, "side": side,
                                 "disc_bp": d[col].mean(), "disc_t_daily": t_d, "disc_years+": int((yrs > 0).sum()),
                                 "val_bp": v[col].mean(), "test_bp": te[col].mean(), "test_n": len(te),
                                 "test_years+": f"{int((te.groupby('year')[col].mean() > 0).sum())}/{te.year.nunique()}"})
    r = pd.DataFrame(rows)
    r.to_csv("data/edge_miner_scan.csv", index=False)
    cand = r[(r.disc_bp > 0) & (r.disc_t_daily > 5) & (r["disc_years+"] >= 5)]
    print(f"{len(r)} conditions scanned; {len(cand)} pass discovery (t>5, >=5/6 years); "
          f"{(cand.val_bp > 0).sum()} of them stay positive in validation")
    ok = cand[cand.val_bp > 0].sort_values("disc_t_daily", ascending=False)
    pd.set_option("display.width", 220)
    print(ok.head(40).round(2).to_string(index=False))


CHEAP = ["EURUSD", "USDJPY", "USDCAD", "GBPUSD", "AUDUSD", "EURJPY"]  # median FXCM spread < 0.45 bp (cost rule, set
# 2026-10-14 from the spread table BEFORE any per-pair model result was seen)


def model(horizons: tuple[int, ...] = (6, 24), trade_pairs: list[str] | None = None) -> None:
    """Pooled gradient boosting on all pairs: predict net long and net short outcome at horizon 6h and 24h from the
    features. Trained on DISCOVERY only; the trade threshold = the prediction level whose VALIDATION trades are best
    (chosen among 4 fixed quantiles); then the LOCKED TEST is run once. One open trade per pair (non-overlapping).
    Checks: pairs never seen in training (train on half the pairs, test the other half), and the same model trained on
    shuffled outcomes (placebo)."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    f = _load()
    f["sym_id"] = f.symbol.astype("category").cat.codes
    feats = FEATURES

    def fit(tr: pd.DataFrame, y: np.ndarray):
        m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=2000,
                                          l2_regularization=1.0, random_state=0)
        return m.fit(tr[feats], y)

    def trades(df: pd.DataFrame, pl: np.ndarray, ps: np.ndarray, thr: float, h: int) -> pd.DataFrame:
        out = []
        for sym, g in df.assign(pl=pl, ps=ps).groupby("symbol"):
            busy = pd.Timestamp(0)
            for x in g.itertuples():
                if x.t < busy:
                    continue
                side = "long" if x.pl >= x.ps else "short"
                if max(x.pl, x.ps) <= thr:
                    continue
                r = getattr(x, f"{side}_{h}")
                if not np.isfinite(r):
                    continue
                out.append((sym, x.t, x.year, side, r))
                busy = x.t + pd.Timedelta(hours=h)
        return pd.DataFrame(out, columns=["symbol", "t", "year", "side", "bp"])

    def summary(t: pd.DataFrame, label: str) -> None:
        if t.empty:
            print(f"  {label}: no trades")
            return
        ys = t.groupby("year").bp.mean()
        pp = t.groupby("symbol").bp.mean()
        print(f"  {label:40} {len(t):6} trades avg {t.bp.mean():+.2f} bp t {t.bp.mean() / t.bp.std() * np.sqrt(len(t)):+.2f} "
              f"years+ {(ys > 0).sum()}/{len(ys)} pairs+ {(pp > 0).sum()}/{len(pp)}")

    rng = np.random.default_rng(0)
    for h in horizons:
        disc, val, test = f[f.year <= DISC], f[(f.year > DISC) & (f.year <= VAL)], f[f.year > VAL]
        if trade_pairs:  # train on every pair, trade only these
            val, test = val[val.symbol.isin(trade_pairs)], test[test.symbol.isin(trade_pairs)]
        print(f"\n===== horizon {h}h =====")
        for label, shuffle in (("MODEL", False), ("PLACEBO (shuffled outcomes)", True)):
            yl, ys = disc[f"long_{h}"].to_numpy(), disc[f"short_{h}"].to_numpy()
            ok = np.isfinite(yl) & np.isfinite(ys)
            yl, ys = np.clip(yl[ok], -300, 300), np.clip(ys[ok], -300, 300)
            if shuffle:
                yl, ys = rng.permutation(yl), rng.permutation(ys)
            ml, ms = fit(disc[ok], yl), fit(disc[ok], ys)
            pv = np.maximum(ml.predict(val[feats]), ms.predict(val[feats]))
            best, thr = -np.inf, None
            for q in (0.90, 0.95, 0.98, 0.99):  # threshold picked on VALIDATION only
                th = float(np.quantile(pv, q))
                tv = trades(val, ml.predict(val[feats]), ms.predict(val[feats]), th, h)
                score = tv.bp.mean() if len(tv) > 30 else -np.inf
                if score > best:
                    best, thr = score, th
            print(f" {label}: threshold from validation = {thr:.2f} bp (validation avg {best:+.2f} bp)")
            summary(trades(test, ml.predict(test[feats]), ms.predict(test[feats]), thr, h), "LOCKED TEST 2020-now")
        # pair hold-out: train on half the pairs (discovery years), test the OTHER pairs in the locked years
        syms = sorted(f.symbol.unique())
        a_set = set(syms[::2])
        tr = disc[disc.symbol.isin(a_set)]
        okk = np.isfinite(tr[f"long_{h}"]) & np.isfinite(tr[f"short_{h}"])
        ml = fit(tr[okk], np.clip(tr.loc[okk, f"long_{h}"], -300, 300))
        ms = fit(tr[okk], np.clip(tr.loc[okk, f"short_{h}"], -300, 300))
        vb = val[~val.symbol.isin(a_set)]
        pv = np.maximum(ml.predict(vb[feats]), ms.predict(vb[feats]))
        th = float(np.quantile(pv, 0.95))
        tb = test[~test.symbol.isin(a_set)]
        summary(trades(tb, ml.predict(tb[feats]), ms.predict(tb[feats]), th, h), "UNSEEN PAIRS, locked test (q95)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "scan", "model", "model_cheap"])
    a = ap.parse_args()
    os.environ.setdefault("OMP_NUM_THREADS", "3")
    if a.cmd == "model_cheap":
        model((3, 6, 12), CHEAP)
    else:
        {"build": build, "scan": scan, "model": model}[a.cmd]()


if __name__ == "__main__":
    main()
