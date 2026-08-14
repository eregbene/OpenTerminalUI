"""Walk-forward / out-of-sample validation over REAL replayed MT5 strategy outcomes (Phase 1).

Reuses backend/research/validation.py's chronological-split / purge-embargo / degradation-
percentage DESIGN LANGUAGE (rolling train window, a purge gap, then a validation window) but
drives it over Historical Intelligence's own real fingerprint+outcome corpus
(historical_pattern_fingerprints / historical_setup_outcomes) instead of that module's
EventDrivenBacktester/DSL-strategy path -- per explicit instruction: validate the REAL MT5
strategy/replay system, not the old equities DSL backtester. No new backtesting engine is built
here; this module only adds chronological splitting, per-split statistics, and edge-stability
classification on top of evidence pattern_builder.py already replayed from real production
strategy logic.

No random leakage: splits are chronological by `entry_time` only, never shuffled. Purge/embargo:
a gap is removed at each split boundary equal to the LONGEST outcome-labeling lookforward window
that could still be resolving at the boundary instant (see _PURGE_WINDOW) -- without this, a
setup entered near the end of the train window could have its outcome determined by future
candles that fall inside the validation window, silently leaking validation-period information
into what's labeled "train".
"""
from __future__ import annotations

import hashlib
import statistics as pystats
from datetime import datetime, timezone, timedelta
from typing import Any

from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM, HistoricalWalkForwardResultORM
from backend.shared.db import SessionLocal

# A conservative purge/embargo gap -- generous relative to outcomes.py's typical resolution
# horizon (most setups resolve within hours; _MAX_LOOKFORWARD_BARS there caps at ~8.3 days) so a
# train-window setup's outcome can never leak from the validation window on the other side of
# the gap. Deliberately NOT the full 8.3-day cap (that would purge away most of a short real
# corpus); this is a pragmatic, documented trade-off, not a claim of zero possible leakage for
# the rare very-slow-resolving setup.
_PURGE_WINDOW = timedelta(hours=12)

EDGE_STRONG = "STRONG"
EDGE_ACCEPTABLE = "ACCEPTABLE"
EDGE_DEGRADED = "DEGRADED"
EDGE_FAILED_OOS = "FAILED_OOS"
EDGE_INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"

# Directional-edge taxonomy (Historical-Intelligence-Semantics-Audit directive): classify_edge_
# stability's own STRONG/ACCEPTABLE/DEGRADED/FAILED_OOS vocabulary is a RETENTION metric ("did a
# positive in-sample edge survive OOS") -- it has no way to say "no positive edge ever existed,
# but the strategy is RELIABLY, REPRODUCIBLY negative in both windows", which is real, useful,
# actionable intelligence (Part 3: do not collapse "reliably negative" and "unstable/no signal"
# into the same FAILED_OOS label). This is a SEPARATE, additive classification over the exact
# same train/oos expectancy pair -- classify_edge_stability's own contract and every existing
# caller of it are unchanged.
DIRECTIONAL_POSITIVE_EDGE = "POSITIVE_EDGE"
DIRECTIONAL_NEGATIVE_EDGE = "NEGATIVE_EDGE"
DIRECTIONAL_NEUTRAL_EDGE = "NEUTRAL_EDGE"
DIRECTIONAL_UNSTABLE = "UNSTABLE"
DIRECTIONAL_INSUFFICIENT = "INSUFFICIENT"
# Below this magnitude (in R), an expectancy is treated as "practically zero" for directional
# classification purposes -- neither a real edge to lean on nor a real pattern to avoid.
_NEUTRAL_EXPECTANCY_BAND_R = 0.05

_MIN_TRAIN_SAMPLE = 20
_MIN_OOS_SAMPLE = 10


