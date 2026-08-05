from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.adaptive_management import service
from backend.adaptive_management.orm import (
    AdaptiveActivationORM,
    AdaptiveBrokerActionResultORM,
    AdaptiveCircuitBreakerORM,
    AdaptiveExperimentORM,
    AdaptiveManagementActionORM,
    AdaptivePartialExitStageORM,
    AdaptivePositionStateORM,
    AdaptiveSessionORM,
    AdaptiveTradeEventORM,
    ChampionChallengerResultORM,
    CounterfactualOutcomeORM,
    ShadowDecisionORM,
    TradeManagementPolicyORM,
    TradePathSnapshotORM,
    TradeThesisORM,
)
from backend.adaptive_management.service import adaptive_management_service
from backend.shared.db import Base


def _managed_state(position_id: str) -> AdaptivePositionStateORM:
    state = AdaptivePositionStateORM(position_id=position_id)
    state.symbol = "XAUUSD"
    state.direction = "LONG"
    state.broker_ticket = position_id
    state.current_volume = 1.0
    state.original_volume = 1.0
    state.entry_price = 4000.0
    state.current_sl = 3980.0
    state.original_sl = 3980.0
    state.current_tp = 4100.0
    state.original_tp = 4100.0
    state.max_achieved_r = 4.0
    state.min_achieved_r = 0.0
    return state


class _FakeAccount:
    login = 123456
    server = "MetaQuotes-Demo"

    def model_dump(self, mode="json"):
        return {"login": self.login, "server": self.server, "currency": "USD"}


class _FakeTerminal:
    account_mode = "DEMO"


class _FakeSymbol:
    volume_step = "0.01"
    volume_min = "0.01"
    volume_max = "100"
    digits = 5
    filling_mode = 2


class _FakeQuote:
    bid = 1.1010
    ask = 1.1012


class _FakePosition:
    def model_dump(self, mode="json"):
        return {
            "ticket": 555,
            "identifier": 555,
            "symbol": "EURUSD",
            "type": 0,
            "volume": 1.23,
            "price_open": 1.1000,
            "price_current": 1.1010,
            "sl": 1.0990,
            "tp": 1.1030,
            "profit": 100,
            "magic": 5601001,
            "comment": "BENSIM_AUTO strategy=BENSIM_AUTO timeframe=M5 setup=TEST",
            "time": "2026-08-04T08:00:00+00:00",
        }


class _FakeMT5:
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009

    def __init__(self):
        self.order_send_calls = 0

    def order_send(self, request):
        self.order_send_calls += 1

        class Result:
            def _asdict(self):
                return {"retcode": 10009, "order": 777, "deal": 888, "price": request.get("price"), "volume": request.get("volume")}

        return Result()


class _FakeClient:
    def __init__(self, mt5):
        self.mt5 = mt5

    def ensure_ready(self):
        return self.mt5


class _FakeAdapter:
    def __init__(self):
        self.mt5 = _FakeMT5()
        self.client = _FakeClient(self.mt5)

    async def mt5_account(self):
        return _FakeAccount()

    async def terminal_status(self):
        return _FakeTerminal()

    async def mt5_positions(self):
        return [_FakePosition()]

    async def symbol_info(self, symbol):
        return _FakeSymbol()

    async def latest_tick(self, symbol):
        return _FakeQuote()

    async def candles(self, symbol, timeframe, count=100):
        start = datetime(2026, 8, 4, 8, 0, tzinfo=timezone.utc)
        return [_FakeCandle(row | {"symbol": symbol, "timeframe": timeframe}) for row in _candles(start)[-20:]]


class _FakeExecutionManager:
    def __init__(self):
        self.calls = 0

    async def submit_mt5_request(self, **kwargs):
        self.calls += 1
        request = kwargs["request"]
        return {"retcode": 10009, "order": 777, "deal": 888, "price": request.get("price"), "volume": request.get("volume"), "comment": "Request executed"}


class _FakeCandle:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, mode="json"):
        return self.payload


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(service, "SessionLocal", SessionLocal)
    return SessionLocal


