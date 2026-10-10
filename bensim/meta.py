"""Meta-labeling, walk-forward: learn which fib-breakout signals pay, using only the past.

    python -m bensim.meta --csv data/bensim_ml_signals_18m.csv --test-from 2026-04-01

Primary model = the plain fib 61.8% strategy (every trade it takes, after costs, from `backtest --csv`).
Secondary model = logistic regression on what was known at the fill (SMC checks, H4/D1 context, news, hour)
predicting "this trade ends positive". For each test month it is trained only on trades that were already
CLOSED before the month began (purged) from the previous `train_days`, then it decides take / skip for every
trade of that month. Compared per month with: all trades, the poi+killzone rule, and model + rule.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from . import db
from .trade_report import features

FEATS = ["chk_swing_trend", "choch", "bos", "chk_fvg", "chk_ob", "chk_discount", "swept", "chk_killzone", "liq_ahead", "rr_planned",
         "h4_swing_agrees", "h4_internal_agrees", "d1_trend_agrees", "d1_trend_eff", "h4_poi_with", "h4_zone_against", "h4_discount",
         "news_60m_fill", "london", "newyork", "is_gold"]
VOL_FEATS = ["vol_break", "vol_pullback"]


def volume_features(f: pd.DataFrame) -> pd.DataFrame:
    """Tick volume (MT5, broker-specific) of the M15 breakout bar and of the pullback until the fill, each relative to
    the 20 M15 bars before the signal. Known at the fill: only bars before t_entry are used."""
    out = []
    for sym, g in f.groupby("symbol"):
        with db.connect() as c:
            rows = c.execute("""select (extract(epoch from ts)::bigint / 900) * 900, sum(tick_volume) from candles where symbol=%s and tf='M5'
                                and ts >= to_timestamp(%s) and ts < to_timestamp(%s) group by 1 order by 1""",
                             (sym, int(g.t_signal.min()) - 30 * 86400, int(g.t_entry.max()) + 900)).fetchall()
        t = np.array([r[0] for r in rows], dtype=np.int64)
        v = np.array([r[1] for r in rows], dtype=float)
        for row in g.itertuples():
            i = int(np.searchsorted(t, row.t_signal))
            k = int(np.searchsorted(t, (row.t_entry - 300) // 900 * 900))
            base = v[max(0, i - 20):i].mean() if i > 0 else np.nan
            pull = v[i + 1:k].mean() if k > i + 1 else v[i] if i < len(v) else np.nan
            out.append({"symbol": sym, "t_entry": row.t_entry, "vol_break": v[i] / base if i < len(v) and base else 1.0,
                        "vol_pullback": pull / base if base else 1.0})
    return f.merge(pd.DataFrame(out), on=["symbol", "t_entry"], how="left").fillna({"vol_break": 1.0, "vol_pullback": 1.0})


def prepare(csv: str) -> pd.DataFrame:
    t = pd.read_csv(csv)
    f = features(t[["symbol", "dir", "t_entry", "t_exit", "entry", "sl", "tp", "exit_reason", "r"]].assign(run=0, period=""))
    f = f.merge(t.drop(columns=["entry", "sl", "tp", "exit_reason", "r", "dir", "t_exit"]), on=["symbol", "t_entry"])
    f["choch"] = f.chk_internal_break.eq("CHoCH")
    f["bos"] = f.chk_internal_break.eq("BOS")
    f["swept"] = f.chk_sweep.ne("none")
    f["liq_ahead"] = f.chk_liq_ahead_r.fillna(10).clip(upper=10)
    f["london"] = f.fill_hour.between(7, 11)
    f["newyork"] = f.fill_hour.between(12, 16)
    f["is_gold"] = f.symbol.eq("XAUUSD")
    f["rule"] = (f.chk_fvg | f.chk_ob) & f.chk_killzone
    f["month"] = pd.to_datetime(f.t_entry, unit="s", utc=True).dt.strftime("%Y-%m")
    f = volume_features(f)
    f[VOL_FEATS] = f[VOL_FEATS].clip(upper=5)
    return f.sort_values("t_entry").reset_index(drop=True)


def walk_forward(f: pd.DataFrame, test_from: str, train_days: int = 365, feats: list[str] = FEATS) -> pd.DataFrame:
    out = []
    for month in sorted(m for m in f.month.unique() if m >= test_from[:7]):
        start = int(pd.Timestamp(month + "-01", tz="UTC").timestamp())
        train = f[(f.t_exit < start) & (f.t_entry >= start - train_days * 86400)]  # purged: every label known before the month
        test = f[f.month == month]
        assert (train.t_exit < test.t_entry.min()).all()
        model = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=2000))
        model.fit(train[feats].astype(float), train.r > 0)
        p = model.predict_proba(test[feats].astype(float))[:, 1]
        win, loss = train.r[train.r > 0].mean(), -train.r[train.r <= 0].mean()
        exp_r = p * win - (1 - p) * loss  # expected R from the training month's own win/loss sizes
        out.append(test.assign(p=p, exp_r=exp_r, take=exp_r > 0, n_train=len(train)))
    return pd.concat(out)


def line(g: pd.DataFrame) -> str:
    return f"n={len(g):3d} win={g.r.gt(0).mean() * 100 if len(g) else 0:3.0f}% avgR={g.r.mean() if len(g) else 0:+.2f} totR={g.r.sum():+6.1f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="data/bensim_ml_signals_18m.csv")
    ap.add_argument("--test-from", default="2026-04-01")
    ap.add_argument("--train-days", type=int, default=365)
    ap.add_argument("--volume", action="store_true", help="add the tick-volume features")
    a = ap.parse_args()
    f = prepare(a.csv)
    feats = FEATS + VOL_FEATS if a.volume else FEATS
    w = walk_forward(f, a.test_from, a.train_days, feats)
    w.to_csv("data/bensim_meta_walkforward.csv", index=False)
    print(f"{len(f)} trades in dataset; tested {len(w)} trades in {w.month.nunique()} unseen months (train window {a.train_days}d, purged)")
    print(f"{'month':8} | {'ALL plain fib':38} | {'RULE poi+killzone':38} | {'MODEL':38} | MODEL + RULE")
    for m, g in list(w.groupby("month")) + [("TOTAL", w)]:
        print(f"{m:8} | {line(g):38} | {line(g[g.rule]):38} | {line(g[g["take"]]):38} | {line(g[g["take"] & g.rule])}")
    last = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=2000)).fit(f[feats].astype(float), f.r > 0)
    coef = pd.Series(last[-1].coef_[0], feats).sort_values()
    print("\nWhat the model leans on (all data, standardized log-odds; + = more likely a winner):")
    print("  " + "  ".join(f"{k}={v:+.2f}" for k, v in coef.items()))


if __name__ == "__main__":
    main()
