"""Lorentzian Classification Stage 2, roles B/C/D: head-to-head against Bensim's real production
Historical Analog engine (backend.historical_intelligence.similarity.similarity_statistics),
on IDENTICAL candidates, IDENTICAL target (+1R / outcome_r), IDENTICAL chronological train/OOS
split and purge window, using the SAME hard filters (symbol, direction, anchor_strategy,
strategy_version, regime_broad, resolution_status, data_quality) similarity_oos.calibration_check
already establishes as this codebase's own walk-forward convention for evaluating this engine --
reused here verbatim, not reinvented, so this comparison is exactly as point-in-time-safe as
Historical Analog's own existing OOS validation.

Role C: for each sampled OOS-period mtfai1/EURUSD candidate, get BOTH engines' prediction --
Historical Analog via the real, unmodified similarity_statistics() call (a genuine DB round trip,
exactly what a live/replay caller would get); Lorentzian via an in-memory k=8 nearest-neighbor
search over the SAME train-period pool, hard-filtered identically. Reports coverage, calibration,
and expectancy-sign agreement with the real outcome for both.

Role B: for each engine, does restricting to candidates the engine itself says have positive
predicted expectancy ("SUPPORT") outperform the unfiltered baseline?

Role D: hybrid -- candidates where BOTH engines say SUPPORT.

Role A (standalone) is intentionally NOT in this script -- it needs a different, raw-bar-walk
harness (no existing fingerprint/candidate to evaluate) and is built separately.

Nothing here writes to any table (similarity_statistics() is read-only).
"""
from __future__ import annotations

import bisect
import statistics as pystats
import sys
from concurrent.futures import ThreadPoolExecutor

from backend.brokers.mt5.orm import MT5CanonicalCandleORM
from backend.historical_intelligence import similarity
from backend.historical_intelligence.lorentzian_similarity import lorentzian_statistics
from backend.historical_intelligence.walk_forward import _MIN_OOS_SAMPLE, _MIN_TRAIN_SAMPLE, _PURGE_WINDOW, _fetch_trusted_rows
from backend.market_structure.bar_utils import normalize_bars
from backend.market_structure.oscillators import lorentzian_feature_series
from backend.shared.db import SessionLocal

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "EURUSD"
ANCHOR_STRATEGY = sys.argv[2] if len(sys.argv) > 2 else "mtfai1"
PROVIDER = "FOREXSB"
TOP_K_HA = 50   # matches similarity_oos.calibration_check's own default
K_LORENTZIAN = 8  # jdehorty's own public default
SAMPLE_EVERY_N = int(sys.argv[3]) if len(sys.argv) > 3 else 20  # bound the number of real DB round trips
_MAX_WORKERS = 6


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
    if i < 0:
        return None
    return features[i]


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


