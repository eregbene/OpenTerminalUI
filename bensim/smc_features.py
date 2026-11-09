"""SMC inputs (BOS / CHoCH, FVG, imbalance, liquidity) for the rate-aware model -- does the data want them?

    python -m bensim.smc_features build    # H1 SMC features for 25 FXCM pairs -> data/smc_features.pkl
    python -m bensim.smc_features run      # 5-seed ensemble walk-forward WITH vs WITHOUT SMC inputs + importance

All features are causal (known at the H1 close they are stamped with; a 5-bar fractal swing is only known 2 bars
after it). Definitions (fixed before running):
  structure  swings = 5-bar fractals. A close above the last confirmed swing high = up-break, below the last swing low
             = down-break. Break in the same direction as the previous one = BOS, opposite = CHoCH.
             smc_trend (+1/-1), smc_last_choch (1 if the last break was a CHoCH), smc_bars_since_break (cap 200),
             smc_dist_break (close - broken level, in ATR, signed with the trend).
  FVG        bullish at bar i: low[i] > high[i-2]; bearish: high[i] < low[i-2]. A bullish FVG is filled when a later low
             trades below its bottom (bearish: high above its top). smc_fvg_bull_dist / smc_fvg_bear_dist = distance
             from close to the nearest unfilled bullish FVG top below / bearish FVG bottom above (ATR, cap 10);
             smc_in_fvg (+1 inside a bullish, -1 inside a bearish, 0); smc_fvg_new (+1/-1 if one formed in the last 3
             bars); smc_fvg_open (unfilled bullish minus bearish among those from the last 100 bars).
  imbalance  smc_displacement: the largest (range / ATR) of the last 3 bars with body >= 70% of range, signed by colour.
  liquidity  smc_sweep_pd: +1 if in the last 3 bars a low went below the previous UTC day's low and the bar closed back
             above it (sell-side swept), -1 for the mirror; smc_eq_high_dist / smc_eq_low_dist: distance (ATR, cap 10)
             to the nearest pair of swing highs (lows) within 0.1 ATR of each other among the last 10 swings.
"""
from __future__ import annotations

import argparse
import os
import pickle

import numpy as np
import pandas as pd

from .fxcm_history import OUT, load

PATH = "data/smc_features.pkl"  # our own cache (never loaded from elsewhere)
SMC = ["smc_trend", "smc_last_choch", "smc_bars_since_break", "smc_dist_break", "smc_fvg_bull_dist", "smc_fvg_bear_dist",
       "smc_in_fvg", "smc_fvg_new", "smc_fvg_open", "smc_displacement", "smc_sweep_pd", "smc_eq_high_dist", "smc_eq_low_dist"]