def classify_directional_edge(*, train: dict[str, Any], oos: dict[str, Any], min_train: int = _MIN_TRAIN_SAMPLE, min_oos: int = _MIN_OOS_SAMPLE, neutral_band_r: float = _NEUTRAL_EXPECTANCY_BAND_R) -> str:
    """Sign-agreement classification, deliberately separate from classify_edge_stability's
    retention-fraction logic above. Both windows must independently clear the SAME sample-size
    floor as the retention classifier -- this is not a lower bar, only a different QUESTION asked
    of the identical evidence:
      - both windows clearly positive (> neutral_band_r)  -> POSITIVE_EDGE
      - both windows clearly negative (< -neutral_band_r) -> NEGATIVE_EDGE (a reproducible,
        actionable NEGATIVE pattern -- real intelligence, not "no information")
      - both windows inside the neutral band              -> NEUTRAL_EDGE
      - the windows disagree in sign / straddle the band   -> UNSTABLE (genuinely no reliable
        directional conclusion -- this is the ONLY case that should block candidate-level
        evidence outright, not "not a positive retained edge")
    """
    if train["n"] < min_train or oos["n"] < min_oos or train["expectancy_r"] is None or oos["expectancy_r"] is None:
        return DIRECTIONAL_INSUFFICIENT
    train_r = train["expectancy_r"]
    oos_r = oos["expectancy_r"]
    train_sign = 1 if train_r > neutral_band_r else (-1 if train_r < -neutral_band_r else 0)
    oos_sign = 1 if oos_r > neutral_band_r else (-1 if oos_r < -neutral_band_r else 0)
    if train_sign == 1 and oos_sign == 1:
        return DIRECTIONAL_POSITIVE_EDGE
    if train_sign == -1 and oos_sign == -1:
        return DIRECTIONAL_NEGATIVE_EDGE
    if train_sign == 0 and oos_sign == 0:
        return DIRECTIONAL_NEUTRAL_EDGE
    return DIRECTIONAL_UNSTABLE


def _stats_for_rows(outcomes: list[HistoricalSetupOutcomeORM]) -> dict[str, Any]:
    r_values = [o.outcome_r for o in outcomes if o.outcome_r is not None]
    n = len(r_values)
    if n == 0:
        return {"n": 0, "win_rate": None, "expectancy_r": None, "median_r": None, "profit_factor": None, "avg_mfe_r": None, "avg_mae_r": None, "immediate_failure_rate": None, "max_drawdown_r": None}
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r < 0]
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    mfe = [o.mfe_r for o in outcomes if o.mfe_r is not None]
    mae = [o.mae_r for o in outcomes if o.mae_r is not None]
    imm_fail = [o.immediate_failure for o in outcomes if o.immediate_failure is not None]

    # Sequential (entry-order) cumulative-R drawdown -- a simple, deterministic proxy for
    # max drawdown WITHIN this split's own trade sequence (not a full Monte Carlo path, see
    # Phase 2 for that); still real evidence, not simulated.
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in r_values:
        cumulative += r
        peak = max(peak, cumulative)
        max_dd = min(max_dd, cumulative - peak)

    return {
        "n": n,
        "win_rate": round(len(wins) / n, 4),
        "expectancy_r": round(pystats.fmean(r_values), 4),
        "median_r": round(pystats.median(r_values), 4),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "avg_mfe_r": round(pystats.fmean(mfe), 4) if mfe else None,
        "avg_mae_r": round(pystats.fmean(mae), 4) if mae else None,
        "immediate_failure_rate": round(sum(1 for x in imm_fail if x) / len(imm_fail), 4) if imm_fail else None,
        "max_drawdown_r": round(max_dd, 4),
    }


def classify_edge_stability(*, train: dict[str, Any], oos: dict[str, Any], min_train: int = _MIN_TRAIN_SAMPLE, min_oos: int = _MIN_OOS_SAMPLE) -> str:
    """Deterministic, transparent -- no ML, no opaque scoring. See module docstring."""
    if train["n"] < min_train or oos["n"] < min_oos or train["expectancy_r"] is None or oos["expectancy_r"] is None:
        return EDGE_INSUFFICIENT_SAMPLE
    if train["expectancy_r"] <= 0:
        # No real in-sample edge existed to begin with -- OOS improving on that is reported
        # honestly (ACCEPTABLE, not STRONG -- there is nothing proven to have "held up"),
        # OOS staying non-positive is simply confirmed, not a "failure" of something that
        # never existed.
        return EDGE_ACCEPTABLE if oos["expectancy_r"] > 0 else EDGE_FAILED_OOS
    if oos["expectancy_r"] <= 0:
        return EDGE_FAILED_OOS
    retained_fraction = oos["expectancy_r"] / train["expectancy_r"]
    if retained_fraction >= 0.7:
        return EDGE_STRONG
    if retained_fraction >= 0.3:
        return EDGE_ACCEPTABLE
    return EDGE_DEGRADED


