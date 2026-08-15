"""Entry-engine DEMO_ACTIVE integration (Part 12/13/14).

Historical Intelligence may only INFLUENCE (never override) candidate quality/ranking/defer-
reject/strategy-confirmation, and only when BOTH gates pass:
  A. strategy replay trust (trust_gating.get_trust_state == HIST_INTEL_ACTIVE)
  B. pattern statistical reliability (statistics.reliability_label in {USEFUL, STRONG})
Both are re-checked on every call -- neither is cached longer than its own TTL, and neither is
ever bypassed for a "special" strategy (Part 14: mtfai1 gets no permanent exemption).

NO SYNCHRONOUS REPLAY (Part 11): this module never calls replay.py. It only (a) builds a
fingerprint from an ALREADY-BUILT StrategyContext (pure, cheap, no I/O) and (b) looks up
pre-computed statistics via cache.cached_pattern_statistics (Redis fast path, Postgres
aggregate-query fallback -- never a live backtest). Total added latency per candidate is
bounded by one Redis round-trip (or one indexed Postgres aggregate on a cache miss).

Historical evidence score (Part 12 -- "do not use win_rate < X => reject"): a transparent,
multi-factor, bounded (-50..+50) blend of expectancy, profit factor, immediate-failure
probability, and continuation probability, scaled by sample reliability. Every component is
persisted in the returned dict (Part 21 observability) so "why did this score come out this
way" is always answerable without re-deriving it.

Fallback (Part 13): ANY of {trust gate fails, reliability gate fails, Redis/Postgres error,
fingerprint cannot be built, historical_intelligence mode is not DEMO_ACTIVE} results in
status=UNAVAILABLE with an explicit reason and ranking_adjustment=0 / no defer-reject
recommendation -- current engine logic is completely untouched. This module never raises; a
caller-side failure here can never abort a trading cycle.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from backend.historical_intelligence import cache, fingerprint as fingerprint_mod, similarity, statistics, trust_gating, walk_forward
from backend.historical_intelligence.modes import demo_active_enabled
from backend.historical_intelligence.orm import HistoricalIntelligenceObservationORM
from backend.historical_intelligence.replay import STRATEGY_REPLAY_VERSION
from backend.mt5_strategies.context import StrategyContext
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

# Minimum sample size required for a pattern to actually influence a live decision, ON TOP OF
# statistics.reliability_label's own USEFUL/STRONG bar -- belt and suspenders: reliability_label
# already enforces >=100 for USEFUL, this constant exists purely so the floor is visible and
# tunable in ONE place without having to also touch statistics.py's general-purpose thresholds.
_MIN_SAMPLE_FOR_LIVE_INFLUENCE = 100
_RELIABLE_LEVELS = statistics.RELIABLE_TIER_LEVELS
# Phase 1 (walk-forward/OOS) gate: only a strategy whose historical edge held up out-of-sample
# may influence a live decision. FAILED_OOS and DEGRADED are excluded outright; INSUFFICIENT_
# SAMPLE is ALSO excluded (never assume stability that hasn't been demonstrated).
_EDGE_STABLE_LEVELS = {"STRONG", "ACCEPTABLE"}
# Historical-Intelligence-Semantics-Audit directive: the retention-fraction gate above
# (_EDGE_STABLE_LEVELS) answers "did a positive edge survive OOS" -- it has no way to let a
# STRATEGY that is reliably, reproducibly NEGATIVE both in-sample and out-of-sample (real,
# actionable intelligence -- see walk_forward.classify_directional_edge) reach candidate-level
# evaluation at all, even though such a strategy could legitimately REJECT/DEFER a matching
# candidate. Only genuinely unreliable/contradictory strategy-level evidence should block
# candidate-level evaluation outright.
_DIRECTIONAL_GATE_BLOCKED = {walk_forward.DIRECTIONAL_UNSTABLE, walk_forward.DIRECTIONAL_INSUFFICIENT}
# Configurable top-K for the multi-neighbor similarity search (section 1 of the analog-
# intelligence directive) -- kept modest for the live hot path; a larger K can be requested
# explicitly via similarity.similarity_statistics/find_similar_setups for offline analysis.
_SIMILARITY_TOP_K = 50

# Bounded, deliberately conservative for this system's FIRST live rollout -- a much wider score
# range (-50..+50) is computed and persisted for observability/analysis, but the amount actually
# allowed to move a live ranking_score is capped much tighter.
_MAX_LIVE_RANKING_ADJUSTMENT = 10.0
# Reject recommendation requires the STRONGEST reliability tier AND a severely negative score --
# a conjunction of several negative signals, never a single win-rate cutoff.
_REJECT_SCORE_THRESHOLD = -35.0
_REJECT_MIN_RELIABILITY = "STRONG"
_REJECT_MIN_IMMEDIATE_FAILURE_RATE = 0.6


_INSUFFICIENT_REASONS = {"PATTERN_SAMPLE_INSUFFICIENT", "NO_GOOD_HISTORICAL_ANALOG", "HISTORICAL_INTELLIGENCE_MODE_NOT_DEMO_ACTIVE"}

# Explicit historical_decision vocabulary (multi-neighbor analog intelligence directive):
# SUPPORT/RANK_ADJUST/DEFER/REJECT for an EVALUATED result, HIST_INTEL_INSUFFICIENT for "not
# enough evidence either way", HIST_INTEL_NEUTRAL for every other UNAVAILABLE reason (trust/OOS/
# mode gate failed). A thin, deterministic labeling layer over the ALREADY-computed
# historical_score/ranking_adjustment/defer_reject_reason -- no new scoring invented.
HIST_INTEL_SUPPORT = "SUPPORT"
HIST_INTEL_RANK_ADJUST = "RANK_ADJUST"
HIST_INTEL_DEFER = "DEFER"
HIST_INTEL_REJECT = "REJECT"
HIST_INTEL_NEUTRAL = "HIST_INTEL_NEUTRAL"
HIST_INTEL_INSUFFICIENT = "HIST_INTEL_INSUFFICIENT"
_SUPPORT_SCORE_THRESHOLD = 15.0
_DEFER_SCORE_THRESHOLD = -15.0


def _historical_decision(*, status: str, reason: str | None, historical_score: float, defer_reject_reason: str | None) -> str:
    if status == "UNAVAILABLE":
        return HIST_INTEL_INSUFFICIENT if reason in _INSUFFICIENT_REASONS else HIST_INTEL_NEUTRAL
    if defer_reject_reason:
        return HIST_INTEL_REJECT
    if historical_score >= _SUPPORT_SCORE_THRESHOLD:
        return HIST_INTEL_SUPPORT
    if historical_score <= _DEFER_SCORE_THRESHOLD:
        return HIST_INTEL_DEFER
    return HIST_INTEL_RANK_ADJUST


def _unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    result = {
        "status": "UNAVAILABLE", "reason": reason, "trust_state": None, "reliability": None,
        "sample_size": 0, "historical_score": 0.0, "ranking_adjustment": 0.0,
        "defer_reject_reason": None, "peer_group_hash": None, "source": None, "evaluation_source": None, **extra,
    }
    result["historical_decision"] = _historical_decision(status="UNAVAILABLE", reason=reason, historical_score=0.0, defer_reject_reason=None)
    return result


def _historical_score(stats: dict[str, Any]) -> float:
    expectancy = stats.get("expectancy_r")
    pf = stats.get("profit_factor")
    immediate_failure_rate = stats.get("immediate_failure_rate")
    p1r = stats.get("probability_1r")

    expectancy_component = max(-1.0, min(1.0, (expectancy or 0.0) / 2.0)) * 40.0
    pf_component = max(-20.0, min(20.0, ((pf - 1.0) * 20.0))) if pf is not None else 0.0
    failure_component = -(immediate_failure_rate or 0.0) * 20.0
    continuation_component = (p1r or 0.0) * 20.0

    reliability_weight = {"USEFUL": 0.7, "STRONG": 1.0}.get(stats.get("reliability"), 0.0)
    raw = (expectancy_component + pf_component + failure_component + continuation_component) * reliability_weight
    return round(max(-50.0, min(50.0, raw)), 2)


def _normalize_similarity_stats(similarity_stats: dict[str, Any]) -> dict[str, Any]:
    """Adapts similarity.similarity_statistics's weighted_* field names onto the same shape
    _evaluate_from_stats already understands (statistics.pattern_statistics's field names) --
    one scoring/gating implementation, never two parallel copies. `sample_size` here is the
    EFFECTIVE (similarity-weighted) sample size, never a raw neighbor count -- see similarity.py's
    module docstring for why that is not a bar-lowering shortcut."""
    return {
        "reliability": similarity_stats.get("reliability"),
        "sample_size": similarity_stats.get("effective_sample_size") or 0,
        "raw_neighbor_count": similarity_stats.get("raw_neighbor_count"),
        "very_close_matches": similarity_stats.get("very_close_matches"),
        "median_similarity": similarity_stats.get("median_similarity"),
        "top_match_quality": similarity_stats.get("top_match_quality"),
        "similarity_weight_distribution": similarity_stats.get("similarity_weight_distribution"),
        "expectancy_r": similarity_stats.get("weighted_expectancy_r"),
        "net_expectancy_r": similarity_stats.get("weighted_net_expectancy_r"),
        "profit_factor": similarity_stats.get("weighted_profit_factor"),
        "immediate_failure_rate": similarity_stats.get("weighted_immediate_failure_probability"),
        "probability_0_25r": similarity_stats.get("weighted_probability_0_25r"),
        "probability_0_5r": similarity_stats.get("weighted_probability_0_5r"),
        "probability_0_75r": similarity_stats.get("weighted_probability_0_75r"),
        "probability_1r": similarity_stats.get("weighted_probability_1r"),
        "probability_1_5r": similarity_stats.get("weighted_probability_1_5r"),
        "probability_2r": similarity_stats.get("weighted_probability_2r"),
        "probability_tp": similarity_stats.get("weighted_probability_tp"),
        "probability_sl": similarity_stats.get("weighted_probability_sl"),
    }


def _pick_evaluation_source(exact_stats: dict[str, Any], similarity_stats: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """Prefers the exact peer-group match when it independently qualifies (a purer signal, no
    similarity-weighting uncertainty); falls back to the similarity-weighted view ONLY when the
    exact match does not qualify but the similarity evidence independently clears the SAME bar
    (Workstream 9 -- effective sample size is never a way to lower the 100-sample requirement,
    only an additional, equally-conservative lens). The two are never blended into one number;
    whichever source is actually used is always reported (`evaluation_source`)."""
    exact_reliable = exact_stats.get("reliability") in _RELIABLE_LEVELS and (exact_stats.get("sample_size") or 0) >= _MIN_SAMPLE_FOR_LIVE_INFLUENCE
    if exact_reliable or similarity_stats is None:
        return "EXACT_PEER_GROUP", exact_stats
    normalized = _normalize_similarity_stats(similarity_stats)
    similarity_reliable = normalized["reliability"] in _RELIABLE_LEVELS and normalized["sample_size"] >= _MIN_SAMPLE_FOR_LIVE_INFLUENCE
    if similarity_reliable:
        return "SIMILARITY_WEIGHTED", normalized
    return "EXACT_PEER_GROUP", exact_stats


def _evaluate_from_stats(*, trust_state: str, stats: dict[str, Any], peer_group_hash: str, source: str | None, evaluation_source: str = "EXACT_PEER_GROUP") -> dict[str, Any]:
    """Pure, synchronous core shared by both entry points below -- everything after "we already
    have a trust_state and a statistics dict" is identical whether those came from the async
    Redis-backed path (evaluate_historical_intelligence, for a live decision) or the sync
    Postgres-direct path (record_observations, for post-decision observability capture)."""
    reliability = stats.get("reliability")
    sample_size = stats.get("sample_size") or 0

    result = {
        "status": "EVALUATED", "reason": None, "trust_state": trust_state,
        "reliability": reliability, "sample_size": sample_size,
        "expectancy_r": stats.get("expectancy_r"), "profit_factor": stats.get("profit_factor"),
        "immediate_failure_probability": stats.get("immediate_failure_rate"),
        "probability_0_5r": stats.get("probability_0_5r"), "probability_1r": stats.get("probability_1r"),
        "probability_1_5r": stats.get("probability_1_5r"), "probability_2r": stats.get("probability_2r"),
        "peer_group_hash": peer_group_hash, "source": source, "evaluation_source": evaluation_source,
    }

    if reliability not in _RELIABLE_LEVELS or sample_size < _MIN_SAMPLE_FOR_LIVE_INFLUENCE:
        result.update({"status": "UNAVAILABLE", "reason": "PATTERN_SAMPLE_INSUFFICIENT", "historical_score": 0.0, "ranking_adjustment": 0.0, "defer_reject_reason": None})
        result["historical_decision"] = _historical_decision(status="UNAVAILABLE", reason="PATTERN_SAMPLE_INSUFFICIENT", historical_score=0.0, defer_reject_reason=None)
        return result

    historical_score = _historical_score(stats)
    ranking_adjustment = max(-_MAX_LIVE_RANKING_ADJUSTMENT, min(_MAX_LIVE_RANKING_ADJUSTMENT, historical_score / 5.0))

    defer_reject_reason = None
    if (
        historical_score <= _REJECT_SCORE_THRESHOLD
        and reliability == _REJECT_MIN_RELIABILITY
        and (stats.get("immediate_failure_rate") or 0.0) >= _REJECT_MIN_IMMEDIATE_FAILURE_RATE
    ):
        defer_reject_reason = "HISTORICAL_EVIDENCE_STRONGLY_NEGATIVE"

    result.update({"historical_score": historical_score, "ranking_adjustment": round(ranking_adjustment, 2), "defer_reject_reason": defer_reject_reason})
    result["historical_decision"] = _historical_decision(status="EVALUATED", reason=None, historical_score=historical_score, defer_reject_reason=defer_reject_reason)
    return result


async def evaluate_historical_intelligence(
    *,
    ctx: StrategyContext,
    strategy_id: str,
    contributing_strategies: list[str],
    strategy_family: str | None,
    entry: float,
    stop_loss: float,
    take_profit: float,
    entry_time: datetime,
    confidence_band: str | None = None,
) -> dict[str, Any]:
    """Called from the live screening path (observation-safe, try/except-wrapped by the
    caller). Always returns a fully-populated observability dict; only ranking_adjustment/
    defer_reject_reason are non-zero/non-None when both trust and reliability gates pass AND
    HISTORICAL_INTELLIGENCE_MODE=DEMO_ACTIVE. Uses the Redis-backed cache (cache.
    cached_pattern_statistics) -- appropriate here because this path can run once per candidate
    per M5 cycle, repeatedly hitting the same handful of peer groups."""
    try:
        if not demo_active_enabled():
            return _unavailable("HISTORICAL_INTELLIGENCE_MODE_NOT_DEMO_ACTIVE")

        # Redis-fronted (Historical-Intelligence-Semantics-Audit directive, real measured fix):
        # these three gate reads are individually cheap PK/indexed-limit-1 queries, but a fresh
        # SessionLocal() per call measured ~300ms under real concurrent Postgres load (a
        # background corpus worker writing continuously) -- and this now runs once per top-K
        # candidate on every live ranking cycle. Cached, short TTL (results only change when
        # trust/walk-forward is recomputed, not per-cycle).
        trust = await cache.cached_trust_state(strategy_id)
        if trust["trust_state"] != trust_gating.HIST_INTEL_ACTIVE:
            return _unavailable("STRATEGY_REPLAY_UNTRUSTED", trust_state=trust["trust_state"])

        # Third gate (Phase 1 -- walk-forward/OOS), directional-edge form (Historical-
        # Intelligence-Semantics-Audit directive): a strategy whose OOS evidence is genuinely
        # UNSTABLE (train/OOS disagree in sign) or INSUFFICIENT must never influence a live
        # decision -- but a strategy that is reliably, reproducibly NEGATIVE (both windows agree)
        # is real, actionable intelligence and MAY reach candidate-level evaluation below, where
        # it can only ever produce RANK_ADJUST/DEFER/REJECT, never a fabricated SUPPORT (see
        # _historical_score -- it is driven by the candidate's OWN peer-group/similarity stats,
        # not by this strategy-level label).
        edge = await cache.cached_edge_stability(strategy_id)
        if edge["directional_edge"] in _DIRECTIONAL_GATE_BLOCKED:
            return _unavailable("STRATEGY_EDGE_UNSTABLE_OR_INSUFFICIENT", trust_state=trust["trust_state"], edge_stability=edge["edge_stability"], directional_edge=edge["directional_edge"])

        fields = fingerprint_mod.build_fingerprint(
            ctx=ctx, strategy_id=strategy_id, contributing_strategies=contributing_strategies, strategy_family=strategy_family,
            strategy_version=STRATEGY_REPLAY_VERSION, source_quality_tier="LIVE", provider="MT5", proxy=False,
            entry=entry, stop_loss=stop_loss, take_profit=take_profit, entry_time=entry_time, confidence_band=confidence_band,
        )

        # Combination-level gate (edge-quality investigation, Phase D), same directional-edge
        # treatment: a genuinely UNSTABLE/INSUFFICIENT combination-level result still blocks
        # candidate-level evaluation for that exact (strategy, symbol) pair; a reliably NEGATIVE
        # combination-level result (e.g. mtfai1+EURUSD+SHORT+REVERSAL) is allowed through so it
        # can inform a REJECT/DEFER decision downstream instead of being silently discarded.
        combo_edge = await cache.cached_combination_edge_stability(strategy_id, fields["canonical_symbol"])
        if combo_edge["directional_edge"] in _DIRECTIONAL_GATE_BLOCKED and combo_edge["directional_edge"] != walk_forward.DIRECTIONAL_INSUFFICIENT:
            return _unavailable("COMBINATION_EDGE_UNSTABLE", trust_state=trust["trust_state"], edge_stability=edge["edge_stability"], directional_edge=edge["directional_edge"], combination_directional_edge=combo_edge["directional_edge"])

        peer_group_hash = fields["peer_group_hash"]
        exact_stats = await cache.cached_pattern_statistics(peer_group_hash, strategy_version=STRATEGY_REPLAY_VERSION)
        exact_reliable = exact_stats.get("reliability") in _RELIABLE_LEVELS and (exact_stats.get("sample_size") or 0) >= _MIN_SAMPLE_FOR_LIVE_INFLUENCE

        similarity_stats = None
        if not exact_reliable:
            # Redis-fronted (cache.cached_similarity_statistics) -- same fail-open Redis-then-
            # Postgres pattern as the exact peer-group lookup above, never a per-candidate
            # uncached DB scan on the hot path when the cache is warm.
            similarity_stats = await cache.cached_similarity_statistics(
                canonical_symbol=fields["canonical_symbol"], direction=fields["direction"],
                anchor_strategy=strategy_id, strategy_version=STRATEGY_REPLAY_VERSION, query_dims=_query_dims(fields),
                regime_broad=fields.get("regime_broad"), top_k=_SIMILARITY_TOP_K,
            )
        evaluation_source, chosen_stats = _pick_evaluation_source(exact_stats, similarity_stats)
        return _evaluate_from_stats(
            trust_state=trust["trust_state"], stats=chosen_stats, peer_group_hash=peer_group_hash,
            source=exact_stats.get("_cache_source"), evaluation_source=evaluation_source,
        )
    except Exception as exc:
        logger.warning("Historical intelligence entry evaluation failed for strategy=%s: %s", strategy_id, exc.__class__.__name__)
        return _unavailable(f"HISTORICAL_INTELLIGENCE_UNAVAILABLE:{exc.__class__.__name__}")


def evaluate_historical_intelligence_sync(
    *,
    ctx: StrategyContext,
    strategy_id: str,
    contributing_strategies: list[str],
    strategy_family: str | None,
    entry: float,
    stop_loss: float,
    take_profit: float,
    entry_time: datetime,
    confidence_band: str | None = None,
) -> dict[str, Any]:
    """Synchronous twin of evaluate_historical_intelligence, for callers in a non-async context
    (backend/brokers/mt5/autonomous.py::_record_cycle is sync). Reads statistics DIRECTLY from
    Postgres (statistics.pattern_statistics), deliberately bypassing the Redis cache -- this is
    a single post-decision observability write per candidate per cycle, not a repeated hot-path
    lookup, so the cache's benefit doesn't apply and skipping it avoids needing an async Redis
    call from sync code. Never mutates a live decision; the caller (record_observations below)
    only ever persists this for observability."""
    try:
        if not demo_active_enabled():
            return _unavailable("HISTORICAL_INTELLIGENCE_MODE_NOT_DEMO_ACTIVE")

        trust = trust_gating.get_trust_state(strategy_id)
        if trust["trust_state"] != trust_gating.HIST_INTEL_ACTIVE:
            return _unavailable("STRATEGY_REPLAY_UNTRUSTED", trust_state=trust["trust_state"])

        edge = walk_forward.latest_edge_stability(anchor_strategy=strategy_id)
        if edge["directional_edge"] in _DIRECTIONAL_GATE_BLOCKED:
            return _unavailable("STRATEGY_EDGE_UNSTABLE_OR_INSUFFICIENT", trust_state=trust["trust_state"], edge_stability=edge["edge_stability"], directional_edge=edge["directional_edge"])

        fields = fingerprint_mod.build_fingerprint(
            ctx=ctx, strategy_id=strategy_id, contributing_strategies=contributing_strategies, strategy_family=strategy_family,
            strategy_version=STRATEGY_REPLAY_VERSION, source_quality_tier="LIVE", provider="MT5", proxy=False,
            entry=entry, stop_loss=stop_loss, take_profit=take_profit, entry_time=entry_time, confidence_band=confidence_band,
        )

        # Combination-level gate -- see the async twin's identical comment above.
        combo_edge = walk_forward.latest_edge_stability_for_symbol(anchor_strategy=strategy_id, canonical_symbol=fields["canonical_symbol"])
        if combo_edge["directional_edge"] in _DIRECTIONAL_GATE_BLOCKED and combo_edge["directional_edge"] != walk_forward.DIRECTIONAL_INSUFFICIENT:
            return _unavailable("COMBINATION_EDGE_UNSTABLE", trust_state=trust["trust_state"], edge_stability=edge["edge_stability"], directional_edge=edge["directional_edge"], combination_directional_edge=combo_edge["directional_edge"])

        peer_group_hash = fields["peer_group_hash"]
        exact_stats = statistics.pattern_statistics(peer_group_hash, strategy_version=STRATEGY_REPLAY_VERSION)
        exact_reliable = exact_stats.get("reliability") in _RELIABLE_LEVELS and (exact_stats.get("sample_size") or 0) >= _MIN_SAMPLE_FOR_LIVE_INFLUENCE

        similarity_stats = None
        if not exact_reliable:
            similarity_stats = similarity.similarity_statistics(
                canonical_symbol=fields["canonical_symbol"], direction=fields["direction"],
                anchor_strategy=strategy_id, strategy_version=STRATEGY_REPLAY_VERSION, query_dims=_query_dims(fields),
                regime_broad=fields.get("regime_broad"), top_k=_SIMILARITY_TOP_K,
            )
        evaluation_source, chosen_stats = _pick_evaluation_source(exact_stats, similarity_stats)
        return _evaluate_from_stats(
            trust_state=trust["trust_state"], stats=chosen_stats, peer_group_hash=peer_group_hash,
            source="postgres", evaluation_source=evaluation_source,
        )
    except Exception as exc:
        logger.warning("Historical intelligence sync entry evaluation failed for strategy=%s: %s", strategy_id, exc.__class__.__name__)
        return _unavailable(f"HISTORICAL_INTELLIGENCE_UNAVAILABLE:{exc.__class__.__name__}")


def _query_dims(fields: dict[str, Any]) -> dict[str, Any]:
    """Extracts similarity.py's dimension subset from a full fingerprint field dict."""
    return {k: fields.get(k) for k in similarity.DIMENSION_WEIGHTS}


def _observation_id(evaluation_id: str) -> str:
    return "HIO_" + hashlib.sha256(evaluation_id.encode()).hexdigest()[:40]


def record_observations(service: Any, result: dict[str, Any]) -> int:
    """Called from backend/brokers/mt5/autonomous.py::_record_cycle, in the SAME best-effort,
    try/except-isolated, AFTER-the-decision-is-already-made style as
    capture_cycle_candidate_evaluations/capture_decision_snapshots that it sits alongside --
    STRICTLY OBSERVATION ONLY (Part 21). Uses evaluate_historical_intelligence_sync so no async
    bridging is needed from this sync method. `ranking_adjustment_applied` is always False here
    -- this hook runs after the cycle's decision is already finalized, so nothing it computes
    could have been applied even when the gates pass; a future pass that moves this evaluation
    earlier (into the actual ranking path) would set this True when it genuinely altered
    ranking_score. Idempotent per evaluation_id (matches the other two capture functions'
    scheme) -- never overwrites an existing observation."""
    cycle_id = result.get("cycle_id")
    if not cycle_id:
        return 0
    context_cache = getattr(service, "_cycle_context_cache", None) or {}
    if not context_cache:
        return 0
    candidates = [c for c in (result.get("candidates") or []) if isinstance(c, dict) and "trade_confidence" in c]
    if not candidates:
        return 0

    written = 0
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        for candidate in candidates:
            candidate_id = candidate.get("candidate_id")
            broker_symbol = str(candidate.get("broker_symbol") or "").upper()
            ctx = context_cache.get(broker_symbol)
            if not candidate_id or ctx is None:
                continue
            evaluation_id = hashlib.sha256(json.dumps({"candidate_id": candidate_id}, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:32]
            observation_id = _observation_id(evaluation_id)
            if db.get(HistoricalIntelligenceObservationORM, observation_id) is not None:
                continue

            context = candidate.get("context") or {}
            strategy_id = candidate.get("strategy") or context.get("strategy_id") or context.get("strategy") or ""
            if not strategy_id:
                continue
            entry = candidate.get("entry") or context.get("entry")
            stop_loss = candidate.get("stop_loss")
            take_profit = candidate.get("take_profit")
            if entry is None or stop_loss is None or take_profit is None:
                continue

            try:
                evaluation = evaluate_historical_intelligence_sync(
                    ctx=ctx, strategy_id=str(strategy_id), contributing_strategies=list(context.get("contributing_strategies") or [strategy_id]),
                    strategy_family=context.get("strategy_family"), entry=float(entry), stop_loss=float(stop_loss), take_profit=float(take_profit),
                    entry_time=now, confidence_band=(candidate.get("trade_confidence") or {}).get("band"),
                )
                row = HistoricalIntelligenceObservationORM(
                    observation_id=observation_id, evaluation_id=evaluation_id, cycle_id=str(cycle_id), strategy_id=str(strategy_id),
                    status=evaluation["status"], reason=evaluation.get("reason"), trust_state=evaluation.get("trust_state"),
                    reliability=evaluation.get("reliability"), sample_size=evaluation.get("sample_size") or 0,
                    expectancy_r=evaluation.get("expectancy_r"), profit_factor=evaluation.get("profit_factor"),
                    immediate_failure_probability=evaluation.get("immediate_failure_probability"),
                    probability_0_5r=evaluation.get("probability_0_5r"), probability_1r=evaluation.get("probability_1r"),
                    probability_1_5r=evaluation.get("probability_1_5r"), probability_2r=evaluation.get("probability_2r"),
                    historical_score=evaluation.get("historical_score") or 0.0, ranking_adjustment=evaluation.get("ranking_adjustment") or 0.0,
                    ranking_adjustment_applied=False, defer_reject_reason=evaluation.get("defer_reject_reason"),
                    peer_group_hash=evaluation.get("peer_group_hash"), source=evaluation.get("source"),
                    evaluation_source=evaluation.get("evaluation_source"), created_at=now,
                )
                db.add(row)
                db.flush()
                written += 1
            except Exception as exc:
                logger.warning("Historical intelligence observation capture failed for candidate_id=%s: %s", candidate_id, exc.__class__.__name__)
                db.rollback()
                continue
        db.commit()
    return written