def pair_features(sym: str) -> pd.DataFrame:
    m = load(sym, "H1")
    t = m["t"] + 3600  # H1 close = decision time (as bensim.edge_miner)
    o, h, lo, c = m["open"], m["high"], m["low"], m["close"]
    n = len(c)
    tr = np.maximum(h - lo, np.maximum(np.abs(h - np.r_[c[0], c[:-1]]), np.abs(lo - np.r_[c[0], c[:-1]])))
    atr = pd.Series(tr).rolling(14, min_periods=5).mean().to_numpy()
    out = {k: np.zeros(n) for k in SMC}
    sh, sl = np.nan, np.nan  # last confirmed swing high / low
    swings_h, swings_l = [], []
    trend, last_choch, since, brk_lvl = 0, 0, 200, np.nan
    bull, bear = [], []  # unfilled FVGs: (bar, bottom, top)
    day = (t - 3600) // 86400
    dh = pd.Series(h).groupby(day).transform("max").to_numpy()
    dl = pd.Series(lo).groupby(day).transform("min").to_numpy()
    first = np.r_[0, np.flatnonzero(np.diff(day)) + 1]
    prev_hi, prev_lo = np.full(n, np.nan), np.full(n, np.nan)
    for k in range(1, len(first)):
        a, b = first[k], (first[k + 1] if k + 1 < len(first) else n)
        prev_hi[a:b], prev_lo[a:b] = dh[first[k - 1]], dl[first[k - 1]]
    sweep = np.where((lo < prev_lo) & (c > prev_lo), 1, np.where((h > prev_hi) & (c < prev_hi), -1, 0))
    body = np.abs(c - o) / np.maximum(h - lo, 1e-12)
    disp = np.where(body >= 0.7, (h - lo) / np.maximum(atr, 1e-12) * np.sign(c - o), 0.0)
    for i in range(n):
        a = atr[i] if np.isfinite(atr[i]) and atr[i] > 0 else np.nan
        j = i - 2  # fractal at j is confirmed now
        if j >= 2:
            if h[j] > max(h[j - 2], h[j - 1], h[j + 1], h[j + 2]):
                sh = h[j]
                swings_h = (swings_h + [h[j]])[-10:]
            if lo[j] < min(lo[j - 2], lo[j - 1], lo[j + 1], lo[j + 2]):
                sl = lo[j]
                swings_l = (swings_l + [lo[j]])[-10:]
        d = 1 if (np.isfinite(sh) and c[i] > sh) else (-1 if (np.isfinite(sl) and c[i] < sl) else 0)
        if d:
            last_choch = int(trend != 0 and d != trend)
            trend, since, brk_lvl = d, 0, (sh if d == 1 else sl)
            if d == 1:
                sh = np.nan  # a level breaks once
            else:
                sl = np.nan
        else:
            since = min(since + 1, 200)
        if i >= 2:
            if lo[i] > h[i - 2]:
                bull.append((i, h[i - 2], lo[i]))
            if h[i] < lo[i - 2]:
                bear.append((i, h[i], lo[i - 2]))
        bull = [f for f in bull if lo[i] >= f[1] and i - f[0] <= 100]  # filled when a low trades below its bottom
        bear = [f for f in bear if h[i] <= f[2] and i - f[0] <= 100]
        if not np.isfinite(a):
            continue
        out["smc_trend"][i], out["smc_last_choch"][i], out["smc_bars_since_break"][i] = trend, last_choch, since
        out["smc_dist_break"][i] = (c[i] - brk_lvl) / a * trend if np.isfinite(brk_lvl) else 0.0
        below = [f[2] for f in bull if f[2] <= c[i]]
        above = [f[1] for f in bear if f[1] >= c[i]]
        out["smc_fvg_bull_dist"][i] = min(10.0, (c[i] - max(below)) / a) if below else 10.0
        out["smc_fvg_bear_dist"][i] = min(10.0, (min(above) - c[i]) / a) if above else 10.0
        out["smc_in_fvg"][i] = 1 if any(f[1] <= c[i] <= f[2] for f in bull) else (-1 if any(f[1] <= c[i] <= f[2] for f in bear) else 0)
        out["smc_fvg_new"][i] = (1 if bull and i - bull[-1][0] < 3 else 0) - (1 if bear and i - bear[-1][0] < 3 else 0)
        out["smc_fvg_open"][i] = len(bull) - len(bear)
        out["smc_displacement"][i] = max(disp[max(0, i - 2):i + 1], key=abs)
        out["smc_sweep_pd"][i] = next((s for s in sweep[max(0, i - 2):i + 1][::-1] if s), 0)
        eqh = [x for p, x in enumerate(swings_h) for y in swings_h[p + 1:] if abs(x - y) <= 0.1 * a and x > c[i]]
        eql = [x for p, x in enumerate(swings_l) for y in swings_l[p + 1:] if abs(x - y) <= 0.1 * a and x < c[i]]
        out["smc_eq_high_dist"][i] = min(10.0, (min(eqh) - c[i]) / a) if eqh else 10.0
        out["smc_eq_low_dist"][i] = min(10.0, (c[i] - max(eql)) / a) if eql else 10.0
    return pd.DataFrame({"t": pd.to_datetime(t, unit="s"), "symbol": sym, **out})


def build() -> None:
    pairs = sorted(p.stem.split("_")[0] for p in OUT.glob("*_H1.npz"))
    frames = []
    for s in pairs:
        frames.append(pair_features(s))
        print(f"  {s}: {len(frames[-1])} bars", flush=True)
    with open(PATH, "wb") as fh:
        pickle.dump(pd.concat(frames, ignore_index=True), fh)


