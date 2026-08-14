"""Edge-quality investigation Phase D + Historical-Intelligence-Semantics-Audit directive
regression tests: combination-level (strategy+symbol) DEMO execution gating in
entry_intelligence.py.

Directional-edge semantics (the audit's core fix): a combination-level result that is
UNSTABLE (train/OOS disagree in sign, or genuinely contradictory) still blocks candidate-level
evaluation outright. A combination-level result that is reliably, reproducibly NEGATIVE_EDGE
(both windows agree it's negative) is real, actionable intelligence and must NOT be silently
discarded -- it is allowed through to candidate-level evaluation, where it can inform a
REJECT/DEFER decision instead of just vanishing. The absence of a combination-level result
(INSUFFICIENT) never blocks anything beyond what the strategy-level gate already decided --
strictly additive, never a new default-block."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.historical_intelligence import entry_intelligence, trust_gating, walk_forward
from backend.mt5_strategies.context import build_strategy_context

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _mk_rows(n: int, *, base: float = 1.1000, wick: float = 0.0015) -> list[dict]:
    rows = []
    t = NOW - timedelta(minutes=15 * n)
    price = base
    for i in range(n):
        c = base
        o = price
        h = max(o, c) + wick
        low = min(o, c) - wick
        rows.append({"time": (t + timedelta(minutes=15 * i)).isoformat(), "open": o, "high": h, "low": low, "close": c, "tick_volume": 100, "spread": 1})
        price = c
    return rows


def _ctx():
    rows = _mk_rows(100)
    ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal("1.1010"), ask=Decimal("1.1012"), spread=Decimal("0.0002"), now=NOW)
    assert ctx is not None
    return ctx


def _wire_common(monkeypatch):
    monkeypatch.setattr(entry_intelligence, "demo_active_enabled", lambda: True)
    monkeypatch.setattr(trust_gating, "get_trust_state", lambda strategy_id: {"trust_state": trust_gating.HIST_INTEL_ACTIVE, "reason": "test"})
    monkeypatch.setattr(walk_forward, "latest_edge_stability", lambda **kwargs: {"edge_stability": walk_forward.EDGE_STRONG, "directional_edge": walk_forward.DIRECTIONAL_POSITIVE_EDGE})


def test_combination_level_unstable_blocks_even_when_strategy_level_passes(monkeypatch):
    _wire_common(monkeypatch)
    monkeypatch.setattr(walk_forward, "latest_edge_stability_for_symbol", lambda **kwargs: {"edge_stability": walk_forward.EDGE_FAILED_OOS, "directional_edge": walk_forward.DIRECTIONAL_UNSTABLE})

    result = entry_intelligence.evaluate_historical_intelligence_sync(
        ctx=_ctx(), strategy_id="mtfai1", contributing_strategies=["mtfai1"], strategy_family=None,
        entry=1.1010, stop_loss=1.0990, take_profit=1.1050, entry_time=NOW,
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "COMBINATION_EDGE_UNSTABLE"
    assert result["ranking_adjustment"] == 0.0


def test_combination_level_reliably_negative_is_NOT_blocked_outright(monkeypatch):
    """The core semantic fix: a reproducible NEGATIVE_EDGE combination result (e.g. the real
    mtfai1+EURUSD+SHORT+REVERSAL finding -- train and OOS both negative) must be allowed to
    reach candidate-level evaluation, not silently discarded as if it were noise."""
    _wire_common(monkeypatch)
    monkeypatch.setattr(walk_forward, "latest_edge_stability_for_symbol", lambda **kwargs: {"edge_stability": walk_forward.EDGE_FAILED_OOS, "directional_edge": walk_forward.DIRECTIONAL_NEGATIVE_EDGE})

    result = entry_intelligence.evaluate_historical_intelligence_sync(
        ctx=_ctx(), strategy_id="mtfai1", contributing_strategies=["mtfai1"], strategy_family=None,
        entry=1.1010, stop_loss=1.0990, take_profit=1.1050, entry_time=NOW,
    )
    assert result["reason"] != "COMBINATION_EDGE_UNSTABLE"  # not blocked by the combination gate itself
    assert result["status"] in ("EVALUATED", "UNAVAILABLE")  # may still be UNAVAILABLE for a DIFFERENT reason (e.g. thin peer-group sample), just not this one


def test_absent_combination_result_never_blocks_beyond_strategy_gate(monkeypatch):
    _wire_common(monkeypatch)
    monkeypatch.setattr(walk_forward, "latest_edge_stability_for_symbol", lambda **kwargs: {"edge_stability": walk_forward.EDGE_INSUFFICIENT_SAMPLE, "directional_edge": walk_forward.DIRECTIONAL_INSUFFICIENT, "reason": "no combination-level run yet"})

    result = entry_intelligence.evaluate_historical_intelligence_sync(
        ctx=_ctx(), strategy_id="mtfai1", contributing_strategies=["mtfai1"], strategy_family=None,
        entry=1.1010, stop_loss=1.0990, take_profit=1.1050, entry_time=NOW,
    )
    assert result["reason"] != "COMBINATION_EDGE_UNSTABLE"  # the combination gate itself must not have fired


def test_combination_level_acceptable_does_not_block(monkeypatch):
    _wire_common(monkeypatch)
    monkeypatch.setattr(walk_forward, "latest_edge_stability_for_symbol", lambda **kwargs: {"edge_stability": walk_forward.EDGE_ACCEPTABLE, "directional_edge": walk_forward.DIRECTIONAL_POSITIVE_EDGE})

    result = entry_intelligence.evaluate_historical_intelligence_sync(
        ctx=_ctx(), strategy_id="mtfai1", contributing_strategies=["mtfai1"], strategy_family=None,
        entry=1.1010, stop_loss=1.0990, take_profit=1.1050, entry_time=NOW,
    )
    assert result["reason"] != "COMBINATION_EDGE_UNSTABLE"


def test_strategy_level_unstable_blocks_before_fingerprint_is_even_built(monkeypatch):
    _wire_common(monkeypatch)
    monkeypatch.setattr(walk_forward, "latest_edge_stability", lambda **kwargs: {"edge_stability": walk_forward.EDGE_FAILED_OOS, "directional_edge": walk_forward.DIRECTIONAL_UNSTABLE})

    result = entry_intelligence.evaluate_historical_intelligence_sync(
        ctx=_ctx(), strategy_id="mtfai1", contributing_strategies=["mtfai1"], strategy_family=None,
        entry=1.1010, stop_loss=1.0990, take_profit=1.1050, entry_time=NOW,
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "STRATEGY_EDGE_UNSTABLE_OR_INSUFFICIENT"


def test_strategy_level_reliably_negative_is_NOT_blocked_outright(monkeypatch):
    _wire_common(monkeypatch)
    monkeypatch.setattr(walk_forward, "latest_edge_stability", lambda **kwargs: {"edge_stability": walk_forward.EDGE_FAILED_OOS, "directional_edge": walk_forward.DIRECTIONAL_NEGATIVE_EDGE})
    monkeypatch.setattr(walk_forward, "latest_edge_stability_for_symbol", lambda **kwargs: {"edge_stability": walk_forward.EDGE_INSUFFICIENT_SAMPLE, "directional_edge": walk_forward.DIRECTIONAL_INSUFFICIENT})

    result = entry_intelligence.evaluate_historical_intelligence_sync(
        ctx=_ctx(), strategy_id="mtfai1", contributing_strategies=["mtfai1"], strategy_family=None,
        entry=1.1010, stop_loss=1.0990, take_profit=1.1050, entry_time=NOW,
    )
    assert result["reason"] != "STRATEGY_EDGE_UNSTABLE_OR_INSUFFICIENT"
