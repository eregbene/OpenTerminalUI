"""DEMO-only MTFAI1 entry-quality experiment.

A standalone MTFAI1 candidate (no other strategy family also producing a valid signal on the
same symbol+direction this cycle) may not become `best` unless it lacks confirmation, in which
case the next-ranked ACTIVE_MT5 non-MTFAI1 candidate that still clears the confidence threshold
is considered instead -- same fallback shape as the diversity cap, and applied strictly AFTER
it. MTFAI1's own confidence/ranking/rank are never altered; confirmation is never fabricated.
DEMO only.

Tests _apply_mtfai1_confirmation_gate directly -- pure, synchronous, side-effect-free (aside
from tagging rejection_reasons on the candidate dicts passed in), mirroring
test_mt5_demo_diversity_cap.py's approach.
"""
from __future__ import annotations

from types import SimpleNamespace

from backend.brokers.mt5 import autonomous
from backend.brokers.mt5.autonomous import MT5AutonomousTradingService


def _mk_service(account_mode: str = "DEMO") -> MT5AutonomousTradingService:
    adapter = SimpleNamespace(config=SimpleNamespace(account_mode=account_mode))
    return MT5AutonomousTradingService(adapter=adapter)


def _mk_candidate(strategy_id: str, *, broker_symbol: str, direction: str = "LONG", confidence: float = 90.0, rank: int = 1, contributing: list[str] | None = None) -> dict:
    context = {"strategy_id": strategy_id}
    if contributing is not None:
        context["contributing_strategies"] = contributing
    return {
        "broker_symbol": broker_symbol,
        "canonical_pair": broker_symbol,
        "direction": direction,
        "context_hash": f"hash-{broker_symbol}-{strategy_id}-{rank}",
        "context": context,
        "trade_confidence": {"overall_score": confidence},
        "rejection_reasons": [],
        "ranking_score": 100 - rank,
    }


def test_standalone_mtfai1_is_deferred_and_alternative_picked():
    service = _mk_service()
    mtfai1_row = _mk_candidate("mtfai1", broker_symbol="EURUSD", direction="LONG", rank=1)
    alt_row = _mk_candidate("vwap_reversion", broker_symbol="GBPUSD", direction="SHORT", rank=2)
    all_candidates = [mtfai1_row, alt_row]
    eligible = [mtfai1_row, alt_row]

    best, gate = service._apply_mtfai1_confirmation_gate("C1", all_candidates, eligible, mtfai1_row)

    assert best is alt_row
    assert gate["enabled"] is True
    assert gate["applicable"] is True
    assert gate["confirmed"] is False
    assert gate["deferred"] is True
    assert gate["defer_reason"] == "MTFAI1_CONFIRMATION_REQUIRED"
    assert gate["confirming_strategy_ids"] == []
    assert gate["executed_strategy_id"] == "vwap_reversion"
    assert "MTFAI1_CONFIRMATION_REQUIRED" in mtfai1_row["rejection_reasons"]
    assert alt_row["rejection_reasons"] == []


def test_mtfai1_confirmed_by_independent_momentum_signal_same_symbol_and_direction():
    service = _mk_service()
    mtfai1_row = _mk_candidate("mtfai1", broker_symbol="EURUSD", direction="LONG", rank=1)
    momentum_row = _mk_candidate("momentum", broker_symbol="EURUSD", direction="LONG", rank=5)
    all_candidates = [mtfai1_row, momentum_row]
    eligible = [mtfai1_row]

    best, gate = service._apply_mtfai1_confirmation_gate("C1", all_candidates, eligible, mtfai1_row)

    assert best is mtfai1_row
    assert gate["confirmed"] is True
    assert gate["confirming_strategy_ids"] == ["momentum"]
    assert gate["deferred"] is False
    assert gate["executed_strategy_id"] == "mtfai1"
    assert mtfai1_row["rejection_reasons"] == []


def test_confirmation_via_contributing_strategies_on_fused_candidate():
    """A fused multi-strategy candidate (breakout anchor, smc_continuation also agreeing) on the
    same symbol+direction counts as confirmation even though smc_continuation isn't the anchor
    of that other row."""
    service = _mk_service()
    mtfai1_row = _mk_candidate("mtfai1", broker_symbol="USDJPY", direction="SHORT", rank=1)
    fused_row = _mk_candidate("breakout", broker_symbol="USDJPY", direction="SHORT", rank=4, contributing=["breakout", "smc_continuation"])
    all_candidates = [mtfai1_row, fused_row]

    best, gate = service._apply_mtfai1_confirmation_gate("C1", all_candidates, [mtfai1_row], mtfai1_row)

    assert best is mtfai1_row
    assert gate["confirmed"] is True
    assert gate["confirming_strategy_ids"] == ["breakout", "smc_continuation"]


def test_same_symbol_but_opposite_direction_does_not_confirm():
    service = _mk_service()
    mtfai1_row = _mk_candidate("mtfai1", broker_symbol="EURUSD", direction="LONG", rank=1)
    opposite_row = _mk_candidate("momentum", broker_symbol="EURUSD", direction="SHORT", rank=5)
    all_candidates = [mtfai1_row, opposite_row]
    eligible = [mtfai1_row]

    best, gate = service._apply_mtfai1_confirmation_gate("C1", all_candidates, eligible, mtfai1_row)

    assert gate["confirmed"] is False
    assert best is None  # no non-MTFAI1 alternative in eligible_for_execution either