def run(h: int = 6, seeds: tuple[int, ...] = (0, 7, 13, 21, 42)) -> None:
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.inspection import permutation_importance

    from . import edge_wf as wf
    from . import macro_yields as my
    from .edge_miner import FEATURES
    from .edge_miner import PATH as EM
    with open(EM, "rb") as fh:  # our own caches
        f = pickle.load(fh)
    with open(PATH, "rb") as fh:
        smc = pickle.load(fh)
    f = f.merge(smc, on=["t", "symbol"], how="left")
    y = pd.read_csv(my.OUT / "yields_2y.csv", index_col=0, parse_dates=True).ffill(limit=5).shift(1)
    day = f.t.dt.normalize()
    for cc in ("level", "d5", "d20", "d60"):
        f[f"gap_{cc}"] = np.nan
    for sym in f.symbol.unique():
        b, q = sym[:3], sym[3:]
        if b in y and q in y:
            g, k = y[b] - y[q], f.symbol == sym
            f.loc[k, "gap_level"] = g.reindex(day[k], method="ffill").to_numpy()
            for nn in (5, 20, 60):
                f.loc[k, f"gap_d{nn}"] = (g - g.shift(nn)).reindex(day[k], method="ffill").to_numpy()
    base = FEATURES + ["gap_level", "gap_d5", "gap_d20", "gap_d60"]
    wf.H = h
    for label, feats in (("rate-aware model (control)", base), ("rate-aware + SMC inputs", base + SMC)):
        rows, last = [], None
        for yr in range(2016, int(f.year.max()) + 1):
            tr = f[f.year < yr - 1]
            tr = tr[np.isfinite(tr[f"long_{h}"]) & np.isfinite(tr[f"short_{h}"])]
            prev = f[(f.year == yr - 1) & f.symbol.isin(my.PAIRS)]
            cur = f[(f.year == yr) & f.symbol.isin(my.PAIRS)]
            pl_p = ps_p = pl = ps = 0.0
            for s in seeds:
                mk = lambda yy: HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,  # noqa: E731
                                                              min_samples_leaf=2000, l2_regularization=1.0, random_state=s).fit(tr[feats], yy)
                ml_, ms_ = mk(np.clip(tr[f"long_{h}"], -300, 300)), mk(np.clip(tr[f"short_{h}"], -300, 300))
                pl_p, ps_p = pl_p + ml_.predict(prev[feats]) / len(seeds), ps_p + ms_.predict(prev[feats]) / len(seeds)
                pl, ps = pl + ml_.predict(cur[feats]) / len(seeds), ps + ms_.predict(cur[feats]) / len(seeds)
                last = (ml_, cur)
            thr = float(np.quantile(np.maximum(pl_p, ps_p), 0.95))
            rows.append(wf.trades(cur, pl, ps, thr))
        t = pd.concat(rows, ignore_index=True)
        ys = t.groupby("year").bp.mean()
        post = t[t.year >= 2021].bp
        print(f"\n{label}: {len(t)} tr avg {t.bp.mean():+.2f} bp t {t.bp.mean() / t.bp.std() * np.sqrt(len(t)):+.2f} years+ {(ys > 0).sum()}/{len(ys)} "
              f"| 2021-now {post.mean():+.2f} bp t {post.mean() / post.std() * np.sqrt(len(post)):+.2f} years+ {(ys.loc[2021:] > 0).sum()}/{len(ys.loc[2021:])}")
        print("   per year: " + " ".join(f"{k}:{v:+.2f}" for k, v in ys.items()), flush=True)
        t.to_csv(f"data/smc_run_{'smc' if len(feats) > len(base) else 'control'}.csv", index=False)
        if len(feats) > len(base) and last is not None:  # which inputs does the latest model use?
            ml_, cur = last
            smp = cur.sample(min(len(cur), 40000), random_state=0)
            imp = permutation_importance(ml_, smp[feats], np.clip(smp[f"long_{h}"].fillna(0), -300, 300), n_repeats=3, random_state=0)
            top = pd.Series(imp.importances_mean, index=feats).sort_values(ascending=False)
            print("   most important inputs (latest model): " + ", ".join(f"{k} {v:.4f}" for k, v in top.head(12).items()))
            print("   SMC inputs ranked: " + ", ".join(f"{k} #{list(top.index).index(k) + 1}" for k in SMC))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "run"])
    a = ap.parse_args()
    os.environ.setdefault("OMP_NUM_THREADS", "3")
    {"build": build, "run": run}[a.cmd]()


if __name__ == "__main__":
    main()