def _candles(start: datetime) -> list[dict]:
    prices = [
        (1.3000, 1.3005, 1.2998, 1.3004),
        (1.3004, 1.3010, 1.3002, 1.3009),
        (1.3009, 1.3015, 1.3007, 1.3014),
        (1.3014, 1.3012, 1.3004, 1.3006),
        (1.3006, 1.3007, 1.2997, 1.2999),
        (1.2999, 1.3000, 1.2990, 1.2992),
        (1.2992, 1.2994, 1.2988, 1.2990),
        (1.2990, 1.2993, 1.2987, 1.2991),
        (1.2991, 1.2992, 1.2985, 1.2986),
        (1.2986, 1.2988, 1.2982, 1.2983),
        (1.2983, 1.2989, 1.2981, 1.2987),
        (1.2987, 1.2990, 1.2984, 1.2988),
        (1.2988, 1.2991, 1.2985, 1.2989),
    ]
    return [
        {"time": (start + timedelta(minutes=5 * idx)).isoformat(), "open": o, "high": h, "low": low, "close": c, "spread": 10 + idx}
        for idx, (o, h, low, c) in enumerate(prices)
    ]


def _trade(start: datetime) -> dict:
    return {
        "trade_id": "TRADE_GBPUSD_1",
        "session_id": "SESSION_TEST",
        "symbol": "GBPUSD",
        "direction": "LONG",
        "volume": 5.0,
        "entry": 1.3000,
        "stop_loss": 1.2990,
        "take_profit": 1.3020,
        "entry_time": start.isoformat(),
        "exit_time": (start + timedelta(minutes=60)).isoformat(),
        "actual_pnl": -250.0,
        "commission": -2.0,
        "strategy_id": "BENSIM_AUTO",
        "strategy_version": "UNKNOWN",
        "setup_id": "UNKNOWN",
        "timeframe": "M5",
    }


