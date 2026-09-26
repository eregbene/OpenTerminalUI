"""Audit the mean_reversion ML result (top-20% +0.52R / +0.91R in 2023 / 2024) before trusting it.

A. Entry alignment: does the fingerprint entry equal the close of the last CLOSED bar (at - 15m), or of the
   bar that opens at `at` (future)?
B. Strict relabel from raw FOREXSB M15: start at the bar opening at `at` (the stored label skips it:
   outcomes._future_candles_with_quality uses timestamp > entry_time), same-bar stop+target = loss,
   stops/targets hit on the ask side for shorts (high/low + spread), costs charged.
C. Walk-forward with strict labels, plus permutation importance of the 2024 model.
D. Model top-20% by cost/risk quartile (are winners just tiny-stop trades?).
E. Train on FOREXSB 2021-2024 (strict labels), test on MT5 2025-2026 mean_reversion fingerprints (stored labels).

Run: python scripts/ml_mean_reversion_audit.py
"""
from __future__ import annotations

import sys
from bisect import bisect_left
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scripts.bsi_v3_august_validation as v3  # noqa: E402
import scripts.ml_walk_forward_hgb as wf  # noqa: E402

STRATEGY = "mean_reversion"
MAX_BARS = 800  # same lookforward as outcomes._MAX_LOOKFORWARD_BARS
FEATURES = wf.CATEGORICAL + wf.BOOLEAN + wf.NUMERIC


def forexsb_m15(store: v3.CandleStore, symbol: str) -> tuple[list[v3.Bar], list[datetime]]:
    rows = store._pg.execute(
        """select timestamp, open, high, low, close from mt5_canonical_candles
           where provider='FOREXSB' and broker_symbol=%s and timeframe='M15'
             and timestamp >= '2020-12-01' and timestamp < '2025-03-01'
             and extract(epoch from timestamp)::bigint %% 900 = 0
           order by timestamp""",
        (symbol,),
    ).fetchall()
    bars = [v3.Bar(v3.utc(t), float(o), float(h), float(l), float(c)) for t, o, h, l, c in rows]
    return bars, [b.time for b in bars]


def strict_outcome(bars: list[v3.Bar], times: list[datetime], at: datetime, entry: float, stop: float, target: float, spread: float) -> tuple[float | None, str]:
    i = bisect_left(times, at)  # first bar opening at/after the decision instant -- included
    if i >= len(bars) or times[i] - at > timedelta(hours=72):
        return None, "no_data"
    long = stop < entry
    risk = abs(entry - stop)
    for b in bars[i : i + MAX_BARS]:
        if long:
            hit_stop, hit_tp = b.low <= stop, b.high >= target
        else:  # short exits buy at the ask
            hit_stop, hit_tp = b.high + spread >= stop, b.low + spread <= target
        if hit_stop:
            return -1.0, "sl_same_bar" if hit_tp else "sl"
        if hit_tp:
            return abs(target - entry) / risk, "tp"
    last = bars[min(len(bars), i + MAX_BARS) - 1]
    return ((last.close - entry) if long else (entry - last.close)) / risk, "timeout"


