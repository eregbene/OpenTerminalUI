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

import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .trade_report import features

FEATS = ["chk_swing_trend", "choch", "bos", "chk_fvg", "chk_ob", "chk_discount", "swept", "chk_killzone", "liq_ahead", "rr_planned",
         "h4_swing_agrees", "h4_internal_agrees", "d1_trend_agrees", "d1_trend_eff", "h4_poi_with", "h4_zone_against", "h4_discount",
         "news_60m_fill", "london", "newyork", "is_gold"]


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
    return f.sort_values("t_entry").reset_index(drop=True)


def walk_forward(f: pd.DataFrame, test_from: str, train_days: int = 365) -> pd.DataFrame:
    out = []
    for month in sorted(m for m in f.month.unique() if m >= test_from[:7]):
        start = int(pd.Timestamp(month + "-01", tz="UTC").timestamp())
        train = f[(f.t_exit < start) & (f.t_entry >= start - train_days * 86400)]  # purged: every label known before the month
        test = f[f.month == month]
        assert (train.t_exit < test.t_entry.min()).all()
        model = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=2000))
        model.fit(train[FEATS].astype(float), train.r > 0)
        p = model.predict_proba(test[FEATS].astype(float))[:, 1]
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
    a = ap.parse_args()
    f = prepare(a.csv)
    w = walk_forward(f, a.test_from, a.train_days)
    w.to_csv("data/bensim_meta_walkforward.csv", index=False)
    print(f"{len(f)} trades in dataset; tested {len(w)} trades in {w.month.nunique()} unseen months (train window {a.train_days}d, purged)")
    print(f"{'month':8} | {'ALL plain fib':38} | {'RULE poi+killzone':38} | {'MODEL':38} | MODEL + RULE")
    for m, g in list(w.groupby("month")) + [("TOTAL", w)]:
        print(f"{m:8} | {line(g):38} | {line(g[g.rule]):38} | {line(g[g["take"]]):38} | {line(g[g["take"] & g.rule])}")
    last = make_pipeline(StandardScaler(), LogisticRegression(C=0.3, max_iter=2000)).fit(f[FEATS].astype(float), f.r > 0)
    coef = pd.Series(last[-1].coef_[0], FEATS).sort_values()
    print("\nWhat the model leans on (all data, standardized log-odds; + = more likely a winner):")
    print("  " + "  ".join(f"{k}={v:+.2f}" for k, v in coef.items()))


if __name__ == "__main__":
    main()
