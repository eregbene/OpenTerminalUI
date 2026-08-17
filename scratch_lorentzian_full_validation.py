"""Lorentzian Classification -- full-scale validation, roles B/C/D, across multiple strategies
and with the same rigor lesson the squeeze/EQH-EQL work already established: a pooled "SUPPORT
beats baseline" claim is not evidence, only a same-sign-both-halves chronological check is.
Extends scratch_lorentzian_vs_historical_analog.py (which stays as the single-strategy/quick-
smoke-test version) with: (1) a real sample size per strategy, (2) a chronological 50/50 split
of the sampled OOS window itself (checks whether Role B/D's "SUPPORT filter beats baseline"
finding is stable, not a fluke of the particular sample), (3) a regime_broad breakdown (Role D:
regime-dependent use), (4) multiple representative strategies in one run.

Role A (standalone) is a separate script (scratch_lorentzian_standalone.py) -- it needs a raw-bar
walk, not existing fingerprints.

Nothing here writes to any table.
"""
from __future__ import annotations

import bisect
import statistics as pystats
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from backend.brokers.mt5.orm import MT5CanonicalCandleORM
from backend.historical_intelligence import similarity
from backend.historical_intelligence.lorentzian_similarity import lorentzian_statistics
from backend.historical_intelligence.walk_forward import _MIN_OOS_SAMPLE, _MIN_TRAIN_SAMPLE, _PURGE_WINDOW, _fetch_trusted_rows
from backend.market_structure.bar_utils import normalize_bars
from backend.market_structure.oscillators import lorentzian_feature_series
from backend.shared.db import SessionLocal

SYMBOL = "EURUSD"
PROVIDER = "FOREXSB"
TOP_K_HA = 50
K_LORENTZIAN = 8
_MAX_WORKERS = 6

# (strategy_id, sample_every_n) -- N tuned per strategy's own corpus depth so each run lands in
# the same ~300-600 evaluated OOS candidates ballpark regardless of how large that strategy's
# total corpus is.
STRATEGIES = [
    ("mtfai1", 250),
    ("support_resistance_bounce", 40),
    ("smc_continuation", 8),
    ("breakout", 14),
    ("momentum", 30),
]