def test_mt5_history_normalization_and_thesis_grouping(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    payload = {
        "session_id": "SESSION_TEST",
        "account_id": "123",
        "account_mode": "DEMO",
        "server": "MetaQuotes-Demo",
        "deals": [
            {"ticket": 1, "order": 10, "position_id": 100, "symbol": "GBPUSD", "type": 1, "volume": 4.0, "price": 1.3420, "profit": -240, "commission": -2, "swap": 0, "time": "2026-08-04T08:00:00+00:00", "comment": "BENSIM_AUTO"},
            {"ticket": 2, "order": 11, "position_id": 101, "symbol": "GBPUSD", "type": 1, "volume": 3.0, "price": 1.3418, "profit": -235, "commission": -2, "swap": 0, "time": "2026-08-04T08:10:00+00:00", "comment": "BENSIM_AUTO"},
            {"ticket": 3, "order": 12, "position_id": 102, "symbol": "GBPUSD", "type": 1, "volume": 5.0, "price": 1.3417, "profit": 426, "commission": -2, "swap": 0, "time": "2026-08-04T08:20:00+00:00", "comment": "BENSIM_AUTO"},
        ],
        "orders": [{"ticket": 12, "symbol": "GBPUSD", "type": 1, "volume": 5.0, "price": 1.3417, "time": "2026-08-04T08:19:00+00:00"}],
        "effective_config": {"max_risk_percent": "unchanged", "lot_size": "observed"},
    }

    result = adaptive_management_service.import_session(payload)

    with SessionLocal() as db:
        session = db.get(AdaptiveSessionORM, "SESSION_TEST")
        theses = db.query(TradeThesisORM).all()
        events = db.query(AdaptiveTradeEventORM).all()

    assert result["imported_deals"] == 3
    assert session.realized_pnl == 426 - 240 - 235 - 6
    assert len(events) == 4
    assert len(theses) == 1
    assert theses[0].trades_per_thesis == 3
    assert theses[0].relationship_summary["repeated_entries"] == 2
    assert theses[0].additional_entry_contribution > 0


def test_mfe_mae_regime_and_completed_candle_timeline(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    start = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)

    path = adaptive_management_service.reconstruct_trade_path(_trade(start), _candles(start))

    with SessionLocal() as db:
        stored = db.get(TradePathSnapshotORM, path["path_id"])

    assert stored is not None
    assert path["mfe"] >= 1.4
    assert path["mae"] <= -1.0
    assert path["candle_count_held"] == 13
    assert all(row["available_data_only"] for row in path["timeline"])
    assert path["market_regime_entry"] in {"insufficient_data", "ranging", "trending_up", "breakout"}


def test_shadow_policies_counterfactuals_and_no_execution(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    start = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)

    result = adaptive_management_service.replay(_trade(start), _candles(start))

    with SessionLocal() as db:
        policies = db.query(TradeManagementPolicyORM).count()
        decisions = db.query(ShadowDecisionORM).all()
        outcomes = db.query(CounterfactualOutcomeORM).all()

    assert result["broker_mutation_calls"] == 0
    assert policies >= 14
    assert len(outcomes) >= 14
    assert all(decision.would_mutate_broker is False for decision in decisions)
    assert {row.policy_id for row in outcomes} >= {
        "static_baseline_v1",
        "partial_25_at_0_5r_v1",
        "breakeven_at_0_75r_v1",
        "tp_progress_partial_v1",
        "tp_progress_full_protect_95_v1",
        "structure_stop_protection_v1",
        "improved_breakeven_structure_v1",
        "atr_structure_stop_wider_v1",
        "atr_structure_stop_tighter_v1",
    }
    assert any(row.loss_reduced or row.avoided_loss for row in outcomes)


def test_policy_evaluation_minimums_walk_forward_and_champion_challenger(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    start = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)
    for idx in range(12):
        trade = _trade(start)
        trade["trade_id"] = f"TRADE_GBPUSD_{idx}"
        trade["entry_time"] = (start + timedelta(hours=idx)).isoformat()
        trade["exit_time"] = (start + timedelta(hours=idx, minutes=60)).isoformat()
        adaptive_management_service.replay(trade, _candles(start + timedelta(hours=idx)))

    evaluation = adaptive_management_service.evaluate_policies()
    walk_forward = adaptive_management_service.run_walk_forward()

    with SessionLocal() as db:
        experiments = db.query(AdaptiveExperimentORM).all()
        cc = db.query(ChampionChallengerResultORM).all()

    assert evaluation["promotion_allowed"] is False
    assert evaluation["manual_approval_required"] is True
    assert walk_forward["chronological"] is True
    assert walk_forward["manual_approval_required"] is True
    assert experiments[0].immutable is True
    assert experiments[0].promotion_requires_manual_approval is True
    assert cc
    assert all(row.manual_approval_required for row in cc)


def test_drift_detection_and_no_current_sizing_change(monkeypatch):
    _session_factory(monkeypatch)
    status = adaptive_management_service.status()
    drift = adaptive_management_service.detect_drift()

    assert status["broker_mutation_allowed"] is False
    assert status["risk_config_observed_only"]["strategy_version"] == "active_current_unchanged"
    assert status["risk_config_observed_only"]["sizing_version"] == "active_current_unchanged"
    assert drift["state"] == "insufficient_data"
    assert drift["broker_mutation_calls"] == 0


def test_adaptive_manager_shadow_mode_does_not_send_orders(monkeypatch):
    _session_factory(monkeypatch)
    fake = _FakeAdapter()
    monkeypatch.setattr(service, "mt5_adapter", fake)
    monkeypatch.setenv("ADAPTIVE_TRADE_MANAGEMENT_MODE", "shadow")

    result = asyncio.run(adaptive_management_service.evaluate_now())

    with service.SessionLocal() as db:
        actions = db.query(AdaptiveManagementActionORM).all()
    assert result["broker_mutation_calls"] == 0
    assert fake.mt5.order_send_calls == 0
    assert actions
    assert all(action.status.startswith("shadow") or action.status == "hold" for action in actions)


def test_adaptive_demo_activation_requires_explicit_mode(monkeypatch):
    _session_factory(monkeypatch)
    monkeypatch.setenv("ADAPTIVE_TRADE_MANAGEMENT_MODE", "shadow")

    result = asyncio.run(adaptive_management_service.activate_demo(eligible_symbols=["EURUSD"]))

    assert result["status"] == "REJECTED"
    assert result["reason"] == "ADAPTIVE_TRADE_MANAGEMENT_MODE_MUST_BE_demo_active"


def test_adaptive_demo_active_requires_new_or_adopted_position(monkeypatch):
    _session_factory(monkeypatch)
    fake = _FakeAdapter()
    monkeypatch.setattr(service, "mt5_adapter", fake)
    monkeypatch.setenv("ADAPTIVE_TRADE_MANAGEMENT_MODE", "demo_active")
    monkeypatch.delenv("ADAPTIVE_MANAGEMENT_APPLY_TO_EXISTING_POSITIONS", raising=False)

    activation = asyncio.run(adaptive_management_service.activate_demo(eligible_symbols=["EURUSD"], effective_from=datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)))
    result = asyncio.run(adaptive_management_service.evaluate_now())

    with service.SessionLocal() as db:
        state = db.get(AdaptivePositionStateORM, "555")
        actions = db.query(AdaptiveManagementActionORM).all()
    assert activation["status"] == "ACTIVE"
    assert state.managed_automatically is False
    assert result["broker_mutation_calls"] == 0
    assert fake.mt5.order_send_calls == 0
    assert any(action.status == "blocked_not_adopted" for action in actions)