def main() -> None:
    store = v3.CandleStore()
    df = wf.load(False, "and f.anchor_strategy = %s and ((f.provider='FOREXSB' and f.entry_time >= '2021-01-01' and f.entry_time < '2025-01-01') or (f.provider='MT5' and f.entry_time >= '2025-01-01'))", (STRATEGY,))
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    df["risk"] = (df["entry"].astype(float) - df["stop_loss"].astype(float)).abs()
    df["cost_r"] = df["canonical_symbol"].astype(str).map(v3.cost_in_price) / df["risk"]
    fx = df[df["provider"] == "FOREXSB"].copy()
    mt5 = df[df["provider"] == "MT5"].copy()
    print(f"{STRATEGY}: {len(fx)} FOREXSB 2021-2024 setups, {len(mt5)} MT5 2025-2026 setups")

    # Need take_profit for relabeling.
    tp = {
        (v3.utc(t), sym): float(x)
        for t, sym, x in store._pg.execute(
            """select f.entry_time, f.canonical_symbol, f.take_profit from historical_pattern_fingerprints f
               where f.anchor_strategy = %s and f.provider='FOREXSB' and f.entry_time >= '2021-01-01' and f.entry_time < '2025-01-01'""",
            (STRATEGY,),
        ).fetchall()
    }

    align_prev = align_cur = 0
    strict, status = [], []
    for symbol, g in fx.groupby("canonical_symbol", observed=True):
        bars, times = forexsb_m15(store, str(symbol))
        spread = v3.cost_in_price(str(symbol))
        for row in g.itertuples():
            at = row.entry_time.to_pydatetime()
            entry, stop = float(row.entry), float(row.stop_loss)
            j = bisect_left(times, at)
            if 0 < j <= len(bars) and abs(bars[j - 1].close - entry) < 1e-9 and times[j - 1] == at - timedelta(minutes=15):
                align_prev += 1
            if j < len(bars) and times[j] == at and abs(bars[j].close - entry) < 1e-9:
                align_cur += 1
            target = tp.get((at, str(symbol)))
            if target is None:
                strict.append(np.nan)
                status.append("no_tp")
                continue
            r, st = strict_outcome(bars, times, at, entry, stop, target, spread)
            strict.append(np.nan if r is None else r)
            status.append(st)
        del bars, times
    fx["strict_gross"] = strict
    fx["strict_status"] = status
    fx["strict_net"] = fx["strict_gross"] - fx["cost_r"]

    print(f"\nA. entry == close of last CLOSED bar (at-15m): {align_prev}/{len(fx)}   entry == close of bar opening AT `at` (future): {align_cur}/{len(fx)}")
    print(f"\nB. strict relabel status: {fx['strict_status'].value_counts().to_dict()}")
    both = fx.dropna(subset=["strict_net"])
    agree = ((both["outcome_r"].astype(float) > 0) == (both["strict_gross"] > 0)).mean()
    print(f"   stored vs strict win/loss agreement: {100 * agree:.1f}%   stored avg net={both['net_r'].mean():+.3f}R   strict avg net={both['strict_net'].mean():+.3f}R")
    for y, g in both.groupby("year"):
        print(f"   {y}: stored {g['net_r'].mean():+.3f}R  strict {g['strict_net'].mean():+.3f}R  n={len(g)}")

    print("\nC. walk-forward on STRICT labels:")
    both = both.assign(net_r=both["strict_net"])
    tops, model24, test24 = [], None, None
    for year in (2023, 2024):
        print(f"  train 2021..{year - 1}, test {year}")
        model, top = wf.fit_and_score(both[both["year"] < year], both[both["year"] == year])
        tops.append(top)
        model24, test24 = model, both[both["year"] == year].copy()
    top = pd.concat(tops)
    print(f"  TOP20 both years (strict): {wf.stats(top)}")
    for c in wf.CATEGORICAL:
        test24[c] = pd.Categorical(test24[c].astype(str), categories=both[both["year"] < 2024][c].cat.categories)
    imp = permutation_importance(model24, test24[FEATURES], (test24["net_r"] > 0).astype(int), scoring="roc_auc", n_repeats=3, random_state=0)
    order = np.argsort(-imp.importances_mean)[:8]
    print("  2024 model permutation importance (AUC drop): " + ", ".join(f"{FEATURES[i]}={imp.importances_mean[i]:.3f}" for i in order))

    print("\nD. strict TOP20 by cost/risk quartile (high cost_r = tiny stop):")
    top = top.assign(cost_q=pd.qcut(top["cost_r"], 4, labels=["Q1 low cost", "Q2", "Q3", "Q4 high cost"]))
    for q, g in top.groupby("cost_q", observed=True):
        print(f"  {q:<12} cost_r~{g['cost_r'].median():.3f}  {wf.stats(g)}")

    print("\nE. train FOREXSB 2021-2024 (strict), test MT5 2025-2026 (stored labels, costs charged):")
    if len(mt5) > 100:
        wf.fit_and_score(both, mt5)
    else:
        print(f"  only {len(mt5)} MT5 setups -- skipped")


if __name__ == "__main__":
    main()
