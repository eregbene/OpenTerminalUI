"""BSI V3 profile-gated demo bridge.

This is not a replacement for the full V3 detector registry. It is the live-engine
gate that lets the MT5 BSI lane consume the prepared V3 next-session profile and
fail closed when a strategy/symbol bucket is not allowed.
"""
from __future__ import annotations

from dataclasses import replace

from backend.adaptive_management.v3_faiz import (
    allow_v3_demo_entry_from_profile,
    load_v3_next_session_profile,
)
from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families.bsi_v2_engine import evaluate_bsi_v2_active
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

    Candidate geometry is still supplied by the existing BSI lane until the full
    V3 detector registry is promoted into backend runtime modules. The V3 profile
    is therefore a hard pre-routing gate, not a claim that every V3 video detector
    is live in MT5.
    """

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

    profile = load_v3_next_session_profile()
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
