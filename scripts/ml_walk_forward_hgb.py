"""Walk-forward test: can a gradient-boosted model pick winning setups from historical fingerprints?

Train on every year before Y, test on year Y (never seen), for each Y. Outcomes are gross in the DB,
so round-trip costs are charged here (scripts.bsi_v3_august_validation.cost_in_price).
Then train on all FOREXSB years and test on MT5 2025-2026 as a different-source check.

Run:  python scripts/ml_walk_forward_hgb.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scripts.bsi_v3_august_validation as v3  # noqa: E402

CATEGORICAL = [
    "canonical_symbol", "direction", "anchor_strategy", "strategy_family", "regime", "session", "confidence_band",
    "m15_trend", "h1_trend", "h4_trend", "liquidity_location", "fvg_state", "order_block_state",
    "premium_discount_position", "support_resistance_context", "atr_regime", "volatility_regime", "spread_regime",
    "stop_distance_atr_bucket", "planned_rr_bucket", "time_of_day_bucket", "economic_event_context",
]
BOOLEAN = ["bos_present", "choch_present", "mss_present", "displacement_present", "liquidity_sweep_present"]
NUMERIC = ["atr_percentile", "day_of_week"]
TOP_FRACTION = 0.2  # "take" only the model's top 20% of setups


def load() -> pd.DataFrame:
    store = v3.CandleStore()
    cols = ", ".join(f"f.{c}" for c in CATEGORICAL + BOOLEAN + NUMERIC)
    rows = store._pg.execute(
        f"""select f.provider, f.entry_time, f.entry, f.stop_loss, o.outcome_r, {cols}
        from historical_pattern_fingerprints f join historical_setup_outcomes o using (fingerprint_id)
        where o.outcome_r is not null and f.provider in ('FOREXSB', 'MT5')"""
    ).fetchall()
    df = pd.DataFrame(rows, columns=["provider", "entry_time", "entry", "stop_loss", "outcome_r"] + CATEGORICAL + BOOLEAN + NUMERIC)
    risk = (df["entry"].astype(float) - df["stop_loss"].astype(float)).abs()
    cost = df["canonical_symbol"].map(v3.cost_in_price)
    df["net_r"] = df["outcome_r"].astype(float) - cost / risk.where(risk > 0)
    df = df.dropna(subset=["net_r"])
    df["year"] = pd.to_datetime(df["entry_time"], utc=True).dt.year
    for c in CATEGORICAL:
        df[c] = df[c].astype(str).astype("category")
    for c in BOOLEAN + NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def stats(r: pd.Series) -> str:
    if len(r) < 2:
        return f"n={len(r)}"
    t = r.mean() / (r.std() / math.sqrt(len(r))) if r.std() > 0 else float("nan")
    return f"n={len(r):>7}  avg={r.mean():+.3f}R  win={100 * (r > 0).mean():4.1f}%  t={t:+.2f}"


def fit_and_score(train: pd.DataFrame, test: pd.DataFrame) -> None:
    features = CATEGORICAL + BOOLEAN + NUMERIC
    # Keep test categories aligned with train so unseen labels map to missing, not a wrong code.
    test = test.copy()
    for c in CATEGORICAL:
        test[c] = pd.Categorical(test[c].astype(str), categories=train[c].cat.categories)
    model = HistGradientBoostingClassifier(categorical_features="from_dtype", max_iter=300, learning_rate=0.05, random_state=0)
    model.fit(train[features], (train["net_r"] > 0).astype(int))
    score = model.predict_proba(test[features])[:, 1]
    top = test["net_r"][score >= np.quantile(score, 1 - TOP_FRACTION)]
    print(f"    take ALL setups : {stats(test['net_r'])}")
    print(f"    take model TOP20: {stats(top)}")


def main() -> None:
    df = load()
    fx = df[df["provider"] == "FOREXSB"]
    print(f"loaded {len(df)} setups; FOREXSB years {sorted(fx['year'].unique())}")
    for year in sorted(fx["year"].unique())[3:]:
        print(f"\n[walk-forward] train FOREXSB < {year}, test {year}")
        fit_and_score(fx[fx["year"] < year], fx[fx["year"] == year])
    mt5 = df[(df["provider"] == "MT5") & (df["year"] >= 2025)]
    print("\n[different source] train all FOREXSB, test MT5 2025-2026")
    fit_and_score(fx, mt5)


if __name__ == "__main__":
    main()
