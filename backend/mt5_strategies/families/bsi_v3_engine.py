"""BSI V3 profile-gated demo bridge.

This is not a replacement for the full V3 detector registry. It is the live-engine
gate that lets the MT5 BSI lane consume the prepared V3 next-session profile and
fail closed when a strategy/symbol bucket is not allowed.
"""
from __future__ import annotations

import os
from dataclasses import replace

from backend.adaptive_management.v3_faiz import (
    allow_v3_demo_entry_from_profile,
    load_v3_next_session_profile,
)
from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families.bsi_v2_engine import evaluate_bsi_v2_active
from backend.mt5_strategies.families.bsi_v3_runtime_detectors import evaluate_bsi_v3_planned_runtime_detectors, evaluate_bsi_v3_runtime_detectors
from backend.mt5_strategies.models import StrategySignal, invalid_signal


BSI_BASELINE_V3_UPDATED_FAIZ = "BSI_BASELINE_V3_UPDATED_FAIZ"

_V2_SUBTYPE_TO_V3_STRATEGY = {
    "bsi_order_flow": "bsi_v3_order_flow",
    "bsi_abc": "bsi_v3_abc",
    "bsi_abcd": "bsi_v3_abcd",
    "bsi_reactionary": "bsi_v3_reactionary_block",
}


def _reject(ctx: StrategyContext, reason: str, evidence: dict | None = None) -> StrategySignal:
    signal = invalid_signal(
        "bsi",
        "bsi",
        symbol=ctx.symbol,
        broker_symbol=ctx.broker_symbol,
        timeframe="M15(bsi_v3)",
        generated_at=ctx.generated_at,
        regime=ctx.regime,
        reason=reason,
    )
    signal.evidence.update(
        {
            "bsi_version": BSI_BASELINE_V3_UPDATED_FAIZ,
            "active_methodology": BSI_BASELINE_V3_UPDATED_FAIZ,
        }
    )
    if evidence:
        signal.evidence.update(evidence)
    return signal


def evaluate_bsi_v3_profile_gated(ctx: StrategyContext) -> StrategySignal:
    """Evaluate BSI through the V3 next-session adaptive profile gate.

    The primary path is the strategy-specific V3 runtime detector registry. The
    older V2-geometry bridge is now an explicit opt-out fallback only.
    """

    profile = load_v3_next_session_profile()
    if profile is None:
        return _reject(ctx, "v3_next_session_profile_missing")
    if not profile.point_in_time_safe:
        return _reject(
            ctx,
            "v3_profile_not_point_in_time_safe",
            {"v3_profile_id": profile.profile_id, "v3_risk_mode": profile.risk_mode},
        )
    if profile.generic_detector_routing_count:
        return _reject(
            ctx,
            "v3_profile_contains_generic_detector_routes",
            {"v3_profile_id": profile.profile_id, "v3_risk_mode": profile.risk_mode},
        )

    allowed_strategy_ids = {bucket.strategy_id for bucket in profile.allowed_buckets if bucket.symbol == ctx.symbol.upper()}
    blocked_strategy_ids = {bucket.strategy_id for bucket in profile.blocked_buckets if bucket.symbol == ctx.symbol.upper()}

    planned_enabled = os.getenv("BSI_V3_PLANNED_ENTRY_LIVE", "true").strip().lower() not in {"false", "0", "off", "no"}
    runtime_signal = (
        evaluate_bsi_v3_planned_runtime_detectors(ctx, set())
        if planned_enabled
        else evaluate_bsi_v3_runtime_detectors(ctx, set())
    )
    if runtime_signal is not None and runtime_signal.valid:
        v3_strategy_id = str(runtime_signal.evidence.get("v3_strategy_id") or "")
        if v3_strategy_id in blocked_strategy_ids:
            return _reject(
                ctx,
                "v3_profile_reliable_hard_block",
                {
                    "v3_strategy_id": v3_strategy_id,
                    "v3_profile_id": profile.profile_id,
                    "v3_risk_mode": profile.risk_mode,
                    "v3_bridge_source": "v3_runtime_detector_registry",
                },
            )
        decision = allow_v3_demo_entry_from_profile(profile, strategy_id=v3_strategy_id, symbol=ctx.symbol)
        if not decision.allow_entry:
            return _reject(
                ctx,
                decision.reason,
                {
                    "v3_strategy_id": v3_strategy_id,
                    "v3_profile_id": decision.profile_id,
                    "v3_risk_mode": decision.risk_mode,
                    "v3_bridge_source": "v3_runtime_detector_registry",
                },
            )
        return replace(
            runtime_signal,
            evidence={
                **runtime_signal.evidence,
                "v3_profile_id": decision.profile_id,
                "v3_risk_mode": decision.risk_mode,
                "v3_profile_decision": decision.reason,
            },
            metadata={**runtime_signal.metadata, "active_methodology": BSI_BASELINE_V3_UPDATED_FAIZ},
        )

    if os.getenv("BSI_V3_ALLOW_V2_GEOMETRY_FALLBACK", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return _reject(
            ctx,
            "V3_RUNTIME_DETECTOR_NO_SETUP",
            {
                "v3_profile_id": profile.profile_id,
                "v3_risk_mode": profile.risk_mode,
                "v3_allowed_strategy_ids_for_symbol": sorted(allowed_strategy_ids),
                "v3_blocked_strategy_ids_for_symbol": sorted(blocked_strategy_ids),
                "v3_bridge_source": "v3_runtime_detector_registry",
            },
        )

    signal = evaluate_bsi_v2_active(ctx)
    if not signal.valid:
        return replace(
            signal,
            evidence={
                **signal.evidence,
                "bsi_version": BSI_BASELINE_V3_UPDATED_FAIZ,
                "active_methodology": BSI_BASELINE_V3_UPDATED_FAIZ,
                "v3_bridge_source": "bsi_v2_candidate_geometry",
            },
            metadata={**signal.metadata, "active_methodology": BSI_BASELINE_V3_UPDATED_FAIZ},
        )

    subtype = str(signal.evidence.get("setup_subtype") or "")
    v3_strategy_id = _V2_SUBTYPE_TO_V3_STRATEGY.get(subtype)
    if v3_strategy_id is None:
        return _reject(
            ctx,
            "V3_PROFILE_GATE_NO_MAPPED_STRATEGY",
            {"setup_subtype": subtype, "v3_bridge_source": "bsi_v2_candidate_geometry"},
        )

    decision = allow_v3_demo_entry_from_profile(profile, strategy_id=v3_strategy_id, symbol=ctx.symbol)
    if not decision.allow_entry:
        return _reject(
            ctx,
            decision.reason,
            {
                "setup_subtype": subtype,
                "v3_strategy_id": v3_strategy_id,
                "v3_profile_id": decision.profile_id,
                "v3_risk_mode": decision.risk_mode,
                "v3_bridge_source": "bsi_v2_candidate_geometry",
            },
        )

    return replace(
        signal,
        strategy_id="bsi",
        strategy_family="bsi",
        evidence={
            **signal.evidence,
            "bsi_version": BSI_BASELINE_V3_UPDATED_FAIZ,
            "active_methodology": BSI_BASELINE_V3_UPDATED_FAIZ,
            "v3_strategy_id": v3_strategy_id,
            "v3_profile_id": decision.profile_id,
            "v3_risk_mode": decision.risk_mode,
            "v3_profile_decision": decision.reason,
            "v3_bridge_source": "bsi_v2_candidate_geometry",
        },
        metadata={**signal.metadata, "active_methodology": BSI_BASELINE_V3_UPDATED_FAIZ},
    )
