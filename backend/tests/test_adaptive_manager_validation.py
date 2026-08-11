"""Adaptive Trade Manager Validation & Performance Analytics layer -- Part 18's 17 required
tests.

Covers: event capture never mutates the manager's decision, immutable baselines, MFE/MAE/
capture-ratio/giveback correctness, break-even analytics, winner-preservation buckets, loser-
protection metrics, post-exit shadow tracking (no broker orders), original-SL/TP and no-BE
counterfactuals, conservative intrabar ambiguity, managed-vs-baseline expectancy separation,
manager_expectancy_delta, and the frozen-behavior guarantees (existing suites, zero OpenAI,
live trading blocked).
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.adaptive_management.analytics import (
    _position_records,
    break_even_analysis_report,
    core_metrics_report,
    loser_protection_report,
    winner_preservation_report,
)
from backend.adaptive_management.event_capture import capture_management_event, capture_position_baseline
from backend.adaptive_management.orm import AdaptiveManagementEventORM, AdaptiveManagerCounterfactualORM, AdaptivePositionBaselineORM, AdaptivePositionStateORM, AdaptiveTradeEventORM
from backend.adaptive_management.outcome_resolver import AdaptiveManagerOutcomeResolver
from backend.brokers.mt5.config import mt5_config
from backend.brokers.mt5.models import MT5Candle
from backend.shared.db import SessionLocal
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite
from backend.tests.test_mt5_adapter import fake_adapter


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _seed_closed_position(
    db,
    *,
    position_id: str,
    symbol: str = "EURUSD",
    direction: str = "LONG",
    entry: float = 1.1000,
    original_sl: float = 1.0950,
    original_tp: float = 1.1200,
    final_sl: float | None = None,
    final_tp: float | None = None,
    max_r: float = 1.0,
    min_r: float = -0.1,
    risk_money: float = 50.0,
    realized_pnl: float = 0.0,
    exit_reason: str = "TAKE_PROFIT",
    opened_at: datetime | None = None,
    closed_at: datetime | None = None,
    strategy: str = "MTFAI1",
    regime: str = "TREND",
    contaminated: bool = False,
) -> None:
    final_sl = original_sl if final_sl is None else final_sl
    final_tp = original_tp if final_tp is None else final_tp
    opened_at = opened_at or (utcnow() - timedelta(hours=5))
    closed_at = closed_at or utcnow()

    state = AdaptivePositionStateORM(position_id=position_id, symbol=symbol, direction=direction, broker_ticket=position_id)
    state.entry_price = entry
    state.original_entry = entry
    state.original_sl = original_sl
    state.original_tp = original_tp
    state.current_sl = final_sl
    state.current_tp = final_tp
    state.max_achieved_r = max_r
    state.min_achieved_r = min_r
    state.original_risk_money = risk_money
    state.opened_at = opened_at
    state.closed_detected_at = closed_at
    state.strategy_id = strategy
    state.entry_regime = regime
    state.contaminated = contaminated
    db.add(state)

    baseline = AdaptivePositionBaselineORM(position_id=position_id)
    baseline.broker_ticket = position_id
    baseline.symbol = symbol
    baseline.direction = direction
    baseline.original_entry = entry
    baseline.original_sl = original_sl
    baseline.original_tp = original_tp
    baseline.initial_stop_distance = abs(entry - original_sl)
    baseline.initial_reward_risk = round(abs(original_tp - entry) / abs(entry - original_sl), 4)
    baseline.initial_risk_money = risk_money
    baseline.original_strategy = strategy
    db.add(baseline)

    deal = AdaptiveTradeEventORM(event_id=f"EVT_{position_id}", session_id="S1", position_id=position_id, event_type="DEAL", symbol=symbol)
    deal.realized_pnl = realized_pnl
    deal.price = final_tp if exit_reason == "TAKE_PROFIT" else final_sl
    deal.broker_exit_reason = exit_reason
    deal.utc_time = closed_at
    db.add(deal)


# 1. Management-event capture does not alter manager decisions.
def test_event_capture_does_not_alter_manager_decision(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    state = SimpleNamespace(
        position_id="C1", symbol="EURUSD", direction="LONG", entry_price=1.1000, current_sl=1.0950, current_tp=1.1200,
        original_sl=1.0950, original_tp=1.1200, max_achieved_r=1.2, min_achieved_r=-0.1, r_source="MONEY",
        winner_classification="strong_continuation", stop_quality_v2="VALID", partial_profit_stage="NONE", adopted=True,
        managed_automatically=True, strategy_id="MTFAI1", entry_regime="TREND", entry_atr=0.0010, broker_ticket="C1",
        original_entry=1.1000, original_risk_money=50.0,
    )
    action = SimpleNamespace(action_id="A1", action_type="HOLD", requested_sl=None, requested_tp=None, status="hold", evidence={"reason_code": "no_trigger"}, considered_actions=[])
    payload = {"price_current": 1.1010, "profit": 10.0}
    before = (action.action_type, action.requested_sl, action.requested_tp, action.status)

    capture_position_baseline(state=state)
    capture_management_event(cycle_run_id="ADPT_TEST", state=state, payload=payload, action=action, reason="test")

    assert (action.action_type, action.requested_sl, action.requested_tp, action.status) == before


# 2. Original trade state remains immutable.
def test_original_baseline_is_immutable(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    state = SimpleNamespace(position_id="C2", symbol="EURUSD", direction="LONG", entry_price=1.1000, original_entry=1.1000, original_sl=1.0950, original_tp=1.1200, original_risk_money=50.0, strategy_id="MTFAI1", broker_ticket="C2")
    written = capture_position_baseline(state=state)
    assert written is True

    mutated = SimpleNamespace(position_id="C2", symbol="EURUSD", direction="LONG", entry_price=1.1000, original_entry=1.1000, original_sl=1.0500, original_tp=1.2000, original_risk_money=999.0, strategy_id="OTHER", broker_ticket="C2")
    written_again = capture_position_baseline(state=mutated)
    assert written_again is False

    with SessionLocal() as db:
        row = db.get(AdaptivePositionBaselineORM, "C2")
        assert row.original_sl == 1.0950
        assert row.original_tp == 1.1200
        assert row.initial_risk_money == 50.0


# 3. MFE/MAE calculations are correct.
def test_mfe_mae_correct(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with session_local() as db:
        _seed_closed_position(db, position_id="C3", max_r=1.8, min_r=-0.3)
        db.commit()
    rec = next(r for r in _position_records() if r["position_id"] == "C3")
    assert rec["mfe_r"] == 1.8
    assert rec["mae_r"] == -0.3


# 4. Capture ratio is correct (and safely None for losers, per Part 3).
def test_capture_ratio_correct(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with session_local() as db:
        _seed_closed_position(db, position_id="C4A", max_r=2.0, risk_money=50.0, realized_pnl=100.0)  # realized_r = 2.0
        _seed_closed_position(db, position_id="C4B", max_r=0.0, risk_money=50.0, realized_pnl=-40.0, exit_reason="STOP_LOSS")  # never went positive
        db.commit()
    records = {r["position_id"]: r for r in _position_records()}
    assert records["C4A"]["realized_r"] == pytest.approx(2.0)
    assert records["C4A"]["capture_ratio"] == pytest.approx(1.0)
    assert records["C4B"]["capture_ratio"] is None  # no nonsensical ratio for a trade with MFE <= 0


# 5. Giveback calculation is correct.
def test_giveback_correct(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with session_local() as db:
        _seed_closed_position(db, position_id="C5", max_r=3.0, risk_money=50.0, realized_pnl=50.0)  # realized_r = 1.0
        db.commit()
    rec = next(r for r in _position_records() if r["position_id"] == "C5")
    assert rec["realized_r"] == pytest.approx(1.0)
    assert rec["giveback_r"] == pytest.approx(2.0)


# 6. Break-even analytics are correct.
def test_break_even_analytics_correct(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    t0 = utcnow() - timedelta(hours=2)
    with session_local() as db:
        _seed_closed_position(db, position_id="C6", entry=1.1000, original_sl=1.0950, final_sl=1.1000, max_r=2.0, risk_money=50.0, realized_pnl=75.0)
        event1 = AdaptiveManagementEventORM(event_id="E1", cycle_run_id="ADPT_1", position_id="C6", symbol="EURUSD", direction="LONG", entry_price=1.1000, sl_before=1.0950, sl_after=1.1000, current_r=0.3, max_achieved_r=0.3, action_type="MOVE_SL_BREAKEVEN", action_category="MOVE_TO_BREAK_EVEN", action_status="selected", is_at_or_beyond_breakeven=True, is_trailing_action=False, manager_state={})
        event1.created_at = t0
        event1.raw_payload = {}
        event2 = AdaptiveManagementEventORM(event_id="E2", cycle_run_id="ADPT_2", position_id="C6", symbol="EURUSD", direction="LONG", entry_price=1.1000, sl_before=1.1000, sl_after=1.1000, current_r=0.3, max_achieved_r=0.3, action_type="HOLD", action_category="NO_ACTION", action_status="hold", is_at_or_beyond_breakeven=True, is_trailing_action=False, manager_state={})
        event2.created_at = t0 + timedelta(minutes=5)
        event2.raw_payload = {}
        event3 = AdaptiveManagementEventORM(event_id="E3", cycle_run_id="ADPT_3", position_id="C6", symbol="EURUSD", direction="LONG", entry_price=1.1000, sl_before=1.1000, sl_after=1.1000, current_r=0.5, max_achieved_r=1.5, action_type="HOLD", action_category="NO_ACTION", action_status="hold", is_at_or_beyond_breakeven=True, is_trailing_action=False, manager_state={})
        event3.created_at = t0 + timedelta(minutes=10)
        event3.raw_payload = {}
        db.add_all([event1, event2, event3])
        db.commit()

    report = break_even_analysis_report()
    assert report["trades_with_be_activated"] == 1
    assert report["avg_r_at_be_activation"] == pytest.approx(0.3)
    assert report["avg_mfe_after_be"] == pytest.approx(1.5)


# 7. Winner preservation buckets are correct.
def test_winner_preservation_buckets_correct(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with session_local() as db:
        _seed_closed_position(db, position_id="C7A", max_r=1.2, risk_money=50.0, realized_pnl=30.0)  # realized_r 0.6, qualifies >=0.5R and >=1R
        _seed_closed_position(db, position_id="C7B", max_r=2.5, risk_money=50.0, realized_pnl=100.0)  # realized_r 2.0, qualifies up through >=2R
        db.commit()

    report = {row["bucket"]: row for row in winner_preservation_report()}
    assert report[">= +0.5R"]["count"] == 2
    assert report[">= +1.0R"]["count"] == 2
    assert report[">= +1.5R"]["count"] == 1
    assert report[">= +2.0R"]["count"] == 1
    assert report[">= +3.0R"]["count"] == 0


# 8. Loser-protection calculations are correct.
def test_loser_protection_correct(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with session_local() as db:
        # Manager protected: exited at -0.3R instead of the full -1R original risk.
        _seed_closed_position(db, position_id="C8", max_r=0.1, min_r=-0.35, risk_money=50.0, realized_pnl=-15.0, exit_reason="STOP_LOSS", final_sl=1.0980)
        db.commit()

    report = loser_protection_report()
    assert report["losing_trades"] == 1
    assert report["avg_final_realized_r"] == pytest.approx(-0.3)
    assert report["pct_manager_reduced_loss_vs_original_risk"] == pytest.approx(1.0)
    assert report["avg_loss_avoided_vs_initial_risk"] == pytest.approx(0.7)  # -1.0 - (-0.3) = 0.7


# 9. Post-exit shadow tracking creates no broker order.
def test_post_exit_shadow_tracking_creates_no_broker_order(monkeypatch: pytest.MonkeyPatch):
    import backend.adaptive_management.outcome_resolver as outcome_resolver_module

    source = inspect.getsource(outcome_resolver_module)
    assert "order_send" not in source
    assert "submit_market_order" not in source
    assert "submit_order" not in source

    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    exit_time = utcnow() - timedelta(days=4)
    with session_local() as db:
        _seed_closed_position(db, position_id="C9", entry=1.1000, original_sl=1.0950, original_tp=1.1500, max_r=1.0, risk_money=50.0, realized_pnl=20.0, exit_reason="TAKE_PROFIT", closed_at=exit_time, final_tp=1.1050)
        db.commit()

    adapter = fake_adapter()

    async def _fake_candles(symbol, timeframe, count=100, completed_only=True):
        return [MT5Candle(symbol=symbol, timeframe="M5", time=exit_time + timedelta(minutes=5), open=1.1050, high=1.1060, low=1.1040, close=1.1055, tick_volume=1, spread=1, real_volume=1, complete=True)]

    monkeypatch.setattr(adapter, "candles", _fake_candles)
    resolver = AdaptiveManagerOutcomeResolver(adapter=adapter)
    asyncio.run(resolver.run_once())

    assert adapter.client.mt5.order_send_calls == 0


# 10. Original SL/TP counterfactual is correct.
def test_original_sltp_counterfactual_correct(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    opened_at = utcnow() - timedelta(hours=1)
    with session_local() as db:
        _seed_closed_position(db, position_id="C10", entry=1.1000, original_sl=1.0950, original_tp=1.1100, max_r=1.0, risk_money=50.0, realized_pnl=0.0, opened_at=opened_at)
        db.commit()

    adapter = fake_adapter()

    async def _fake_candles(symbol, timeframe, count=100, completed_only=True):
        return [
            MT5Candle(symbol=symbol, timeframe="M5", time=opened_at + timedelta(minutes=5), open=1.1000, high=1.1010, low=1.0995, close=1.1005, tick_volume=1, spread=1, real_volume=1, complete=True),
            MT5Candle(symbol=symbol, timeframe="M5", time=opened_at + timedelta(minutes=10), open=1.1005, high=1.1105, low=1.1000, close=1.1100, tick_volume=1, spread=1, real_volume=1, complete=True),  # TP touched
        ]

    monkeypatch.setattr(adapter, "candles", _fake_candles)
    resolver = AdaptiveManagerOutcomeResolver(adapter=adapter)
    asyncio.run(resolver._resolve_original_sltp_baselines())

    with SessionLocal() as db:
        cf = db.get(AdaptiveManagerCounterfactualORM, "C10")
        assert cf.original_sltp_outcome == "ORIGINAL_TP_FIRST"
        assert cf.original_sltp_r > 0


# 11. Ambiguous intrabar TP/SL uses conservative ordering.
def test_ambiguous_intrabar_uses_conservative_ordering():
    candle = MT5Candle(symbol="EURUSD", timeframe="M5", time=utcnow(), open=1.1000, high=1.1200, low=1.0900, close=1.1050, tick_volume=1, spread=1, real_volume=1, complete=True)
    outcome, touched = AdaptiveManagerOutcomeResolver._first_touch([candle], long=True, sl=1.0950, tp=1.1100)
    assert outcome == "SL"  # both levels fall inside the same candle -- conservative rule wins


# 12. Managed expectancy and baseline expectancy are separate.
def test_managed_and_baseline_expectancy_are_separate(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with session_local() as db:
        _seed_closed_position(db, position_id="C12A", max_r=1.0, risk_money=50.0, realized_pnl=25.0)  # realized_r 0.5
        _seed_closed_position(db, position_id="C12B", max_r=1.0, risk_money=50.0, realized_pnl=-25.0, exit_reason="STOP_LOSS")  # realized_r -0.5
        cf_a = AdaptiveManagerCounterfactualORM(position_id="C12A", symbol="EURUSD", original_sltp_outcome="ORIGINAL_TP_FIRST", original_sltp_r=2.0)
        cf_b = AdaptiveManagerCounterfactualORM(position_id="C12B", symbol="EURUSD", original_sltp_outcome="ORIGINAL_SL_FIRST", original_sltp_r=-1.0)
        db.add_all([cf_a, cf_b])
        db.commit()

    metrics = core_metrics_report()
    assert metrics["managed_expectancy"] == pytest.approx(0.0)  # (0.5 + -0.5) / 2
    assert metrics["original_sltp_baseline_expectancy"] == pytest.approx(0.5)  # (2.0 + -1.0) / 2
    assert metrics["managed_expectancy"] != metrics["original_sltp_baseline_expectancy"]


# 13. manager_expectancy_delta is correct.
def test_manager_expectancy_delta_correct(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with session_local() as db:
        _seed_closed_position(db, position_id="C13A", max_r=1.0, risk_money=50.0, realized_pnl=50.0)  # realized_r 1.0
        cf_a = AdaptiveManagerCounterfactualORM(position_id="C13A", symbol="EURUSD", original_sltp_outcome="ORIGINAL_SL_FIRST", original_sltp_r=-1.0)
        db.add(cf_a)
        db.commit()

    metrics = core_metrics_report()
    assert metrics["manager_expectancy_delta"] == pytest.approx(metrics["managed_expectancy"] - metrics["original_sltp_baseline_expectancy"])
    assert metrics["manager_expectancy_delta"] == pytest.approx(2.0)  # 1.0 - (-1.0)


# 14. Existing adaptive-management decision logic (_select_action) is unchanged by this layer.
def test_existing_select_action_logic_unchanged():
    from backend.adaptive_management.service import AdaptiveManagementService, ManagementCandidate

    service = AdaptiveManagementService()
    candidates = [ManagementCandidate(action_type="HOLD", priority=100), ManagementCandidate(action_type="TRAIL_STOP", priority=5)]
    assert service._select_action(candidates).action_type == "TRAIL_STOP"


# 15. Existing entry-confidence behavior is unchanged by this layer.
def test_existing_confidence_behavior_unchanged():
    from backend.brokers.mt5.confidence import classify_confidence_band, is_autonomous_eligible

    assert classify_confidence_band(76.0) == "valid_autonomous"
    assert is_autonomous_eligible(76.0) is True
    assert is_autonomous_eligible(74.9) is False


# 16. OpenAI calls remain zero -- no AI/provider dependency anywhere in this validation layer.
def test_validation_layer_source_has_zero_openai_dependencies():
    import backend.adaptive_management.analytics as analytics_module
    import backend.adaptive_management.event_capture as event_capture_module
    import backend.adaptive_management.outcome_resolver as outcome_resolver_module

    for module in (event_capture_module, outcome_resolver_module, analytics_module):
        source = inspect.getsource(module)
        assert "openai" not in source.lower()
        assert "provider_registry" not in source
        assert "ai_trading_config" not in source


# 17. Live trading remains blocked.
def test_live_trading_remains_blocked():
    assert mt5_config().live_trading_enabled is False
