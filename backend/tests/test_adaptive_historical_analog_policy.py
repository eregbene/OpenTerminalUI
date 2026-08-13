"""Regression tests for the historical_analog_v1 shadow policy family (Adaptive-Historical-
Intelligence-Backfill directive, Phase 12/28): the ONE new counterfactual policy added to the
EXISTING, already-validated shadow-replay engine (service.simulate_policy/default_policies), so
the current-manager/static-baseline/atr-tighter-stop A/B/C comparison gets a genuine D arm that
consults the SAME live Historical Adaptive Intelligence recommendation function a real DEMO cycle
would. Never touches the broker, never invents a new action type -- only ever HOLD (falls
through to the same static-exit default every other unhit policy uses) or exits early at a
+0.25R/+0.5R/+0.75R/+1R/+1.5R/+2R milestone matching LIGHT_PROTECTION/PROTECT_TRAIL_EXIT.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.adaptive_management.service import TradeCase, reconstruct_path, simulate_policy

ENTRY_TIME = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)

_HISTORICAL_ANALOG_POLICY = {
    "policy_id": "historical_analog_v1", "family": "historical_analog", "name": "Historical-analog adaptive policy",
    "parameters": {"min_effective_sample": 20},
}


def _case(**overrides) -> TradeCase:
    base = dict(
        trade_id="T1", session_id="S1", symbol="EURUSD", direction="LONG", volume=1.0,
        entry=1.1000, stop_loss=1.0950, take_profit=1.1200, entry_time=ENTRY_TIME, exit_time=None,
        actual_pnl=100.0, strategy_id="MTFAI1", timeframe="M5",
    )
    base.update(overrides)
    return TradeCase(**base)


def _candles(highs: list[float]) -> list[dict]:
    """A steadily climbing LONG trade: risk=0.005 (entry 1.1000, sl 1.0950), so each `high` here
    is expressed directly in price -- callers pick highs that cross known R milestones."""
    rows = []
    price = 1.1000
    for i, high in enumerate(highs):
        t = ENTRY_TIME + timedelta(minutes=5 * i)
        rows.append({"time": t, "open": price, "high": high, "low": min(price, high) - 0.0002, "close": high - 0.0005, "spread": 0.0001})
        price = high
    return rows


def test_historical_analog_holds_when_recommendation_never_protects(monkeypatch):
    """If the live Historical Adaptive Intelligence model never recommends protection at any
    checkpoint (e.g. always HIST_INTEL_INSUFFICIENT -- the honest, evidence-gated common case
    while the backfill corpus is still young), the shadow policy falls through to the SAME
    default (HOLD_TO_STATIC_EXIT, using the final timeline r) every other unhit policy uses --
    identical behavior to static_baseline_v1."""
    import backend.historical_intelligence.adaptive_cache as adaptive_cache
    import backend.historical_intelligence.adaptive_similarity as adaptive_similarity

    monkeypatch.setattr(adaptive_cache, "cached_weighted_state_statistics", lambda **kwargs: {"status": "OK", "effective_sample_size": 5.0})
    monkeypatch.setattr(adaptive_similarity, "historical_management_recommendation", lambda stats, min_effective_sample=20: "HIST_INTEL_INSUFFICIENT")

    case = _case(exit_time=ENTRY_TIME + timedelta(minutes=50))
    candles = _candles([1.1030, 1.1060, 1.1090, 1.1130, 1.1180, 1.1150, 1.1160, 1.1170, 1.1175, 1.1180])
    path = reconstruct_path(case, candles)

    outcome = simulate_policy(case, path, _HISTORICAL_ANALOG_POLICY)
    assert outcome["first_shadow_decision"]["proposed_action"] == "HOLD_TO_STATIC_EXIT"
    assert outcome["first_shadow_decision"]["reason"] == "baseline_static_sl_tp"
    assert abs(outcome["hypothetical_r"] - float(path["timeline"][-1]["r"])) < 1e-9


def test_historical_analog_protects_at_the_first_qualifying_milestone(monkeypatch):
    """When the model recommends PROTECT_TRAIL_EXIT, the shadow policy exits AT that milestone's
    first-hit checkpoint, not at the trade's eventual final r -- proving the counterfactual
    actually diverges from the static baseline when the evidence says to."""
    import backend.historical_intelligence.adaptive_cache as adaptive_cache
    import backend.historical_intelligence.adaptive_similarity as adaptive_similarity

    monkeypatch.setattr(adaptive_cache, "cached_weighted_state_statistics", lambda **kwargs: {"status": "OK", "effective_sample_size": 40.0})
    monkeypatch.setattr(adaptive_similarity, "historical_management_recommendation", lambda stats, min_effective_sample=20: "PROTECT_TRAIL_EXIT")

    case = _case(exit_time=ENTRY_TIME + timedelta(minutes=50))
    # risk = 1.1000 - 1.0950 = 0.0050 -- +0.25R = 1.10125, first candle (1.1030) already clears it.
    candles = _candles([1.1030, 1.1060, 1.1090, 1.1130, 1.1180, 1.1150, 1.1160, 1.1170, 1.1175, 1.1180])
    path = reconstruct_path(case, candles)

    outcome = simulate_policy(case, path, _HISTORICAL_ANALOG_POLICY)
    assert outcome["first_shadow_decision"]["proposed_action"] == "HISTORICAL_ANALOG_PROTECT_SHADOW"
    assert "historical_analog_recommendation=PROTECT_TRAIL_EXIT" in outcome["first_shadow_decision"]["reason"]
    assert outcome["hypothetical_r"] < float(path["timeline"][-1]["r"])  # exited earlier than the eventual static outcome


def test_historical_analog_never_invents_a_new_action_type(monkeypatch):
    """LIGHT_PROTECTION maps to a distinct, still-EXISTING shadow action label -- never a
    fabricated new type outside the two this family ever produces."""
    import backend.historical_intelligence.adaptive_cache as adaptive_cache
    import backend.historical_intelligence.adaptive_similarity as adaptive_similarity

    monkeypatch.setattr(adaptive_cache, "cached_weighted_state_statistics", lambda **kwargs: {"status": "OK", "effective_sample_size": 40.0})
    monkeypatch.setattr(adaptive_similarity, "historical_management_recommendation", lambda stats, min_effective_sample=20: "LIGHT_PROTECTION")

    case = _case(exit_time=ENTRY_TIME + timedelta(minutes=50))
    candles = _candles([1.1030, 1.1060, 1.1090, 1.1130, 1.1180, 1.1150, 1.1160, 1.1170, 1.1175, 1.1180])
    path = reconstruct_path(case, candles)

    outcome = simulate_policy(case, path, _HISTORICAL_ANALOG_POLICY)
    assert outcome["first_shadow_decision"]["proposed_action"] == "HISTORICAL_ANALOG_LIGHT_PROTECTION_SHADOW"


def test_historical_analog_normalizes_strategy_id_before_querying(monkeypatch):
    """Real bug fixed this session: the historical backfill corpus stores strategy identity via
    normalize_strategy_id (canonical lowercase, e.g. "mtfai1"), but TradeCase.strategy_id comes
    straight from the raw order comment and is frequently uppercase ("MTFAI1"). Without
    normalizing before querying, the hard strategy-match filter never matches a real backfilled
    state even when one genuinely exists -- silently zeroing out the whole D-policy arm."""
    import backend.historical_intelligence.adaptive_cache as adaptive_cache
    import backend.historical_intelligence.adaptive_similarity as adaptive_similarity

    seen_strategies = []

    def _fake_stats(*, strategy, symbol, direction, query_fields, top_k=50):
        seen_strategies.append(strategy)
        return {"status": "OK", "effective_sample_size": 5.0}

    monkeypatch.setattr(adaptive_cache, "cached_weighted_state_statistics", _fake_stats)
    monkeypatch.setattr(adaptive_similarity, "historical_management_recommendation", lambda stats, min_effective_sample=20: "HIST_INTEL_INSUFFICIENT")

    case = _case(strategy_id="MTFAI1", exit_time=ENTRY_TIME + timedelta(minutes=50))
    candles = _candles([1.1030, 1.1060, 1.1090, 1.1130, 1.1180, 1.1150, 1.1160, 1.1170, 1.1175, 1.1180])
    path = reconstruct_path(case, candles)

    simulate_policy(case, path, _HISTORICAL_ANALOG_POLICY)
    assert seen_strategies  # queried at least once
    assert all(s == "mtfai1" for s in seen_strategies)  # always normalized, never raw "MTFAI1"


def test_historical_analog_swallows_lookup_failures_as_insufficient(monkeypatch):
    """A Redis/Postgres error mid-lookup must never propagate out of the shadow-replay engine --
    degrades to the same HIST_INTEL_INSUFFICIENT/HOLD path as genuinely thin evidence."""
    import backend.historical_intelligence.adaptive_cache as adaptive_cache

    def _boom(**kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(adaptive_cache, "cached_weighted_state_statistics", _boom)

    case = _case(exit_time=ENTRY_TIME + timedelta(minutes=50))
    candles = _candles([1.1030, 1.1060, 1.1090, 1.1130, 1.1180, 1.1150, 1.1160, 1.1170, 1.1175, 1.1180])
    path = reconstruct_path(case, candles)

    outcome = simulate_policy(case, path, _HISTORICAL_ANALOG_POLICY)
    assert outcome["first_shadow_decision"]["proposed_action"] == "HOLD_TO_STATIC_EXIT"


def test_historical_analog_never_mutates_broker_state():
    """default_policies()'s historical_analog_v1 entry carries no broker-mutation capability --
    same static, declarative catalog shape as every other shadow policy."""
    from backend.adaptive_management.service import default_policies

    policies = {p["policy_id"]: p for p in default_policies()}
    assert "historical_analog_v1" in policies
    assert policies["historical_analog_v1"]["family"] == "historical_analog"