def test_adopted_position_can_execute_conservative_demo_action(monkeypatch):
    _session_factory(monkeypatch)
    fake = _FakeAdapter()
    execution = _FakeExecutionManager()
    monkeypatch.setattr(service, "mt5_adapter", fake)
    monkeypatch.setattr(service, "execution_manager", execution)
    monkeypatch.setenv("ADAPTIVE_TRADE_MANAGEMENT_MODE", "demo_active")
    monkeypatch.setenv("ADAPTIVE_PARTIAL_PROFIT_R", "0.1")

    asyncio.run(adaptive_management_service.activate_demo(eligible_symbols=["EURUSD"], effective_from=datetime(2026, 8, 4, 7, 0, tzinfo=timezone.utc)))
    result = asyncio.run(adaptive_management_service.evaluate_now())

    with service.SessionLocal() as db:
        rows = db.query(AdaptiveBrokerActionResultORM).all()
    assert result["broker_mutation_calls"] == 1
    assert execution.calls == 1
    assert rows[0].status == "ACCEPTED"


def test_circuit_breaker_blocks_demo_execution(monkeypatch):
    _session_factory(monkeypatch)
    fake = _FakeAdapter()
    monkeypatch.setattr(service, "mt5_adapter", fake)
    monkeypatch.setenv("ADAPTIVE_TRADE_MANAGEMENT_MODE", "demo_active")
    monkeypatch.setenv("ADAPTIVE_PARTIAL_PROFIT_R", "0.1")
    asyncio.run(adaptive_management_service.activate_demo(eligible_symbols=["EURUSD"], effective_from=datetime(2026, 8, 4, 7, 0, tzinfo=timezone.utc)))
    with service.SessionLocal() as db:
        breaker = db.get(AdaptiveCircuitBreakerORM, "adaptive_demo_manager")
        breaker.state = "open"
        db.merge(breaker)
        db.commit()

    result = asyncio.run(adaptive_management_service.evaluate_now())

    assert result["broker_mutation_calls"] == 0
    assert fake.mt5.order_send_calls == 0


def test_volume_normalization_uses_broker_step_and_minimum():
    assert service._normalize_volume(0.257, _FakeSymbol()) == 0.25
    assert service._normalize_volume(0.001, _FakeSymbol()) == 0.0


def test_attempted_rejected_action_is_not_retried(monkeypatch):
    _session_factory(monkeypatch)
    monkeypatch.setenv("ADAPTIVE_TRADE_MANAGEMENT_MODE", "demo_active")
    with service.SessionLocal() as db:
        activation = AdaptiveActivationORM(activation_id="ACT")
        activation.policy_id = "conservative_demo_manager_v1"
        activation.policy_version = "v1"
        activation.mode = "demo_active"
        activation.demo_account = "123"
        activation.effective_from = datetime(2026, 8, 4, 7, 0, tzinfo=timezone.utc)
        activation.approved_by = "test"
        activation.approved_at = activation.effective_from
        activation.eligible_symbols = ["EURUSD"]
        activation.eligible_strategies = ["BENSIM_AUTO"]
        activation.maximum_actions_per_hour = 6
        activation.rollback_policy = {}
        activation.emergency_state = "normal"
        activation.configuration_snapshot = {}
        activation.active = True
        state = AdaptivePositionStateORM(position_id="POS1")
        state.symbol = "EURUSD"
        state.direction = "LONG"
        state.broker_ticket = "POS1"
        state.current_volume = 1
        state.entry_price = 1.1
        state.current_sl = 1.099
        state.current_tp = 1.103
        state.managed_automatically = True
        action = AdaptiveManagementActionORM(action_id="A1")
        action.activation_id = "ACT"
        action.position_id = "POS1"
        action.policy_id = "conservative_demo_manager_v1"
        action.action_type = "PARTIAL_PROFIT"
        action.priority = 40
        action.mode = "demo_active"
        action.status = "rejected"
        action.idempotency_key = "KEY1"
        action.selected = True
        action.considered_actions = []
        action.evidence = {}
        action.broker_mutation_attempted = True
        db.merge(activation)
        db.merge(state)
        db.merge(action)
        breaker = service._breaker(db)
        db.commit()

        assert adaptive_management_service._can_execute(action, state, activation, breaker, "demo_active") is False