def load_feature_series():
    print(f"Loading {PROVIDER} M15 canonical candles for {SYMBOL}...", flush=True)
    with SessionLocal() as db:
        rows = (
            db.query(MT5CanonicalCandleORM)
            .filter(
                MT5CanonicalCandleORM.broker_symbol == SYMBOL, MT5CanonicalCandleORM.provider == PROVIDER,
                MT5CanonicalCandleORM.timeframe == "M15", MT5CanonicalCandleORM.quality != "INVALID",
                MT5CanonicalCandleORM.timestamp_utc.isnot(None),
            )
            .order_by(MT5CanonicalCandleORM.timestamp_utc.asc())
            .all()
        )
    print(f"  {len(rows)} bars loaded", flush=True)
    dict_rows = [{"time": r.timestamp_utc, "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.tick_volume} for r in rows]
    bars = normalize_bars(dict_rows, symbol=SYMBOL, timeframe="M15")
    print("Computing RSI/WaveTrend/CCI/ADX Lorentzian feature series (normalize_window=100, matches live)...", flush=True)
    features = lorentzian_feature_series(bars, normalize_window=100)
    close_times = [b.close_time for b in bars]
    return close_times, features


def point_in_time_features(close_times, features, at):
    i = bisect.bisect_right(close_times, at) - 1
    return features[i] if i >= 0 else None


def _dims(fp):
    return similarity._fingerprint_to_dims(fp)


def _stats(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return {"n": 0, "expectancy_r": None, "win_rate": None, "profit_factor": None}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win, gross_loss = sum(wins), abs(sum(losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else None)
    return {"n": n, "expectancy_r": round(sum(rs) / n, 4), "win_rate": round(len(wins) / n, 4), "profit_factor": round(pf, 3) if pf not in (None, float("inf")) else pf}


def _split_half(items: list) -> tuple[list, list]:
    cut = len(items) // 2
    return items[:cut], items[cut:]


def run_strategy(anchor_strategy: str, sample_every_n: int, close_times, features):
    print("\n" + "#" * 110)
    print(f"# {anchor_strategy} / {SYMBOL}")
    print("#" * 110)

    rows = _fetch_trusted_rows(anchor_strategy=anchor_strategy, canonical_symbol=SYMBOL, regime=None, session=None, confidence_band=None, peer_group_hash=None)
    total_n = len(rows)
    print(f"  {total_n} trusted rows", flush=True)
    if total_n < _MIN_TRAIN_SAMPLE + _MIN_OOS_SAMPLE:
        print("  INSUFFICIENT_SAMPLE"); return

    split_index = max(1, min(total_n - 1, int(total_n * 0.6)))
    split_time = rows[split_index][0].entry_time
    train_rows = [(fp, o) for fp, o in rows if fp.entry_time <= split_time - _PURGE_WINDOW]
    oos_rows = [(fp, o) for fp, o in rows if fp.entry_time > split_time + _PURGE_WINDOW]
    print(f"  train_n={len(train_rows)}  oos_n={len(oos_rows)}  split_time={split_time.isoformat()}", flush=True)
    if len(train_rows) < _MIN_TRAIN_SAMPLE or len(oos_rows) < _MIN_OOS_SAMPLE:
        print("  INSUFFICIENT_SAMPLE"); return

    lorentzian_pool: dict[tuple[str, str | None], list[tuple[str, tuple, float | None, bool | None]]] = {}
    for fp, outcome in train_rows:
        feat = point_in_time_features(close_times, features, fp.entry_time)
        if feat is None:
            continue
        key = (fp.direction, fp.regime_broad)
        lorentzian_pool.setdefault(key, []).append((fp.fingerprint_id, feat, outcome.outcome_r, outcome.reached_1r))

    oos_sample = oos_rows[::sample_every_n]
    print(f"  Sampling {len(oos_sample)} of {len(oos_rows)} OOS candidates (every {sample_every_n}th)", flush=True)
    as_of = split_time - _PURGE_WINDOW

    def _evaluate(item):
        fp, outcome = item
        ha = similarity.similarity_statistics(
            canonical_symbol=SYMBOL, direction=fp.direction, anchor_strategy=anchor_strategy,
            strategy_version=fp.strategy_version, query_dims=_dims(fp), regime_broad=fp.regime_broad,
            top_k=TOP_K_HA, as_of=as_of,
        )
        query_feat = point_in_time_features(close_times, features, fp.entry_time)
        lz = {"status": "NO_FEATURE"}
        if query_feat is not None:
            pool = lorentzian_pool.get((fp.direction, fp.regime_broad), [])
            lz = lorentzian_statistics(query_feat, pool, k=K_LORENTZIAN)
        return {
            "fingerprint_id": fp.fingerprint_id, "entry_time": fp.entry_time, "regime_broad": fp.regime_broad,
            "actual_outcome_r": outcome.outcome_r, "actual_reached_1r": outcome.reached_1r, "ha": ha, "lz": lz,
        }

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        results = list(pool.map(_evaluate, oos_sample))
    results.sort(key=lambda r: r["entry_time"])
    print(f"  {len(results)} evaluated", flush=True)

    ha_usable = [r for r in results if r["ha"].get("status") == "OK"]
    lz_usable = [r for r in results if r["lz"].get("status") == "OK"]
    print(f"  Coverage: HA usable={len(ha_usable)}/{len(results)}   Lorentzian usable={len(lz_usable)}/{len(results)}")

    ha_sign_agree = sum(1 for r in ha_usable if (r["ha"].get("weighted_expectancy_r") or 0) != 0 and r["actual_outcome_r"] is not None and ((r["ha"]["weighted_expectancy_r"] > 0) == (r["actual_outcome_r"] > 0)))
    ha_sign_n = sum(1 for r in ha_usable if (r["ha"].get("weighted_expectancy_r") or 0) != 0 and r["actual_outcome_r"] is not None)
    lz_sign_agree = sum(1 for r in lz_usable if (r["lz"].get("expectancy_r") or 0) != 0 and r["actual_outcome_r"] is not None and ((r["lz"]["expectancy_r"] > 0) == (r["actual_outcome_r"] > 0)))
    lz_sign_n = sum(1 for r in lz_usable if (r["lz"].get("expectancy_r") or 0) != 0 and r["actual_outcome_r"] is not None)
    print(f"  Predicted-expectancy-sign agreement: HA={round(ha_sign_agree/ha_sign_n,4) if ha_sign_n else None} (n={ha_sign_n})   Lorentzian={round(lz_sign_agree/lz_sign_n,4) if lz_sign_n else None} (n={lz_sign_n})")

    print("\n  --- ROLE B/D: SUPPORT filter vs baseline, chronological 50/50 split of the OOS sample ---")
    baseline_rs = [r["actual_outcome_r"] for r in results if r["actual_outcome_r"] is not None]
    print(f"  baseline (all)                          {_stats(baseline_rs)}")

    ha_support = [r for r in ha_usable if (r["ha"].get("weighted_expectancy_r") or 0) > 0]
    lz_support = [r for r in lz_usable if (r["lz"].get("expectancy_r") or 0) > 0]
    both_ids = {r["fingerprint_id"] for r in ha_support} & {r["fingerprint_id"] for r in lz_support}
    hybrid_support = sorted([r for r in results if r["fingerprint_id"] in both_ids], key=lambda r: r["entry_time"])

    for label, subset in [("HA-SUPPORT", sorted(ha_support, key=lambda r: r["entry_time"])),
                           ("Lorentzian-SUPPORT", sorted(lz_support, key=lambda r: r["entry_time"])),
                           ("BOTH-SUPPORT (hybrid)", hybrid_support)]:
        first_half, second_half = _split_half(subset)
        all_rs = [r["actual_outcome_r"] for r in subset if r["actual_outcome_r"] is not None]
        h1_rs = [r["actual_outcome_r"] for r in first_half if r["actual_outcome_r"] is not None]
        h2_rs = [r["actual_outcome_r"] for r in second_half if r["actual_outcome_r"] is not None]
        print(f"  {label:24s} all={_stats(all_rs)}")
        print(f"  {'':24s} 1st-half={_stats(h1_rs)}  2nd-half={_stats(h2_rs)}")

    print("\n  --- ROLE D (regime-dependent): Lorentzian-SUPPORT expectancy by regime_broad ---")
    by_regime = Counter(r["regime_broad"] for r in lz_support)
    for regime, _ in by_regime.most_common():
        subset_rs = [r["actual_outcome_r"] for r in lz_support if r["regime_broad"] == regime and r["actual_outcome_r"] is not None]
        print(f"  {str(regime):16s} {_stats(subset_rs)}")


def main():
    close_times, features = load_feature_series()
    for strat, n in STRATEGIES:
        run_strategy(strat, n, close_times, features)


if __name__ == "__main__":
    main()
