"""Multi-neighbor analog intelligence: OOS calibration and old-vs-new model comparison.

Two real, point-in-time-safe questions, both walk-forward (never train-and-test-on-the-same-
period):

1. calibration_check: when the similarity-weighted model says "P(+1R) = 70%" for an OOS-period
   setup, using ONLY historical evidence that existed before that setup's own train/OOS split
   boundary (similarity.find_similar_setups's `as_of` parameter), does approximately 70% of
   comparable OOS setups actually reach +1R?

2. compare_peer_group_vs_similarity: for the same OOS-period setups, does the exact peer-group
   model (statistics.pattern_statistics-style hard hash) or the weighted multi-neighbor model
   produce a usable (sample-reliable) prediction more often, and when both produce one, which is
   closer to the real outcome?

Both are point-in-time-safe by construction: `as_of` is fixed at the train/OOS split boundary for
every OOS-period query, so no query ever sees evidence from after that boundary -- not even
evidence from earlier in the same OOS window than the setup being evaluated.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from backend.historical_intelligence import similarity
from backend.historical_intelligence.walk_forward import _PURGE_WINDOW, _fetch_trusted_rows

_MIN_TRAIN_HISTORY = 20
_MIN_OOS_EVAL = 10


def _dims(fp: Any) -> dict[str, Any]:
    return similarity._fingerprint_to_dims(fp)


def calibration_check(
    anchor_strategy: str, *, probability_field: str = "weighted_probability_1r", actual_field: str = "reached_1r",
    train_fraction: float = 0.6, purge: timedelta = _PURGE_WINDOW, top_k: int = 50, num_buckets: int = 5,
) -> dict[str, Any]:
    """Real walk-forward calibration: for every OOS-period setup, predicts `probability_field`
    using ONLY train-period neighbors (as_of=train/OOS boundary), then buckets by predicted
    probability and compares to the real, actual `actual_field` rate within each bucket."""
    rows = _fetch_trusted_rows(anchor_strategy=anchor_strategy, canonical_symbol=None, regime=None, session=None, confidence_band=None, peer_group_hash=None)
    total_n = len(rows)
    if total_n < _MIN_TRAIN_HISTORY + _MIN_OOS_EVAL:
        return {"anchor_strategy": anchor_strategy, "status": "INSUFFICIENT_SAMPLE", "total_n": total_n}

    split_index = max(1, min(total_n - 1, int(total_n * train_fraction)))
    split_time = rows[split_index][0].entry_time
    train_rows = [(fp, o) for fp, o in rows if fp.entry_time <= split_time - purge]
    oos_rows = [(fp, o) for fp, o in rows if fp.entry_time > split_time + purge]
    if len(train_rows) < _MIN_TRAIN_HISTORY or len(oos_rows) < _MIN_OOS_EVAL:
        return {"anchor_strategy": anchor_strategy, "status": "INSUFFICIENT_SAMPLE", "total_n": total_n, "train_n": len(train_rows), "oos_n": len(oos_rows)}

    predictions: list[tuple[float, bool]] = []
    skipped_no_prediction = 0
    for fp, outcome in oos_rows:
        stats = similarity.similarity_statistics(
            canonical_symbol=fp.canonical_symbol, direction=fp.direction, anchor_strategy=anchor_strategy,
            strategy_version=fp.strategy_version, query_dims=_dims(fp), regime_broad=fp.regime_broad,
            top_k=top_k, as_of=split_time - purge,
        )
        predicted_p = stats.get(probability_field)
        actual = getattr(outcome, actual_field)
        if predicted_p is None or actual is None:
            skipped_no_prediction += 1
            continue
        predictions.append((predicted_p, bool(actual)))

    if not predictions:
        return {"anchor_strategy": anchor_strategy, "status": "NO_PREDICTIONS_AVAILABLE", "total_n": total_n, "oos_n": len(oos_rows), "skipped": skipped_no_prediction}

    bucket_width = 1.0 / num_buckets
    buckets: dict[str, list[tuple[float, bool]]] = {}
    for p, actual in predictions:
        idx = min(num_buckets - 1, int(p / bucket_width))
        key = f"{round(idx * bucket_width, 2)}-{round((idx + 1) * bucket_width, 2)}"
        buckets.setdefault(key, []).append((p, actual))

    bucket_report = {}
    total_abs_error = 0.0
    for key, items in sorted(buckets.items()):
        n = len(items)
        avg_predicted = round(sum(p for p, _ in items) / n, 4)
        actual_rate = round(sum(1 for _, a in items if a) / n, 4)
        bucket_report[key] = {"n": n, "avg_predicted_probability": avg_predicted, "actual_rate": actual_rate, "calibration_error": round(abs(avg_predicted - actual_rate), 4)}
        total_abs_error += abs(avg_predicted - actual_rate) * n

    mean_absolute_calibration_error = round(total_abs_error / len(predictions), 4)
    return {
        "anchor_strategy": anchor_strategy, "status": "OK", "total_n": total_n, "train_n": len(train_rows), "oos_n": len(oos_rows),
        "predictions_made": len(predictions), "skipped_no_prediction": skipped_no_prediction,
        "buckets": bucket_report, "mean_absolute_calibration_error": mean_absolute_calibration_error,
        "well_calibrated": mean_absolute_calibration_error < 0.15,
    }


def compare_peer_group_vs_similarity(
    anchor_strategy: str, *, train_fraction: float = 0.6, purge: timedelta = _PURGE_WINDOW, top_k: int = 50,
) -> dict[str, Any]:
    """For every OOS-period setup: does an EXACT peer_group_hash match within the train-only
    window clear the 100-sample reliability bar? Does the weighted multi-neighbor model (also
    train-only, via as_of)? Reports how often each model produces a USABLE (sample-reliable)
    prediction, and -- only where both did -- which was closer to the real outcome_r."""
    rows = _fetch_trusted_rows(anchor_strategy=anchor_strategy, canonical_symbol=None, regime=None, session=None, confidence_band=None, peer_group_hash=None)
    total_n = len(rows)
    if total_n < _MIN_TRAIN_HISTORY + _MIN_OOS_EVAL:
        return {"anchor_strategy": anchor_strategy, "status": "INSUFFICIENT_SAMPLE", "total_n": total_n}

    split_index = max(1, min(total_n - 1, int(total_n * train_fraction)))
    split_time = rows[split_index][0].entry_time
    train_rows = [(fp, o) for fp, o in rows if fp.entry_time <= split_time - purge]
    oos_rows = [(fp, o) for fp, o in rows if fp.entry_time > split_time + purge]
    if len(train_rows) < _MIN_TRAIN_HISTORY or len(oos_rows) < _MIN_OOS_EVAL:
        return {"anchor_strategy": anchor_strategy, "status": "INSUFFICIENT_SAMPLE", "total_n": total_n, "train_n": len(train_rows), "oos_n": len(oos_rows)}

    as_of = split_time - purge
    exact_usable = 0
    similarity_usable = 0
    both_usable_errors: list[tuple[float, float]] = []  # (exact_abs_error, similarity_abs_error)

    # Exact peer-group match, restricted to the train-only window -- a simple direct count
    # (reuses peer_group_hash, the SAME hard-match key statistics.py's exact model uses).
    train_by_peer_group: dict[str, list[Any]] = {}
    for fp, o in train_rows:
        train_by_peer_group.setdefault(fp.peer_group_hash, []).append(o)

    for fp, outcome in oos_rows:
        exact_group = train_by_peer_group.get(fp.peer_group_hash, [])
        exact_n = len(exact_group)
        exact_prediction = None
        if exact_n >= 100:
            exact_usable += 1
            r_values = [o.outcome_r for o in exact_group if o.outcome_r is not None]
            exact_prediction = sum(r_values) / len(r_values) if r_values else None

        sim_stats = similarity.similarity_statistics(
            canonical_symbol=fp.canonical_symbol, direction=fp.direction, anchor_strategy=anchor_strategy,
            strategy_version=fp.strategy_version, query_dims=_dims(fp), regime_broad=fp.regime_broad, top_k=top_k, as_of=as_of,
        )
        sim_prediction = None
        if sim_stats.get("status") == "OK" and sim_stats.get("effective_sample_size", 0) >= 100:
            similarity_usable += 1
            sim_prediction = sim_stats.get("weighted_expectancy_r")

        if exact_prediction is not None and sim_prediction is not None and outcome.outcome_r is not None:
            both_usable_errors.append((abs(exact_prediction - outcome.outcome_r), abs(sim_prediction - outcome.outcome_r)))

    result = {
        "anchor_strategy": anchor_strategy, "status": "OK", "oos_n": len(oos_rows),
        "exact_peer_group_usable_count": exact_usable, "similarity_weighted_usable_count": similarity_usable,
        "both_usable_count": len(both_usable_errors),
    }
    if both_usable_errors:
        exact_mae = round(sum(e for e, _ in both_usable_errors) / len(both_usable_errors), 4)
        sim_mae = round(sum(s for _, s in both_usable_errors) / len(both_usable_errors), 4)
        result.update({"exact_peer_group_mean_abs_error": exact_mae, "similarity_weighted_mean_abs_error": sim_mae, "similarity_model_better": sim_mae < exact_mae})
    else:
        result["comparison"] = "NEITHER_OR_ONLY_ONE_MODEL_REACHED_RELIABLE_SAMPLE_IN_TRAIN_ONLY_WINDOW"
    return result
