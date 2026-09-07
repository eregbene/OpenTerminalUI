"""BSI intelligence migration (2026-09-01): fusion.py's execution-eligibility gate must respect
the WINNING BSI subtype's own bsi_subtype_activation_status(), not just the coarse family-level
activation_status("bsi") -- every BSI subtype signal shares the same strategy_id ("bsi"), so the
family-level check alone cannot tell a SHADOW_MT5 subtype's candidate apart from an ACTIVE_MT5
one. This is the exact gap that would otherwise let a shadow/observation-only subtype (e.g. a
still-unvalidated bsi_new_york) become order-eligible merely because a DIFFERENT subtype
(bsi_under_over) has been promoted to ACTIVE_MT5 at the family level.

See backend/mt5_strategies/fusion.py::build_candidates' own inline comment for the full
reasoning."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.mt5_strategies.fusion import build_candidates
from backend.mt5_strategies.models import ACTIVE_MT5, DISABLED, SHADOW_MT5, StrategySignal

NOW = datetime.now(timezone.utc)


def _bsi_signal(*, subtype_activation_status: str | None, direction: str = "LONG") -> StrategySignal:
    evidence: dict = {"subtype": "bsi_under_over"}
    if subtype_activation_status is not None:
        evidence["subtype_activation_status"] = subtype_activation_status
    return StrategySignal(
        strategy_id="bsi", strategy_family="bsi", symbol="EURUSD", broker_symbol="EURUSD",
        direction=direction, timeframe="M15", generated_at=NOW, valid=True, raw_signal_strength=75.0,
        proposed_entry=1.1010, stop_loss=1.0990, take_profit=1.1060, reward_risk=2.5,
        regime="trending_up", evidence=evidence,
    )


def test_bsi_active_subtype_is_execution_eligible():
    sig = _bsi_signal(subtype_activation_status=ACTIVE_MT5)
    candidates = build_candidates(symbol="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", cycle_id="C-bsi-1", signals=[sig], htf_trend_h4="bullish", now=NOW)
    assert len(candidates) == 1
    assert candidates[0]["strategy_activation"] == ACTIVE_MT5


def test_bsi_shadow_subtype_stays_shadow_even_though_signal_is_valid():
    """The core fix: a SHADOW_MT5-gated subtype's signal is still fused/scored/persisted (for
    calibration/shadow data collection), but must NEVER be reported as ACTIVE_MT5 -- regardless
    of what the coarse family-level activation_status("bsi") would say (which, in a real
    deployment where bsi_under_over is promoted, would itself be ACTIVE_MT5 -- exactly the
    scenario this test guards against)."""
    sig = _bsi_signal(subtype_activation_status=SHADOW_MT5)
    candidates = build_candidates(symbol="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", cycle_id="C-bsi-2", signals=[sig], htf_trend_h4="bullish", now=NOW)
    assert len(candidates) == 1
    assert candidates[0]["strategy_activation"] == SHADOW_MT5
    assert candidates[0]["strategy_activation"] != ACTIVE_MT5


def test_bsi_signal_missing_subtype_stamp_fails_closed_to_disabled():
    """Defense in depth: a BSI signal that somehow reaches fusion without the per-subtype stamp
    (should never happen via the real evaluate_bsi() path, which always sets it on a valid
    signal) must fail CLOSED, not silently inherit the family-level status."""
    sig = _bsi_signal(subtype_activation_status=None)
    candidates = build_candidates(symbol="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", cycle_id="C-bsi-3", signals=[sig], htf_trend_h4="bullish", now=NOW)
    assert len(candidates) == 1
    assert candidates[0]["strategy_activation"] == DISABLED


def test_bsi_disabled_subtype_stamp_is_respected_too():
    sig = _bsi_signal(subtype_activation_status=DISABLED)
    candidates = build_candidates(symbol="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", cycle_id="C-bsi-4", signals=[sig], htf_trend_h4="bullish", now=NOW)
    assert len(candidates) == 1
    assert candidates[0]["strategy_activation"] == DISABLED