def _fetch_trusted_rows(*, anchor_strategy: str | None, canonical_symbol: str | None, regime: str | None, session: str | None, confidence_band: str | None, peer_group_hash: str | None) -> list[tuple[HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM]]:
    with SessionLocal() as db:
        query = (
            db.query(HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM)
            .join(HistoricalSetupOutcomeORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id)
            .filter(HistoricalSetupOutcomeORM.resolution_status == "RESOLVED", HistoricalSetupOutcomeORM.data_quality != "UNTRUSTED")
        )
        if anchor_strategy:
            query = query.filter(HistoricalPatternFingerprintORM.anchor_strategy == anchor_strategy)
        if canonical_symbol:
            query = query.filter(HistoricalPatternFingerprintORM.canonical_symbol == canonical_symbol.upper())
        if regime:
            query = query.filter(HistoricalPatternFingerprintORM.regime == regime)
        if session:
            query = query.filter(HistoricalPatternFingerprintORM.session == session)
        if confidence_band:
            query = query.filter(HistoricalPatternFingerprintORM.confidence_band == confidence_band)
        if peer_group_hash:
            query = query.filter(HistoricalPatternFingerprintORM.peer_group_hash == peer_group_hash)
        rows = query.order_by(HistoricalPatternFingerprintORM.entry_time.asc()).all()
    return rows


def run_walk_forward(
    *,
    anchor_strategy: str | None = None, canonical_symbol: str | None = None, regime: str | None = None,
    session: str | None = None, confidence_band: str | None = None, peer_group_hash: str | None = None,
    train_fraction: float = 0.6, purge: timedelta = _PURGE_WINDOW,
) -> dict[str, Any]:
    """TRAIN -> (purge) -> OOS, chronological, no leakage. At least one filter should normally be
    supplied (unfiltered = every trusted setup across every strategy, rarely a meaningful single
    edge to validate) -- left unfiltered as a valid, explicit choice for callers who want an
    aggregate system-wide check."""
    rows = _fetch_trusted_rows(anchor_strategy=anchor_strategy, canonical_symbol=canonical_symbol, regime=regime, session=session, confidence_band=confidence_band, peer_group_hash=peer_group_hash)
    total_n = len(rows)
    if total_n == 0:
        return {"edge_stability": EDGE_INSUFFICIENT_SAMPLE, "directional_edge": DIRECTIONAL_INSUFFICIENT, "total_n": 0, "train": _stats_for_rows([]), "oos": _stats_for_rows([]), "filters": _filters_dict(anchor_strategy, canonical_symbol, regime, session, confidence_band, peer_group_hash)}

    split_index = int(total_n * train_fraction)
    split_index = max(1, min(total_n - 1, split_index))
    split_time = rows[split_index][0].entry_time

    train_rows = [o for fp, o in rows if fp.entry_time <= split_time - purge]
    oos_rows = [o for fp, o in rows if fp.entry_time > split_time + purge]
    purged_count = total_n - len(train_rows) - len(oos_rows)

    train_stats = _stats_for_rows(train_rows)
    oos_stats = _stats_for_rows(oos_rows)
    edge_stability = classify_edge_stability(train=train_stats, oos=oos_stats)
    directional_edge = classify_directional_edge(train=train_stats, oos=oos_stats)

    degradation_pct = None
    if train_stats["expectancy_r"] not in (None, 0) and oos_stats["expectancy_r"] is not None:
        degradation_pct = round(100.0 * (1.0 - oos_stats["expectancy_r"] / train_stats["expectancy_r"]), 1)

    result = {
        "edge_stability": edge_stability,
        "directional_edge": directional_edge,
        "total_n": total_n,
        "purged_n": purged_count,
        "split_time": split_time.isoformat(),
        "train": train_stats,
        "oos": oos_stats,
        "degradation_pct": degradation_pct,
        "filters": _filters_dict(anchor_strategy, canonical_symbol, regime, session, confidence_band, peer_group_hash),
    }
    _persist(anchor_strategy=anchor_strategy, canonical_symbol=canonical_symbol, regime=regime, session=session, confidence_band=confidence_band, peer_group_hash=peer_group_hash, result=result)
    return result


def _result_id(anchor_strategy, canonical_symbol, regime, session, confidence_band, peer_group_hash) -> str:
    key = f"{anchor_strategy}:{canonical_symbol}:{regime}:{session}:{confidence_band}:{peer_group_hash}"
    return "HWFR_" + hashlib.sha256(key.encode()).hexdigest()[:40]


def _persist(*, anchor_strategy, canonical_symbol, regime, session, confidence_band, peer_group_hash, result: dict[str, Any]) -> None:
    result_id = _result_id(anchor_strategy, canonical_symbol, regime, session, confidence_band, peer_group_hash)
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        row = db.get(HistoricalWalkForwardResultORM, result_id)
        if row is None:
            row = HistoricalWalkForwardResultORM(result_id=result_id, anchor_strategy=anchor_strategy, canonical_symbol=canonical_symbol, regime=regime, session=session, confidence_band=confidence_band, peer_group_hash=peer_group_hash)
            db.add(row)
        row.edge_stability = result["edge_stability"]
        row.directional_edge = result.get("directional_edge")
        row.total_n = result["total_n"]
        row.train_n = result["train"]["n"]
        row.oos_n = result["oos"]["n"]
        row.train_expectancy_r = result["train"]["expectancy_r"]
        row.oos_expectancy_r = result["oos"]["expectancy_r"]
        row.degradation_pct = result.get("degradation_pct")
        row.train_stats = result["train"]
        row.oos_stats = result["oos"]
        row.computed_at = now
        db.commit()