def test_non_whitelisted_family_does_not_confirm():
    """mean_reversion / vwap_reversion / support_resistance_bounce / session_breakout /
    ema_trend are real strategies but not on the confirmation whitelist -- agreeing on the same
    symbol+direction still leaves MTFAI1 standalone."""
    service = _mk_service()
    mtfai1_row = _mk_candidate("mtfai1", broker_symbol="EURUSD", direction="LONG", rank=1)
    mean_rev_row = _mk_candidate("mean_reversion", broker_symbol="EURUSD", direction="LONG", rank=5)
    alt_row = _mk_candidate("breakout", broker_symbol="GBPUSD", direction="LONG", rank=2)
    all_candidates = [mtfai1_row, mean_rev_row, alt_row]
    eligible = [mtfai1_row, alt_row]

    best, gate = service._apply_mtfai1_confirmation_gate("C1", all_candidates, eligible, mtfai1_row)

    assert gate["confirmed"] is False
    assert best is alt_row


def test_never_forces_a_trade_when_no_alternative_qualifies():
    service = _mk_service()
    mtfai1_row = _mk_candidate("mtfai1", broker_symbol="EURUSD", direction="LONG", rank=1)
    all_candidates = [mtfai1_row]
    eligible = [mtfai1_row]

    best, gate = service._apply_mtfai1_confirmation_gate("C1", all_candidates, eligible, mtfai1_row)

    assert best is None
    assert gate["deferred"] is True
    assert gate["executed_strategy_id"] is None
    assert "MTFAI1_CONFIRMATION_REQUIRED" in mtfai1_row["rejection_reasons"]


def test_ignored_in_live_mode():
    service = _mk_service(account_mode="LIVE")
    mtfai1_row = _mk_candidate("mtfai1", broker_symbol="EURUSD", direction="LONG", rank=1)
    alt_row = _mk_candidate("breakout", broker_symbol="GBPUSD", direction="LONG", rank=2)
    all_candidates = [mtfai1_row, alt_row]

    best, gate = service._apply_mtfai1_confirmation_gate("C1", all_candidates, [mtfai1_row, alt_row], mtfai1_row)

    assert best is mtfai1_row  # never gated outside DEMO, regardless of confirmation
    assert gate["enabled"] is False
    assert gate["applicable"] is False
    assert mtfai1_row["rejection_reasons"] == []


def test_not_applicable_when_best_is_not_mtfai1():
    service = _mk_service()
    non_mtfai1_row = _mk_candidate("breakout", broker_symbol="EURUSD", direction="LONG", rank=1)

    best, gate = service._apply_mtfai1_confirmation_gate("C1", [non_mtfai1_row], [non_mtfai1_row], non_mtfai1_row)

    assert best is non_mtfai1_row
    assert gate["enabled"] is True
    assert gate["applicable"] is False
    assert gate["confirmed"] is None
    assert non_mtfai1_row["rejection_reasons"] == []


def test_noop_when_best_is_none():
    service = _mk_service()
    best, gate = service._apply_mtfai1_confirmation_gate("C1", [], [], None)
    assert best is None
    assert gate["applicable"] is False
    assert gate["executed_strategy_id"] is None


def test_confidence_and_ranking_never_altered():
    service = _mk_service()
    mtfai1_row = _mk_candidate("mtfai1", broker_symbol="EURUSD", direction="LONG", rank=1, confidence=92.5)
    momentum_row = _mk_candidate("momentum", broker_symbol="EURUSD", direction="LONG", rank=5)
    original_confidence = dict(mtfai1_row["trade_confidence"])
    original_ranking_score = mtfai1_row["ranking_score"]

    service._apply_mtfai1_confirmation_gate("C1", [mtfai1_row, momentum_row], [mtfai1_row], mtfai1_row)

    assert mtfai1_row["trade_confidence"] == original_confidence
    assert mtfai1_row["ranking_score"] == original_ranking_score


def test_clears_stale_lower_ranked_tag_from_the_chosen_alternative():
    """Simulates the diversity cap already having tagged every non-winner with
    LOWER_RANKED_CANDIDATE before this gate ran and swapped `best` again."""
    service = _mk_service()
    mtfai1_row = _mk_candidate("mtfai1", broker_symbol="EURUSD", direction="LONG", rank=1)
    alt_row = _mk_candidate("breakout", broker_symbol="GBPUSD", direction="LONG", rank=2)
    alt_row["rejection_reasons"] = ["LOWER_RANKED_CANDIDATE"]  # stamped by the diversity cap pass
    eligible = [mtfai1_row, alt_row]

    best, gate = service._apply_mtfai1_confirmation_gate("C1", [mtfai1_row, alt_row], eligible, mtfai1_row)

    assert best is alt_row
    assert alt_row["rejection_reasons"] == []


def test_confirmation_whitelist_contents():
    assert autonomous.MTFAI1_CONFIRMING_STRATEGY_IDS == {"momentum", "smc_continuation", "breakout", "trend_pullback", "liquidity_sweep_reversal"}