def test_mfe_giveback_triggers_from_half_r(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.delenv("ADAPTIVE_MFE_MIN_R", raising=False)
    state = AdaptivePositionStateORM(position_id="XAU1")
    state.symbol = "XAUUSD"
    state.direction = "LONG"
    state.current_volume = 1
    state.entry_price = 4063.51
    state.current_sl = 4058.13
    state.original_sl = 4058.13
    state.current_tp = 4073.14
    state.max_achieved_r = 0.55
    payload = {"price_current": 4064.83}

    with SessionLocal() as db:
        actions = adaptive_management_service._evaluate_position(db, state, payload, {}, [])

    assert any(action.action_type == "MFE_PROTECTION_CLOSE" for action in actions)


def test_tp_progress_and_max_tp_progress_persist_across_cycles(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    payload_early = {"ticket": 900, "identifier": 900, "symbol": "EURUSD", "type": 0, "volume": 1.0, "price_open": 1.1000, "price_current": 1.1020, "sl": 1.0980, "tp": 1.1100, "time": "2026-08-04T08:00:00+00:00", "comment": "BENSIM_AUTO"}
    payload_later = dict(payload_early, price_current=1.1085)

    with SessionLocal() as db:
        state = adaptive_management_service._sync_position_state(db, payload_early, None, {}, [])
        db.commit()
        first_progress = state.tp_progress
        first_max = state.max_tp_progress

        state = adaptive_management_service._sync_position_state(db, payload_later, None, {}, [])
        db.commit()
        second_progress = state.tp_progress
        second_max = state.max_tp_progress

        state = adaptive_management_service._sync_position_state(db, payload_early, None, {}, [])
        db.commit()
        third_progress = state.tp_progress
        third_max = state.max_tp_progress

    assert first_progress == pytest.approx(0.2)
    assert first_max == pytest.approx(0.2)
    assert second_progress == pytest.approx(0.85)
    assert second_max == pytest.approx(0.85)
    assert third_progress == pytest.approx(0.2)
    assert third_max == pytest.approx(0.85)


def test_profit_lock_floor_triggers_protection_after_deep_giveback(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("XAU_FLOOR")
    state.max_tp_progress = 0.85
    state.tp_progress = 0.20
    state.winner_classification = "weakening"
    payload = {"price_current": 4000.0}

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, [])

    action_types = {candidate.action_type for candidate in candidates}
    assert "TP_PROGRESS_PROFIT_LOCK" in action_types
    lock_candidate = next(candidate for candidate in candidates if candidate.action_type == "TP_PROGRESS_PROFIT_LOCK")
    assert lock_candidate.requested_volume is not None and lock_candidate.requested_volume > 0
    assert lock_candidate.requested_volume <= state.current_volume


def test_strong_continuation_prefers_hold_or_structure_stop_not_full_close(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("XAU_STRONG")
    state.max_tp_progress = 0.80
    state.tp_progress = 0.80
    state.winner_classification = "strong_continuation"
    payload = {"price_current": 4075.0}

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, [])
        selected = adaptive_management_service._select_action(candidates)

    assert selected.action_type != "TP_PROGRESS_PARTIAL_PROTECT"
    full_close_candidates = [c for c in candidates if c.action_type == "TP_PROGRESS_PROFIT_LOCK" and c.requested_volume == state.current_volume]
    assert not full_close_candidates


def test_healthy_pullback_does_not_trigger_partial_protect(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("XAU_HEALTHY")
    state.max_tp_progress = 0.78
    state.tp_progress = 0.78
    state.winner_classification = "healthy_pullback"
    payload = {"price_current": 4070.0}

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, [])

    assert not any(candidate.action_type == "TP_PROGRESS_PARTIAL_PROTECT" for candidate in candidates)


def test_weakening_triggers_partial_protect_stage(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("XAU_WEAK")
    state.max_tp_progress = 0.78
    state.tp_progress = 0.78
    state.winner_classification = "weakening"
    payload = {"price_current": 4070.0}

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, [])

    protect = [c for c in candidates if c.action_type == "TP_PROGRESS_PARTIAL_PROTECT"]
    assert protect
    assert protect[0].evidence["stage"] == "stage_1"


def test_partial_protect_stage_does_not_repeat_once_executed(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("XAU_STAGE")
    state.max_tp_progress = 0.78
    state.tp_progress = 0.78
    state.winner_classification = "weakening"
    payload = {"price_current": 4070.0}

    with SessionLocal() as db:
        stage_row = AdaptivePartialExitStageORM(stage_id="APES_TEST")
        stage_row.position_id = state.position_id
        stage_row.stage = "stage_1"
        stage_row.trigger_tp_progress = 0.78
        stage_row.requested_fraction = 0.25
        db.add(stage_row)
        db.commit()

        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, [])

    assert not any(candidate.action_type == "TP_PROGRESS_PARTIAL_PROTECT" for candidate in candidates)


def test_modification_cooldown_suppresses_repeat_stop_modify(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("XAU_COOLDOWN")
    state.max_tp_progress = 0.35
    state.tp_progress = 0.35
    state.winner_classification = "healthy_pullback"
    state.last_management_at = datetime.now(timezone.utc)
    payload = {"price_current": 4020.0}

    with SessionLocal() as db:
        candidates_cooling = adaptive_management_service._evaluate_position(db, state, payload, {}, [])

    state.last_management_at = datetime.now(timezone.utc) - timedelta(hours=1)
    with SessionLocal() as db:
        candidates_ready = adaptive_management_service._evaluate_position(db, state, payload, {}, [])

    cooling_types = {c.action_type for c in candidates_cooling}
    ready_types = {c.action_type for c in candidates_ready}
    assert "MOVE_SL_BREAKEVEN" not in cooling_types
    assert "MOVE_SL_BREAKEVEN" in ready_types


def test_lineage_parses_bsm_comment_format():
    comment = "BSM|MTFAI1|M15|EURUSD1755"

    assert service._lineage(comment, "strategy") == "MTFAI1"
    assert service._lineage(comment, "timeframe") == "M15"
    assert service._lineage(comment, "setup") == "MT5_AUTONOMOUS_ENTRY"
    assert service._lineage(comment, "strategy_version") == "v1"


def test_lineage_still_handles_legacy_and_missing_comments():
    assert service._lineage(None, "strategy") == "UNKNOWN"
    assert service._lineage("some manual note", "strategy") == "UNKNOWN"
    assert service._lineage("BENSIM_AUTO legacy order", "strategy") == "BENSIM_AUTO"
    assert service._lineage("strategy=EMATrend timeframe=M5", "strategy") == "EMATrend"


def test_bsm_comment_truncates_to_mt5_limit():
    comment = service._bsm_comment("BENSIM_AUTO", "UNKNOWN", "EXIT")
    assert comment.startswith("BSM|")
    assert len(comment) <= 31

    overflow_comment = service._bsm_comment("A" * 20, "B" * 20, "VERYLONGTAGNAME")
    assert len(overflow_comment) <= 31

    default_comment = service._bsm_comment(None, None, "SLTP")
    assert default_comment == "BSM|UNK|NA|SLTP"


def test_sync_position_state_captures_strategy_and_timeframe_once(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    payload = {"ticket": 901, "identifier": 901, "symbol": "EURUSD", "type": 0, "volume": 1.0, "price_open": 1.1000, "price_current": 1.1010, "sl": 1.0980, "tp": 1.1100, "time": "2026-08-04T08:00:00+00:00", "comment": "BSM|MTFAI1|M15|EURUSD0800"}
    payload_no_lineage = dict(payload, comment="BSM|OTHERSTRAT|H1|EURUSD0900")

    with SessionLocal() as db:
        state = adaptive_management_service._sync_position_state(db, payload, None, {}, [])
        db.commit()
        first_strategy, first_timeframe = state.strategy_id, state.timeframe

        state = adaptive_management_service._sync_position_state(db, payload_no_lineage, None, {}, [])
        db.commit()
        second_strategy, second_timeframe = state.strategy_id, state.timeframe

    assert first_strategy == "MTFAI1"
    assert first_timeframe == "M15"
    assert second_strategy == "MTFAI1"
    assert second_timeframe == "M15"


def test_exit_and_sltp_actions_carry_captured_lineage_in_comment():
    state = _managed_state("XAU_LINEAGE")
    state.strategy_id = "MTFAI1"
    state.timeframe = "M15"
    payload = {"ticket": 777, "identifier": 777}
    mt5, symbol, quote = _FakeMT5(), _FakeSymbol(), _FakeQuote()

    exit_action = AdaptiveManagementActionORM(action_id="A_EXIT")
    exit_action.action_type = "PARTIAL_PROFIT"
    exit_action.requested_volume = 0.5

    sltp_action = AdaptiveManagementActionORM(action_id="A_SLTP")
    sltp_action.action_type = "MOVE_SL_BREAKEVEN"
    sltp_action.requested_sl = 4000.5
    sltp_action.requested_tp = 4100.0

    exit_request = asyncio.run(adaptive_management_service._build_mt5_request(mt5, symbol, quote, exit_action, state, payload))
    sltp_request = asyncio.run(adaptive_management_service._build_mt5_request(mt5, symbol, quote, sltp_action, state, payload))

    assert exit_request["comment"] == "BSM|MTFAI1|M15|EXIT"
    assert sltp_request["comment"] == "BSM|MTFAI1|M15|SLTP"


def test_reconcile_recently_closed_marks_and_triggers_import_once(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    adaptive_management_service._last_reconciliation_at = None
    calls = []

    async def _fake_import(*, days=7, session_id=None):
        calls.append(days)
        return {"status": "ok"}

    monkeypatch.setattr(adaptive_management_service, "import_mt5_session", _fake_import)

    with SessionLocal() as db:
        first = AdaptivePositionStateORM(position_id="CLOSED_1")
        first.symbol = "EURUSD"
        first.direction = "LONG"
        first.broker_ticket = "CLOSED_1"
        first.opened_at = datetime.now(timezone.utc) - timedelta(hours=2)
        second = AdaptivePositionStateORM(position_id="CLOSED_2")
        second.symbol = "GBPUSD"
        second.direction = "SHORT"
        second.broker_ticket = "CLOSED_2"
        second.opened_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db.add(first)
        db.add(second)
        db.commit()

        asyncio.run(adaptive_management_service._reconcile_recently_closed(db, {"CLOSED_2"}))
        refreshed_first = db.get(AdaptivePositionStateORM, "CLOSED_1")
        refreshed_second = db.get(AdaptivePositionStateORM, "CLOSED_2")
        assert refreshed_first.closed_detected_at is not None
        assert refreshed_second.closed_detected_at is None
        assert calls == [3]

        asyncio.run(adaptive_management_service._reconcile_recently_closed(db, set()))
        refreshed_second = db.get(AdaptivePositionStateORM, "CLOSED_2")
        assert refreshed_second.closed_detected_at is not None
        assert calls == [3]


def test_price_matches_tolerates_broker_rounding_but_catches_real_drift():
    assert service._price_matches(1.10000, 1.10000) is True
    assert service._price_matches(1.10000, 1.10001) is True
    assert service._price_matches(1.10000, 1.10500) is False
    assert service._price_matches(None, None) is True
    assert service._price_matches(1.10000, None) is False
    assert service._price_matches(None, 1.10000) is False


def _pending_sltp_action_and_result(position_id: str, *, action_type: str, requested_sl: float, requested_tp: float) -> tuple[AdaptiveManagementActionORM, AdaptiveBrokerActionResultORM]:
    action = AdaptiveManagementActionORM(action_id=f"ACT_{position_id}_{action_type}")
    action.position_id = position_id
    action.policy_id = "conservative_demo_manager_v1"
    action.action_type = action_type
    action.priority = 50
    action.mode = "demo_active"
    action.status = "submitted"
    action.idempotency_key = f"KEY_{position_id}_{action_type}"
    action.requested_sl = requested_sl
    action.requested_tp = requested_tp
    action.selected = True
    action.considered_actions = []
    action.evidence = {}
    action.broker_mutation_attempted = True

    result = AdaptiveBrokerActionResultORM(result_id=f"RES_{position_id}_{action_type}")
    result.action_id = action.action_id
    result.status = "ACCEPTED"
    result.reconciliation_state = "pending"
    result.raw_request = {}
    result.raw_response = {}
    return action, result


def test_sltp_reconciliation_confirms_matching_broker_state(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    action, result = _pending_sltp_action_and_result("POS_CONFIRM", action_type="MOVE_SL_BREAKEVEN", requested_sl=4000.50, requested_tp=4100.00)

    with SessionLocal() as db:
        db.add(action)
        db.add(result)
        db.commit()

        adaptive_management_service._reconcile_sltp_confirmations(db, "POS_CONFIRM", 4000.50, 4100.00)
        db.commit()

        refreshed = db.get(AdaptiveBrokerActionResultORM, result.result_id)
        assert refreshed.confirmed_sl == 4000.50
        assert refreshed.confirmed_tp == 4100.00
        assert refreshed.reconciliation_state == "confirmed"


def test_sltp_reconciliation_flags_mismatch_from_broker_drift(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    action, result = _pending_sltp_action_and_result("POS_MISMATCH", action_type="TRAIL_STOP", requested_sl=4000.50, requested_tp=4100.00)

    with SessionLocal() as db:
        db.add(action)
        db.add(result)
        db.commit()

        adaptive_management_service._reconcile_sltp_confirmations(db, "POS_MISMATCH", 3990.00, 4100.00)
        db.commit()

        refreshed = db.get(AdaptiveBrokerActionResultORM, result.result_id)
        assert refreshed.confirmed_sl == 3990.00
        assert refreshed.reconciliation_state == "mismatch"


def test_sltp_reconciliation_only_touches_pending_rows_for_that_position(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    action, result = _pending_sltp_action_and_result("POS_OTHER", action_type="MOVE_SL_BREAKEVEN", requested_sl=1.0980, requested_tp=1.1100)

    with SessionLocal() as db:
        db.add(action)
        db.add(result)
        db.commit()

        adaptive_management_service._reconcile_sltp_confirmations(db, "SOME_OTHER_POSITION", 1.0980, 1.1100)
        db.commit()

        refreshed = db.get(AdaptiveBrokerActionResultORM, result.result_id)
        assert refreshed.reconciliation_state == "pending"
        assert refreshed.confirmed_sl is None


def test_auto_recover_breaker_closes_after_broker_not_ready(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        breaker = service._breaker(db)
        breaker.state = "open"
        breaker.reason = "BROKER_NOT_READY:MT5UnavailableError"
        breaker.failed_actions = 3
        breaker.rejected_actions = 2
        db.merge(breaker)
        db.commit()

        adaptive_management_service._auto_recover_breaker(db, breaker)

        refreshed = db.get(AdaptiveCircuitBreakerORM, "adaptive_demo_manager")
        assert refreshed.state == "closed"
        assert refreshed.failed_actions == 0
        assert refreshed.rejected_actions == 0
        assert refreshed.reason == "auto_recovered_broker_reachable"


def test_auto_recover_breaker_leaves_execution_failures_open(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        breaker = service._breaker(db)
        breaker.state = "open"
        breaker.reason = "MT5_REJECTED:10019"
        db.merge(breaker)
        db.commit()

        adaptive_management_service._auto_recover_breaker(db, breaker)

        refreshed = db.get(AdaptiveCircuitBreakerORM, "adaptive_demo_manager")
        assert refreshed.state == "open"
        assert refreshed.reason == "MT5_REJECTED:10019"


def test_auto_recover_breaker_is_noop_when_already_closed(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        breaker = service._breaker(db)
        assert breaker.state == "closed"

        adaptive_management_service._auto_recover_breaker(db, breaker)

        refreshed = db.get(AdaptiveCircuitBreakerORM, "adaptive_demo_manager")
        assert refreshed.state == "closed"


def test_circuit_breaker_auto_recovers_within_next_successful_cycle(monkeypatch):
    _session_factory(monkeypatch)
    fake = _FakeAdapter()
    execution = _FakeExecutionManager()
    monkeypatch.setattr(service, "mt5_adapter", fake)
    monkeypatch.setattr(service, "execution_manager", execution)
    monkeypatch.setenv("ADAPTIVE_TRADE_MANAGEMENT_MODE", "demo_active")
    monkeypatch.setenv("ADAPTIVE_PARTIAL_PROFIT_R", "0.1")
    asyncio.run(adaptive_management_service.activate_demo(eligible_symbols=["EURUSD"], effective_from=datetime(2026, 8, 4, 7, 0, tzinfo=timezone.utc)))
    with service.SessionLocal() as db:
        breaker = db.get(AdaptiveCircuitBreakerORM, "adaptive_demo_manager")
        breaker.state = "open"
        breaker.reason = "BROKER_NOT_READY:MT5UnavailableError"
        db.merge(breaker)
        db.commit()

    result = asyncio.run(adaptive_management_service.evaluate_now())

    with service.SessionLocal() as db:
        breaker = db.get(AdaptiveCircuitBreakerORM, "adaptive_demo_manager")
    assert breaker.state == "closed"
    assert result["broker_mutation_calls"] == 1