def latest_edge_stability(*, anchor_strategy: str) -> dict[str, Any]:
    """Read-only lookup for entry_intelligence.py's third gate -- does NOT recompute; call
    run_walk_forward_by_strategy() on whatever cadence makes sense (e.g. after each bulk
    fingerprint regeneration) and this reads the latest persisted result."""
    with SessionLocal() as db:
        row = (
            db.query(HistoricalWalkForwardResultORM)
            .filter(HistoricalWalkForwardResultORM.anchor_strategy == anchor_strategy, HistoricalWalkForwardResultORM.canonical_symbol.is_(None))
            .order_by(HistoricalWalkForwardResultORM.computed_at.desc())
            .first()
        )
    if row is None:
        return {"edge_stability": EDGE_INSUFFICIENT_SAMPLE, "directional_edge": DIRECTIONAL_INSUFFICIENT, "reason": "no walk-forward run recorded for this strategy yet"}
    return {"edge_stability": row.edge_stability, "directional_edge": row.directional_edge or DIRECTIONAL_INSUFFICIENT, "train_n": row.train_n, "oos_n": row.oos_n, "train_expectancy_r": row.train_expectancy_r, "oos_expectancy_r": row.oos_expectancy_r, "degradation_pct": row.degradation_pct, "computed_at": row.computed_at.isoformat()}


def latest_edge_stability_for_symbol(*, anchor_strategy: str, canonical_symbol: str) -> dict[str, Any]:
    """Read-only, combination-level counterpart to latest_edge_stability -- reads the latest
    persisted (anchor_strategy, canonical_symbol) result with no other filter set (regime/
    session/confidence_band all NULL), i.e. the row segment_matrix.strategy_symbol_matrix()
    persists via run_walk_forward(anchor_strategy=..., canonical_symbol=...). Does NOT recompute
    -- if no combination-level run has ever persisted a result for this pair, returns
    INSUFFICIENT_SAMPLE rather than blocking on absence (the strategy-level gate remains the
    primary, always-on safety net; this is a strictly ADDITIONAL, more granular check)."""
    with SessionLocal() as db:
        row = (
            db.query(HistoricalWalkForwardResultORM)
            .filter(
                HistoricalWalkForwardResultORM.anchor_strategy == anchor_strategy,
                HistoricalWalkForwardResultORM.canonical_symbol == canonical_symbol.upper(),
                HistoricalWalkForwardResultORM.regime.is_(None),
                HistoricalWalkForwardResultORM.session.is_(None),
                HistoricalWalkForwardResultORM.confidence_band.is_(None),
            )
            .order_by(HistoricalWalkForwardResultORM.computed_at.desc())
            .first()
        )
    if row is None:
        return {"edge_stability": EDGE_INSUFFICIENT_SAMPLE, "directional_edge": DIRECTIONAL_INSUFFICIENT, "reason": "no combination-level walk-forward run recorded for this strategy+symbol pair yet"}
    return {"edge_stability": row.edge_stability, "directional_edge": row.directional_edge or DIRECTIONAL_INSUFFICIENT, "train_n": row.train_n, "oos_n": row.oos_n, "train_expectancy_r": row.train_expectancy_r, "oos_expectancy_r": row.oos_expectancy_r, "degradation_pct": row.degradation_pct, "computed_at": row.computed_at.isoformat()}


def _filters_dict(anchor_strategy, canonical_symbol, regime, session, confidence_band, peer_group_hash) -> dict[str, Any]:
    return {"anchor_strategy": anchor_strategy, "canonical_symbol": canonical_symbol, "regime": regime, "session": session, "confidence_band": confidence_band, "peer_group_hash": peer_group_hash}


def run_walk_forward_by_strategy() -> dict[str, dict[str, Any]]:
    """Convenience driver: one walk-forward run per anchor_strategy currently represented in the
    trusted corpus."""
    with SessionLocal() as db:
        strategies = [r[0] for r in db.query(HistoricalPatternFingerprintORM.anchor_strategy).distinct().all()]
    return {strategy: run_walk_forward(anchor_strategy=strategy) for strategy in strategies}