def main():
    close_times, features = load_feature_series()

    print(f"Fetching trusted rows for {ANCHOR_STRATEGY}/{SYMBOL}...", flush=True)
    rows = _fetch_trusted_rows(anchor_strategy=ANCHOR_STRATEGY, canonical_symbol=SYMBOL, regime=None, session=None, confidence_band=None, peer_group_hash=None)
    total_n = len(rows)
    print(f"  {total_n} trusted rows", flush=True)
    if total_n < _MIN_TRAIN_SAMPLE + _MIN_OOS_SAMPLE:
        print("INSUFFICIENT_SAMPLE"); return

    split_index = max(1, min(total_n - 1, int(total_n * 0.6)))
    split_time = rows[split_index][0].entry_time
    train_rows = [(fp, o) for fp, o in rows if fp.entry_time <= split_time - _PURGE_WINDOW]
    oos_rows = [(fp, o) for fp, o in rows if fp.entry_time > split_time + _PURGE_WINDOW]
    print(f"  train_n={len(train_rows)}  oos_n={len(oos_rows)}  split_time={split_time.isoformat()}  purge={_PURGE_WINDOW}", flush=True)
    if len(train_rows) < _MIN_TRAIN_SAMPLE or len(oos_rows) < _MIN_OOS_SAMPLE:
        print("INSUFFICIENT_SAMPLE"); return

    print("Building point-in-time Lorentzian train pool (grouped by direction, regime_broad)...", flush=True)
    lorentzian_pool: dict[tuple[str, str | None], list[tuple[str, tuple, float | None, bool | None]]] = {}
    skipped_no_feature = 0
    for fp, outcome in train_rows:
        feat = point_in_time_features(close_times, features, fp.entry_time)
        if feat is None:
            skipped_no_feature += 1
            continue
        key = (fp.direction, fp.regime_broad)
        lorentzian_pool.setdefault(key, []).append((fp.fingerprint_id, feat, outcome.outcome_r, outcome.reached_1r))
    print(f"  pool groups={len(lorentzian_pool)}  skipped(no feature -- too early for a closed bar)={skipped_no_feature}", flush=True)

    oos_sample = oos_rows[::SAMPLE_EVERY_N]
    print(f"Sampling {len(oos_sample)} of {len(oos_rows)} OOS candidates (every {SAMPLE_EVERY_N}th, chronological)...", flush=True)

    as_of = split_time - _PURGE_WINDOW

    def _evaluate(item):
        fp, outcome = item
        ha = similarity.similarity_statistics(
            canonical_symbol=SYMBOL, direction=fp.direction, anchor_strategy=ANCHOR_STRATEGY,
            strategy_version=fp.strategy_version, query_dims=_dims(fp), regime_broad=fp.regime_broad,
            top_k=TOP_K_HA, as_of=as_of,
        )
        query_feat = point_in_time_features(close_times, features, fp.entry_time)
        lz = {"status": "NO_FEATURE"}
        if query_feat is not None:
            pool = lorentzian_pool.get((fp.direction, fp.regime_broad), [])
            lz = lorentzian_statistics(query_feat, pool, k=K_LORENTZIAN)
        return {
            "fingerprint_id": fp.fingerprint_id, "entry_time": fp.entry_time,
            "actual_outcome_r": outcome.outcome_r, "actual_reached_1r": outcome.reached_1r,
            "ha": ha, "lz": lz,
        }

    print("Running head-to-head evaluation (real DB calls for Historical Analog side)...", flush=True)
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        results = list(pool.map(_evaluate, oos_sample))
    print(f"  {len(results)} evaluated", flush=True)

    ha_usable = [r for r in results if r["ha"].get("status") == "OK"]
    lz_usable = [r for r in results if r["lz"].get("status") == "OK"]
    print(f"\nCoverage: Historical Analog usable={len(ha_usable)}/{len(results)} ({round(100*len(ha_usable)/len(results),1)}%)   Lorentzian usable={len(lz_usable)}/{len(results)} ({round(100*len(lz_usable)/len(results),1)}%)")

    print("\n" + "=" * 100)
    print(f"ROLE C: alternative HI similarity engine -- {ANCHOR_STRATEGY}/{SYMBOL}, OOS, as_of={as_of.isoformat()}")
    print("=" * 100)

    def _calibration(usable, prob_key):
        buckets: dict[str, list[tuple[float, bool]]] = {}
        for r in usable:
            p = r["ha" if prob_key == "weighted_probability_1r" else "lz"].get(prob_key)
            actual = r["actual_reached_1r"]
            if p is None or actual is None:
                continue
            idx = min(4, int(p / 0.2))
            key = f"{round(idx*0.2,1)}-{round((idx+1)*0.2,1)}"
            buckets.setdefault(key, []).append((p, bool(actual)))
        total_abs_err, total_n = 0.0, 0
        report = {}
        for key, items in sorted(buckets.items()):
            n = len(items)
            avg_p = sum(p for p, _ in items) / n
            actual_rate = sum(1 for _, a in items if a) / n
            report[key] = {"n": n, "avg_predicted": round(avg_p, 3), "actual_rate": round(actual_rate, 3)}
            total_abs_err += abs(avg_p - actual_rate) * n
            total_n += n
        mae = round(total_abs_err / total_n, 4) if total_n else None
        return report, mae

    ha_calib, ha_mae = _calibration(ha_usable, "weighted_probability_1r")
    lz_calib, lz_mae = _calibration(lz_usable, "probability_1r")
    print(f"\nHistorical Analog +1R calibration (n={len(ha_usable)}, mean_abs_calibration_error={ha_mae}):")
    for k, v in ha_calib.items():
        print(f"  {k}: {v}")
    print(f"\nLorentzian +1R calibration (n={len(lz_usable)}, mean_abs_calibration_error={lz_mae}):")
    for k, v in lz_calib.items():
        print(f"  {k}: {v}")

    ha_ess = [r["ha"]["effective_sample_size"] for r in ha_usable]
    lz_ess = [r["lz"]["effective_sample_size"] for r in lz_usable]
    print(f"\nEffective sample size -- Historical Analog: mean={round(pystats.fmean(ha_ess),1) if ha_ess else None}, median={round(pystats.median(ha_ess),1) if ha_ess else None}")
    print(f"Effective sample size -- Lorentzian:         mean={round(pystats.fmean(lz_ess),1) if lz_ess else None}, median={round(pystats.median(lz_ess),1) if lz_ess else None}  (uniform weight -> always {K_LORENTZIAN} once pool>=k)")

    # Does the engine's predicted expectancy SIGN agree with the realized outcome sign?
    def _sign_agreement(usable, expectancy_key, engine):
        agree = 0
        total = 0
        for r in usable:
            pred = r[engine].get(expectancy_key)
            actual = r["actual_outcome_r"]
            if pred is None or actual is None or pred == 0:
                continue
            total += 1
            if (pred > 0) == (actual > 0):
                agree += 1
        return round(agree / total, 4) if total else None, total

    ha_sign_acc, ha_sign_n = _sign_agreement(ha_usable, "weighted_expectancy_r", "ha")
    lz_sign_acc, lz_sign_n = _sign_agreement(lz_usable, "expectancy_r", "lz")
    print(f"\nPredicted-expectancy-sign vs actual-outcome-sign agreement -- Historical Analog: {ha_sign_acc} (n={ha_sign_n})   Lorentzian: {lz_sign_acc} (n={lz_sign_n})")

    print("\n" + "=" * 100)
    print("ROLE B/D: does filtering to SUPPORT (predicted expectancy > 0) beat baseline? (chronological within the sample)")
    print("=" * 100)
    baseline_rs = [r["actual_outcome_r"] for r in results if r["actual_outcome_r"] is not None]
    print(f"  baseline (all sampled OOS candidates)     {_stats(baseline_rs)}")

    ha_support = [r for r in ha_usable if (r["ha"].get("weighted_expectancy_r") or 0) > 0]
    lz_support = [r for r in lz_usable if (r["lz"].get("expectancy_r") or 0) > 0]
    both_support_ids = {r["fingerprint_id"] for r in ha_support} & {r["fingerprint_id"] for r in lz_support}
    hybrid_support = [r for r in results if r["fingerprint_id"] in both_support_ids]

    print(f"  Historical-Analog-SUPPORT only (role B)   {_stats([r['actual_outcome_r'] for r in ha_support if r['actual_outcome_r'] is not None])}")
    print(f"  Lorentzian-SUPPORT only (role B)          {_stats([r['actual_outcome_r'] for r in lz_support if r['actual_outcome_r'] is not None])}")
    print(f"  BOTH engines SUPPORT (role D, hybrid)     {_stats([r['actual_outcome_r'] for r in hybrid_support if r['actual_outcome_r'] is not None])}")


if __name__ == "__main__":
    main()
