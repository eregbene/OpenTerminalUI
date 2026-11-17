"""Walk-forward of the edge-miner model as it would have run live: each year retrained on ALL prior years, threshold set
on the prior year, traded on the next year -- cheap pairs, 6-hour horizon. Plus what the model looks at.

    python -m bensim.edge_wf

Fixed before running: train = every pair, all years < Y-1 ; threshold = 95th percentile of the model's best-side
prediction on year Y-1 (the most recent year, not used for training) ; trade year Y on CHEAP pairs, one open trade per
pair, 6h hold, mid prices, real spread. Years 2016..now (first train 2012-2014). Report per year, total, and the
features the 2019-trained model relies on (permutation importance on 2019).
"""
from __future__ import annotations

import os
import pickle

import numpy as np
import pandas as pd

from .edge_miner import CHEAP, FEATURES, PATH

H = 6


def fit(tr: pd.DataFrame, y: np.ndarray):
    from sklearn.ensemble import HistGradientBoostingRegressor
    return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31, min_samples_leaf=2000,
                                         l2_regularization=1.0, random_state=0).fit(tr[FEATURES], y)


def trades(df: pd.DataFrame, pl: np.ndarray, ps: np.ndarray, thr: float) -> pd.DataFrame:
    out = []
    for sym, g in df.assign(pl=pl, ps=ps).groupby("symbol"):
        busy = pd.Timestamp(0)
        for x in g.itertuples():
            if x.t < busy or max(x.pl, x.ps) <= thr:
                continue
            side = "long" if x.pl >= x.ps else "short"
            r = getattr(x, f"{side}_{H}")
            if np.isfinite(r):
                out.append((sym, x.t, x.year, x.hour, side, r))
                busy = x.t + pd.Timedelta(hours=H)
    return pd.DataFrame(out, columns=["symbol", "t", "year", "hour", "side", "bp"])


def main(window: int = 0) -> None:
    """window > 0: train only on the last `window` years before Y-1 (adapts to regime changes); 0 = all history."""
    os.environ.setdefault("OMP_NUM_THREADS", "3")
    with open(PATH, "rb") as fh:  # our own cache from edge_miner build
        f = pickle.load(fh)
    all_t = []
    for y in range(2016, int(f.year.max()) + 1):
        tr = f[(f.year < y - 1) & (f.year >= (y - 1 - window if window else 0))]
        ok = np.isfinite(tr[f"long_{H}"]) & np.isfinite(tr[f"short_{H}"])
        tr = tr[ok]
        ml, ms = fit(tr, np.clip(tr[f"long_{H}"], -300, 300)), fit(tr, np.clip(tr[f"short_{H}"], -300, 300))
        prev = f[(f.year == y - 1) & f.symbol.isin(CHEAP)]
        thr = float(np.quantile(np.maximum(ml.predict(prev[FEATURES]), ms.predict(prev[FEATURES])), 0.95))
        cur = f[(f.year == y) & f.symbol.isin(CHEAP)]
        t = trades(cur, ml.predict(cur[FEATURES]), ms.predict(cur[FEATURES]), thr)
        all_t.append(t)
        print(f"  {y}: trained on <= {y - 2}, threshold {thr:.2f} bp from {y - 1}: {len(t)} trades avg {t.bp.mean():+.2f} bp "
              f"total {t.bp.sum():+.0f} bp", flush=True)
        if y == 2020 and not window:  # what does a model trained up to 2018 look at? (importance on 2019)
            from sklearn.inspection import permutation_importance
            smp = prev.sample(min(len(prev), 60000), random_state=0)
            imp = permutation_importance(ml, smp[FEATURES], np.clip(smp[f"long_{H}"].fillna(0), -300, 300), n_repeats=3, random_state=0)
            top = pd.Series(imp.importances_mean, index=FEATURES).sort_values(ascending=False)
            print("  long-model feature importance (2019): " + ", ".join(f"{k} {v:.4f}" for k, v in top.head(10).items()))
    t = pd.concat(all_t, ignore_index=True)
    t.to_csv(f"data/edge_wf_trades_w{window}.csv", index=False)
    ys = t.groupby("year").bp.mean()
    print(f"\nWALK-FORWARD 2016-now: {len(t)} trades avg {t.bp.mean():+.2f} bp t {t.bp.mean() / t.bp.std() * np.sqrt(len(t)):+.2f} "
          f"years+ {(ys > 0).sum()}/{len(ys)}")
    print("  by pair: " + ", ".join(f"{k} {v:+.2f}" for k, v in t.groupby("symbol").bp.mean().items()))
    hb = t.groupby("hour").bp.agg(["count", "mean"])
    print("  by entry hour (UTC): " + " ".join(f"{h}:{int(n)}/{m:+.1f}" for h, (n, m) in hb.iterrows()))
    print("  by side: " + ", ".join(f"{k} {v:+.2f}" for k, v in t.groupby("side").bp.mean().items()))


if __name__ == "__main__":
    import sys
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
