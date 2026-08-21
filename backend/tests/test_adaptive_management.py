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
    AdaptivePartialProfitStageORM,
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
from backend.brokers.mt5 import account_registry
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
    company = "MetaQuotes"
    currency = "USD"

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
    monkeypatch.setattr(account_registry, "SessionLocal", SessionLocal)
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


def test_non_demo_10k_deal_position_id_is_scoped_to_match_the_position_state_row(monkeypatch):
    """BUG FIX regression (traced live -- caused a real incident: 78.8% of the ATM auto-replay
    backlog was permanently stuck because AdaptiveTradeEventORM.position_id was NEVER account-
    scoped, while AdaptivePositionStateORM.position_id IS scoped for every non-demo_10k account
    (service.py::_position_id). _auto_replay_recently_closed's deal lookup does a direct
    `AdaptiveTradeEventORM.position_id == row.position_id` match, which could never succeed for
    ftmo_demo_25k/50k/100k -- not missing data, a permanent format mismatch. demo_10k's own
    format (no prefix) must stay exactly as before -- every existing row already uses it."""
    _session_factory(monkeypatch)
    svc = service.AdaptiveManagementService(account_id="ftmo_demo_25k")
    payload = {
        "session_id": "SESSION_SCOPED_TEST",
        "account_id": "999",
        "account_mode": "DEMO",
        "deals": [{"ticket": 1, "order": 10, "position_id": 555, "symbol": "EURUSD", "type": 0, "volume": 1.0, "price": 1.1, "profit": 10, "commission": 0, "swap": 0, "time": "2026-08-04T08:00:00+00:00", "comment": "BENSIM_AUTO"}],
        "orders": [],
        "effective_config": {},
    }

    svc.import_session(payload)

    with service.SessionLocal() as db:
        event = db.query(AdaptiveTradeEventORM).filter(AdaptiveTradeEventORM.event_type == "DEAL").one()
    assert event.position_id == "ftmo_demo_25k:555"  # scoped exactly like AdaptivePositionStateORM's own position_id


def test_demo_10k_deal_position_id_stays_unprefixed(monkeypatch):
    """demo_10k must be completely unaffected by the scoping fix -- every existing row in the
    real database already uses the bare, unprefixed ticket format."""
    _session_factory(monkeypatch)
    svc = service.AdaptiveManagementService()  # defaults to account_id="demo_10k"
    payload = {
        "session_id": "SESSION_DEMO10K_TEST",
        "account_id": "123",
        "account_mode": "DEMO",
        "deals": [{"ticket": 2, "order": 11, "position_id": 556, "symbol": "EURUSD", "type": 0, "volume": 1.0, "price": 1.1, "profit": 10, "commission": 0, "swap": 0, "time": "2026-08-04T08:00:00+00:00", "comment": "BENSIM_AUTO"}],
        "orders": [],
        "effective_config": {},
    }

    svc.import_session(payload)

    with service.SessionLocal() as db:
        event = db.query(AdaptiveTradeEventORM).filter(AdaptiveTradeEventORM.event_type == "DEAL").one()
    assert event.position_id == "556"


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


def test_position_opened_event_fires_once_on_first_reconciliation_not_again(monkeypatch):
    """mt5.position.opened must publish exactly once -- the cycle a position is first seen and
    reconciled into AdaptivePositionStateORM (existing_row is None) -- and never again on later
    cycles for the same still-open ticket."""
    _session_factory(monkeypatch)
    fake = _FakeAdapter()
    monkeypatch.setattr(service, "mt5_adapter", fake)
    monkeypatch.setenv("ADAPTIVE_TRADE_MANAGEMENT_MODE", "shadow")
    published = []

    async def _fake_publish(topic, payload):
        published.append((topic, payload))

    monkeypatch.setattr(service.redis_layer, "publish_event", _fake_publish)

    asyncio.run(adaptive_management_service.evaluate_now())  # first cycle -- position is new
    opened_events_first_cycle = [p for p in published if p[0] == "mt5.position.opened"]
    published.clear()
    asyncio.run(adaptive_management_service.evaluate_now())  # second cycle -- same position, already tracked
    opened_events_second_cycle = [p for p in published if p[0] == "mt5.position.opened"]

    assert len(opened_events_first_cycle) == 1
    assert opened_events_first_cycle[0][1]["ticket"] == "555"
    assert opened_events_first_cycle[0][1]["symbol"] == "EURUSD"
    assert opened_events_second_cycle == []  # not republished on a later cycle for the same ticket


def test_adaptive_demo_activation_requires_explicit_mode(monkeypatch):
    _session_factory(monkeypatch)
    monkeypatch.setenv("ADAPTIVE_TRADE_MANAGEMENT_MODE", "shadow")

    result = asyncio.run(adaptive_management_service.activate_demo(eligible_symbols=["EURUSD"]))

    assert result["status"] == "REJECTED"
    assert result["reason"] == "ADAPTIVE_TRADE_MANAGEMENT_MODE_MUST_BE_demo_active"


def test_activate_demo_rejects_account_classified_as_prop_evaluation(monkeypatch):
    # A demo-mode MT5 account (terminal/cfg both report "DEMO") that a human has classified as
    # a prop-firm evaluation account must never activate real V2 management -- classification
    # overrides the raw MT5 DEMO/LIVE flag. See account_registry.AccountClassification.
    SessionLocal = _session_factory(monkeypatch)
    fake = _FakeAdapter()
    monkeypatch.setattr(service, "mt5_adapter", fake)
    monkeypatch.setenv("ADAPTIVE_TRADE_MANAGEMENT_MODE", "demo_active")
    fp = account_registry.fingerprint_account(_FakeAccount())
    account_registry.record_sighting(fp, account_mode="DEMO")
    with SessionLocal() as db:
        row = db.get(account_registry.MT5AccountProfileORM, fp.fingerprint_hash)
        row.classification = account_registry.AccountClassification.PROP_EVALUATION.value
        db.commit()

    result = asyncio.run(adaptive_management_service.activate_demo(eligible_symbols=["EURUSD"]))

    assert result["status"] == "REJECTED"
    assert result["reason"] == "ACCOUNT_NOT_INTERNAL_DEMO"


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


class _FakePositionTwo:
    def model_dump(self, mode="json"):
        return {
            "ticket": 556,
            "identifier": 556,
            "symbol": "GBPUSD",
            "type": 0,
            "volume": 1.0,
            "price_open": 1.2500,
            "price_current": 1.2510,
            "sl": 1.2480,
            "tp": 1.2550,
            "profit": 50,
            "magic": 5601001,
            "comment": "BENSIM_AUTO strategy=BENSIM_AUTO timeframe=M5 setup=TEST",
            "time": "2026-08-04T08:00:00+00:00",
        }


class _FakeAdapterTwoPositions(_FakeAdapter):
    async def mt5_positions(self):
        return [_FakePosition(), _FakePositionTwo()]


def test_one_malformed_position_does_not_stop_other_positions_from_being_managed(monkeypatch):
    # Incident hardening (Part 5): the runaway-R bug on ticket 57873187767 crashed with a
    # ZeroDivisionError inside a SINGLE position's evaluation, which previously aborted the
    # entire monitor cycle -- every OTHER open position (EURUSD/GBPUSD/etc.) silently stopped
    # being managed too, for as long as the bug persisted. This test proves a crash confined to
    # one ticket (555/EURUSD) can no longer prevent a different ticket (556/GBPUSD) in the SAME
    # cycle from being evaluated.
    _session_factory(monkeypatch)
    fake = _FakeAdapterTwoPositions()
    monkeypatch.setattr(service, "mt5_adapter", fake)
    monkeypatch.setenv("ADAPTIVE_TRADE_MANAGEMENT_MODE", "demo_active")
    asyncio.run(adaptive_management_service.activate_demo(eligible_symbols=["EURUSD", "GBPUSD"], effective_from=datetime(2026, 8, 4, 7, 0, tzinfo=timezone.utc)))

    original_sync = service.AdaptiveManagementService._sync_position_state

    def _boom_for_eurusd(self, db, payload, *args, **kwargs):
        if str(payload.get("ticket")) == "555":
            raise ZeroDivisionError("simulated runaway-R incident")
        return original_sync(self, db, payload, *args, **kwargs)

    monkeypatch.setattr(service.AdaptiveManagementService, "_sync_position_state", _boom_for_eurusd)

    result = asyncio.run(adaptive_management_service.evaluate_now())

    assert result["status"] == "OK"
    assert not any(row["position_id"] == "555" for row in result["selected_actions"])
    assert any(row["position_id"] == "556" for row in result["selected_actions"])
    status = adaptive_management_service.status()
    assert status["adaptive_position_evaluation_errors_total"] >= 1
    assert status["adaptive_position_evaluation_errors_by_symbol"].get("EURUSD", 0) >= 1


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


def test_sl_at_entry_does_not_crash_position_sync(monkeypatch):
    # Regression test for a live incident: a breakeven SL that lands exactly on entry price (sl
    # == entry, e.g. after broker-side rounding) made risk == abs(entry - sl) == 0. The old
    # `if sl else 0.00001` guard didn't catch a zero DISTANCE (only a missing sl), so this raised
    # ZeroDivisionError inside _signed_r() and crashed the entire adaptive-manager monitor cycle
    # -- for every open position, every cycle, until the position closed.
    SessionLocal = _session_factory(monkeypatch)
    payload = {"ticket": 1, "identifier": 1, "symbol": "XAUUSD", "type": 0, "volume": 0.1, "price_open": 4279.93, "price_current": 4290.84, "sl": 4279.93, "tp": 4316.14, "time": "2026-08-07T06:58:36+00:00", "comment": "BSM|MTFAI1|M15"}

    with SessionLocal() as db:
        state = adaptive_management_service._sync_position_state(db, payload, None, {}, [])
        db.commit()

    assert state.current_sl == pytest.approx(4279.93)


def test_r_uses_original_sl_not_current_sl_after_breakeven_move(monkeypatch):
    # Second, more serious live incident on the SAME trade, uncovered immediately after fixing
    # the crash above: once a genuinely-tracked position's SL is later moved to (near) entry by a
    # real protective action, computing R's risk denominator from the CURRENT sl (abs(entry -
    # current_sl) ~= 0, caught only by an epsilon fallback) exploded r_now into the hundreds of
    # thousands -- which then satisfied every R-based threshold at once and fired several REAL,
    # repeated PARTIAL_PROFIT broker closes in production (0.10 lot reduced to 0.03 lot in under
    # a minute). R must stay anchored to the trade's ORIGINAL risk distance regardless of where
    # the live stop currently sits.
    SessionLocal = _session_factory(monkeypatch)
    entry_payload = {"ticket": 2, "identifier": 2, "symbol": "XAUUSD", "type": 0, "volume": 0.1, "price_open": 4279.93, "price_current": 4279.93, "sl": 4256.34, "tp": 4316.14, "time": "2026-08-07T06:00:30+00:00", "comment": "BSM|MTFAI1|M15"}
    breakeven_payload = dict(entry_payload, price_current=4290.84, sl=4279.93)  # SL moved to entry

    with SessionLocal() as db:
        adaptive_management_service._sync_position_state(db, entry_payload, None, {}, [])
        db.commit()
        state = adaptive_management_service._sync_position_state(db, breakeven_payload, None, {}, [])
        db.commit()
        max_achieved_r = state.max_achieved_r

    # True R at this point: (4290.84 - 4279.93) / (4279.93 - 4256.34) ~= 0.4626 -- nowhere near
    # the "hundreds of thousands" the current-sl-based epsilon fallback produced.
    assert max_achieved_r == pytest.approx(0.4626, abs=0.01)
    assert max_achieved_r < 5.0


def test_corrupted_stored_max_achieved_r_self_heals_instead_of_persisting_forever(monkeypatch):
    # max_achieved_r/min_achieved_r are otherwise pure max()/min() against their own prior
    # value, so ANY single bad r_now (e.g. one computed before the fix above existed) poisons
    # the column permanently -- every later, correctly-computed r_now still loses to max()
    # against the old garbage. This happened in production: a live position's max_achieved_r
    # was found at ~854,999 after the bug above fired once. A stored value that implausible
    # (no trade in this system legitimately reaches +/-50R) must be discarded, not preserved.
    SessionLocal = _session_factory(monkeypatch)
    payload = {"ticket": 3, "identifier": 3, "symbol": "XAUUSD", "type": 0, "volume": 0.1, "price_open": 4279.93, "price_current": 4290.84, "sl": 4279.93, "tp": 4316.14, "time": "2026-08-07T06:58:36+00:00", "comment": "BSM|MTFAI1|M15"}

    with SessionLocal() as db:
        row = AdaptivePositionStateORM(position_id="3", symbol="XAUUSD", direction="LONG", broker_ticket="3", entry_price=4279.93, original_sl=4256.34, current_sl=4279.93, max_achieved_r=854999.99, min_achieved_r=-1200.0)
        db.merge(row)
        db.commit()
        state = adaptive_management_service._sync_position_state(db, payload, None, {}, [])
        db.commit()
        max_achieved_r = state.max_achieved_r
        min_achieved_r = state.min_achieved_r

    assert max_achieved_r < 5.0
    assert min_achieved_r > -5.0


# ---------------------------------------------------------------------------
# Canonical monetary risk: immutable original_* fields, mutable current_* fields, monetary R,
# R_UNAVAILABLE, legacy reconstruction, and the persistent partial-profit stage machine.
# ---------------------------------------------------------------------------


def _mt5_symbol_eurusd():
    from backend.brokers.mt5.models import MT5Symbol
    from decimal import Decimal

    return MT5Symbol(
        symbol="EURUSD", visible=True, selected=True, digits=5, point=Decimal("0.00001"),
        trade_tick_size=Decimal("0.00001"), trade_tick_value=Decimal("1.0"), trade_tick_value_profit=Decimal("1.0"),
        trade_tick_value_loss=Decimal("1.0"), trade_contract_size=Decimal("100000"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )


def test_original_risk_money_persisted_once_at_first_sight(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    payload = {"ticket": 10, "identifier": 10, "symbol": "EURUSD", "type": 0, "volume": 1.0, "price_open": 1.1000, "price_current": 1.1010, "sl": 1.0990, "tp": 1.1030, "time": "2026-08-07T08:00:00+00:00", "comment": "BENSIM_AUTO"}

    with SessionLocal() as db:
        state = adaptive_management_service._sync_position_state(db, payload, None, {}, [], symbol_info=_mt5_symbol_eurusd(), account_equity=10000.0)
        db.commit()
        original_risk_money = state.original_risk_money
        original_risk_source = state.original_risk_source
        original_entry = state.original_entry
        original_stop_distance = state.original_stop_distance

    assert original_risk_money == pytest.approx(100.0, rel=0.01)  # 0.001 distance x 100000 contract x 1.0 volume
    assert original_risk_source in {"CONSERVATIVE_MULTI_METHOD", "BROKER_NATIVE"}
    assert original_entry == pytest.approx(1.1000)
    assert original_stop_distance == pytest.approx(0.0010, abs=1e-6)


def test_original_risk_immutable_after_breakeven_move(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    opened = {"ticket": 11, "identifier": 11, "symbol": "EURUSD", "type": 0, "volume": 1.0, "price_open": 1.1000, "price_current": 1.1000, "sl": 1.0990, "tp": 1.1030, "time": "2026-08-07T08:00:00+00:00", "comment": "BENSIM_AUTO"}
    after_breakeven = dict(opened, price_current=1.1050, sl=1.1000)  # SL moved to entry

    with SessionLocal() as db:
        adaptive_management_service._sync_position_state(db, opened, None, {}, [], symbol_info=_mt5_symbol_eurusd(), account_equity=10000.0)
        db.commit()
        state = adaptive_management_service._sync_position_state(db, after_breakeven, None, {}, [], symbol_info=_mt5_symbol_eurusd(), account_equity=10000.0)
        db.commit()
        original_risk_money = state.original_risk_money
        original_stop_distance = state.original_stop_distance

    assert original_risk_money == pytest.approx(100.0, rel=0.01)  # unchanged by the SL move
    assert original_stop_distance == pytest.approx(0.0010, abs=1e-6)


def test_original_risk_immutable_after_trailing_into_profit(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    opened = {"ticket": 12, "identifier": 12, "symbol": "EURUSD", "type": 0, "volume": 1.0, "price_open": 1.1000, "price_current": 1.1000, "sl": 1.0990, "tp": 1.1030, "time": "2026-08-07T08:00:00+00:00", "comment": "BENSIM_AUTO"}
    after_trailing = dict(opened, price_current=1.1080, sl=1.1040)  # SL trailed into profit territory

    with SessionLocal() as db:
        adaptive_management_service._sync_position_state(db, opened, None, {}, [], symbol_info=_mt5_symbol_eurusd(), account_equity=10000.0)
        db.commit()
        state = adaptive_management_service._sync_position_state(db, after_trailing, None, {}, [], symbol_info=_mt5_symbol_eurusd(), account_equity=10000.0)
        db.commit()
        original_risk_money = state.original_risk_money

    assert original_risk_money == pytest.approx(100.0, rel=0.01)


def test_original_risk_immutable_after_partial_close_reduces_volume(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    opened = {"ticket": 13, "identifier": 13, "symbol": "EURUSD", "type": 0, "volume": 1.0, "price_open": 1.1000, "price_current": 1.1000, "sl": 1.0990, "tp": 1.1030, "time": "2026-08-07T08:00:00+00:00", "comment": "BENSIM_AUTO"}
    after_partial = dict(opened, volume=0.75, price_current=1.1020)  # 25% partial close

    with SessionLocal() as db:
        adaptive_management_service._sync_position_state(db, opened, None, {}, [], symbol_info=_mt5_symbol_eurusd(), account_equity=10000.0)
        db.commit()
        state = adaptive_management_service._sync_position_state(db, after_partial, None, {}, [], symbol_info=_mt5_symbol_eurusd(), account_equity=10000.0)
        db.commit()
        original_risk_money = state.original_risk_money
        original_volume = state.original_volume
        current_volume = state.current_volume

    assert original_risk_money == pytest.approx(100.0, rel=0.01)  # still the FULL 1.0-lot original risk
    assert original_volume == pytest.approx(1.0)
    assert current_volume == pytest.approx(0.75)


def test_current_risk_money_reflects_live_sl_not_original(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    opened = {"ticket": 14, "identifier": 14, "symbol": "EURUSD", "type": 0, "volume": 1.0, "price_open": 1.1000, "price_current": 1.1000, "sl": 1.0990, "tp": 1.1030, "time": "2026-08-07T08:00:00+00:00", "comment": "BENSIM_AUTO"}
    reduced_risk = dict(opened, price_current=1.1015, sl=1.0995)  # SL improved halfway
    trailed_into_profit = dict(opened, price_current=1.1080, sl=1.1040)  # SL now protects profit

    with SessionLocal() as db:
        adaptive_management_service._sync_position_state(db, opened, None, {}, [], symbol_info=_mt5_symbol_eurusd(), account_equity=10000.0)
        db.commit()
        state_reduced = adaptive_management_service._sync_position_state(db, reduced_risk, None, {}, [], symbol_info=_mt5_symbol_eurusd(), account_equity=10000.0)
        db.commit()
        reduced_current_risk = state_reduced.current_risk_money
        state_profit = adaptive_management_service._sync_position_state(db, trailed_into_profit, None, {}, [], symbol_info=_mt5_symbol_eurusd(), account_equity=10000.0)
        db.commit()
        profit_current_risk = state_profit.current_risk_money
        protected_profit = state_profit.protected_profit_money

    assert reduced_current_risk == pytest.approx(50.0, rel=0.05)  # half the original $100 risk remains
    assert profit_current_risk == pytest.approx(0.0, abs=0.01)
    assert protected_profit > 0


def test_monetary_r_uses_broker_profit_over_original_risk_money(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    opened = {"ticket": 15, "identifier": 15, "symbol": "EURUSD", "type": 0, "volume": 1.0, "price_open": 1.1000, "price_current": 1.1000, "sl": 1.0990, "tp": 1.1030, "profit": 0.0, "time": "2026-08-07T08:00:00+00:00", "comment": "BENSIM_AUTO"}
    in_profit = dict(opened, price_current=1.1050, profit=50.0)  # broker-reported $50 profit, original risk $100 -> R=0.5

    with SessionLocal() as db:
        adaptive_management_service._sync_position_state(db, opened, None, {}, [], symbol_info=_mt5_symbol_eurusd(), account_equity=10000.0)
        db.commit()
        state = adaptive_management_service._sync_position_state(db, in_profit, None, {}, [], symbol_info=_mt5_symbol_eurusd(), account_equity=10000.0)
        db.commit()
        max_achieved_r = state.max_achieved_r
        r_source = state.r_source

    assert r_source == "MONEY"
    assert max_achieved_r == pytest.approx(0.5, rel=0.01)


def test_r_unavailable_when_no_sl_and_no_original_risk(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("R_UNAVAIL")
    state.current_sl = None
    state.original_sl = None
    state.original_risk_money = None
    payload = {"price_current": 4001.0}

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, [])

    # Missing static protection (no SL) is the FIRST gate -- R never even needs evaluating.
    assert any(c.reason == "missing_static_protection_preserve_manual_review" for c in candidates)


def test_r_unavailable_skips_r_threshold_candidates_but_keeps_equity_layer(monkeypatch):
    # Pinned explicitly: real deployed .env now lowers these thresholds (2026-08-18, user-
    # requested account-level profit lock) below this test's own $200/$10,000=2% scenario, which
    # would otherwise still land in the "protect"/"strong" tier either way -- pinned to the
    # historically-documented defaults so this test verifies the tier logic, not today's tuning.
    monkeypatch.setenv("ADAPTIVE_PROFIT_REASSESS_EQUITY_PCT", "0.50")
    monkeypatch.setenv("ADAPTIVE_PROFIT_PROTECT_EQUITY_PCT", "0.75")
    monkeypatch.setenv("ADAPTIVE_STRONG_PROFIT_EQUITY_PCT", "1.00")
    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("R_UNAVAIL2")
    state.original_sl = None  # SL exists (current_sl set in _managed_state) but no ORIGINAL sl and no original_risk_money
    state.original_risk_money = None
    payload = {"price_current": 4010.0, "profit": 200.0}

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, [], account_equity=10000.0)

    hold = next(c for c in candidates if c.action_type == "HOLD")
    assert hold.evidence["r_source"] == "UNAVAILABLE"
    assert not any(c.action_type == "PARTIAL_PROFIT" for c in candidates)
    # The account-scaled equity layer is R-independent and must still be able to reassess. 2% of
    # equity is above the "strong" tier (1.00%), so this now fires a real ACCOUNT_PROFIT_LOCK
    # partial close (2026-08-18 change) rather than the old passive-only HOLD_WITH_GIVEBACK_RISK.
    assert any(c.action_type == "ACCOUNT_PROFIT_LOCK" for c in candidates)


def test_legacy_position_reconstructs_original_risk_once(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    # Simulates a row created by an OLDER version of this code: original_sl/tp/volume already
    # exist, but original_risk_money (added in this hardening) does not.
    legacy_row = AdaptivePositionStateORM(
        position_id="LEGACY1", symbol="EURUSD", direction="LONG", broker_ticket="LEGACY1",
        entry_price=1.1000, original_sl=1.0990, current_sl=1.0995, original_tp=1.1030, current_tp=1.1030,
        original_volume=1.0, current_volume=1.0,
    )
    payload = {"ticket": "LEGACY1", "identifier": "LEGACY1", "symbol": "EURUSD", "type": 0, "volume": 1.0, "price_open": 1.1000, "price_current": 1.1010, "sl": 1.0995, "tp": 1.1030, "time": "2026-08-07T08:00:00+00:00", "comment": "BENSIM_AUTO"}

    with SessionLocal() as db:
        db.merge(legacy_row)
        db.commit()
        state = adaptive_management_service._sync_position_state(db, payload, None, {}, [], symbol_info=_mt5_symbol_eurusd(), account_equity=10000.0)
        db.commit()
        original_risk_money = state.original_risk_money
        original_risk_source = state.original_risk_source

    assert original_risk_money == pytest.approx(100.0, rel=0.01)  # from original_sl (1.0990), NOT current_sl (1.0995)
    assert original_risk_source == "RECONSTRUCTED"


def _base_activation_and_breaker():
    from backend.adaptive_management.orm import AdaptiveActivationORM, AdaptiveCircuitBreakerORM

    activation = AdaptiveActivationORM(activation_id="ACT_PS", policy_id=service.ACTIVE_POLICY_ID, policy_version=service.ACTIVE_POLICY_VERSION, account_fingerprint=None, effective_from=datetime.now(timezone.utc), eligible_strategies=[], eligible_symbols=[], maximum_actions_per_hour=6, active=True, demo_account="1", approved_by="test", approved_at=datetime.now(timezone.utc), rollback_policy={}, emergency_state="normal", configuration_snapshot={}, mode="demo_active")
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B_PS", state="closed", actions_this_hour=0)
    return activation, breaker


def test_partial_1_stage_executes_once_and_blocks_repeat_candidate(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("PS1")
    state.partial_profit_stage = "NONE"
    action = AdaptiveManagementActionORM(action_id="A_PS1", position_id="PS1", policy_id=service.ACTIVE_POLICY_ID, action_type="PARTIAL_PROFIT", priority=40, mode="demo_active", status="selected", idempotency_key="A_PS1-KEY", requested_volume=0.25, evidence={"target_stage": "PARTIAL_1", "requested_fraction": 0.25})

    with SessionLocal() as db:
        db.merge(state)
        db.commit()
        stage_row = adaptive_management_service._begin_partial_stage_attempt(db, state, action)
        db.commit()
        assert stage_row.stage == "PARTIAL_1_PENDING"
        adaptive_management_service._resolve_partial_stage_attempt(db, stage_row, state, {"status": "ACCEPTED", "filled_volume": 0.25, "broker_ticket": "999"})
        updated = db.get(AdaptivePositionStateORM, "PS1")
        assert updated.partial_profit_stage == "PARTIAL_1_EXECUTED"

    # A fresh evaluation, even with r_now still well above the stage-1 threshold, must NOT
    # generate a second PARTIAL_1 candidate -- the stage has already executed.
    with SessionLocal() as db:
        updated = db.get(AdaptivePositionStateORM, "PS1")
        candidates = adaptive_management_service._evaluate_position(db, updated, {"price_current": 4050.0, "profit": 500.0}, {}, [])
    assert not [c for c in candidates if c.action_type == "PARTIAL_PROFIT" and c.evidence.get("target_stage") == "PARTIAL_1"]


def test_partial_2_stage_executes_once_and_advances_to_runner(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("PS2")
    state.partial_profit_stage = "PARTIAL_1_EXECUTED"
    action = AdaptiveManagementActionORM(action_id="A_PS2", position_id="PS2", policy_id=service.ACTIVE_POLICY_ID, action_type="PARTIAL_PROFIT", priority=40, mode="demo_active", status="selected", idempotency_key="A_PS2-KEY", requested_volume=0.25, evidence={"target_stage": "PARTIAL_2", "requested_fraction": 0.33})

    with SessionLocal() as db:
        db.merge(state)
        db.commit()
        stage_row = adaptive_management_service._begin_partial_stage_attempt(db, state, action)
        db.commit()
        adaptive_management_service._resolve_partial_stage_attempt(db, stage_row, state, {"status": "ACCEPTED", "filled_volume": 0.25, "broker_ticket": "1000"})
        updated = db.get(AdaptivePositionStateORM, "PS2")

    assert updated.partial_profit_stage == "RUNNER"


def test_cooldown_expiry_cannot_repeat_an_executed_stage(monkeypatch):
    # Stage-gating is independent of the modification cooldown -- even with the cooldown long
    # expired (last_management_at far in the past), an EXECUTED stage must never fire again.
    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("PS_COOLDOWN")
    state.partial_profit_stage = "PARTIAL_1_EXECUTED"
    state.last_management_at = datetime.now(timezone.utc) - timedelta(hours=1)

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, {"price_current": 4050.0, "profit": 500.0}, {}, [])

    assert not [c for c in candidates if c.action_type == "PARTIAL_PROFIT" and c.evidence.get("target_stage") == "PARTIAL_1"]


def test_restart_reconciliation_confirms_executed_stage_from_execution_order(monkeypatch):
    # PART 11: a PENDING row left behind by a crash (between committing PENDING and resolving
    # it) must reconcile against the Execution Manager's own durable order-state record, never
    # blindly resubmit.
    from backend.portfolio_execution.orm import ExecutionOrderORM

    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("PS_RESTART_OK")
    with SessionLocal() as db:
        db.merge(state)
        order = ExecutionOrderORM(execution_id="EXE_RESTART_OK", idempotency_key="KEY_RESTART_OK", source="adaptive_trade_manager", symbol="XAUUSD", action_type="DEAL", requested_volume=0.25, state="ACCEPTED", deal_ticket="777", order_ticket="888")
        db.merge(order)
        stage_row = AdaptivePartialProfitStageORM(stage_attempt_id="APPS_RESTART_OK", position_id="PS_RESTART_OK", broker_ticket="PS_RESTART_OK", stage="PARTIAL_1_PENDING", requested_volume=0.25, idempotency_key="KEY_RESTART_OK", requested_at=datetime.now(timezone.utc), reconciliation_status="PENDING")
        db.merge(stage_row)
        db.commit()

        import asyncio as _asyncio
        _asyncio.run(adaptive_management_service._reconcile_pending_partial_stages(db, {"PS_RESTART_OK"}))

        resolved = db.query(AdaptivePartialProfitStageORM).filter(AdaptivePartialProfitStageORM.stage_attempt_id == "APPS_RESTART_OK").first()
        updated_state = db.get(AdaptivePositionStateORM, "PS_RESTART_OK")

    assert resolved.reconciliation_status == "CONFIRMED_EXECUTED"
    assert resolved.stage == "PARTIAL_1_EXECUTED"
    assert updated_state.partial_profit_stage == "PARTIAL_1_EXECUTED"


def test_restart_reconciliation_allows_retry_after_confirmed_rejection(monkeypatch):
    from backend.portfolio_execution.orm import ExecutionOrderORM

    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("PS_RESTART_FAIL")
    with SessionLocal() as db:
        db.merge(state)
        order = ExecutionOrderORM(execution_id="EXE_RESTART_FAIL", idempotency_key="KEY_RESTART_FAIL", source="adaptive_trade_manager", symbol="XAUUSD", action_type="DEAL", requested_volume=0.25, state="REJECTED")
        db.merge(order)
        stage_row = AdaptivePartialProfitStageORM(stage_attempt_id="APPS_RESTART_FAIL", position_id="PS_RESTART_FAIL", broker_ticket="PS_RESTART_FAIL", stage="PARTIAL_1_PENDING", requested_volume=0.25, idempotency_key="KEY_RESTART_FAIL", requested_at=datetime.now(timezone.utc), reconciliation_status="PENDING")
        db.merge(stage_row)
        db.commit()

        import asyncio as _asyncio
        _asyncio.run(adaptive_management_service._reconcile_pending_partial_stages(db, {"PS_RESTART_FAIL"}))

        resolved = db.query(AdaptivePartialProfitStageORM).filter(AdaptivePartialProfitStageORM.stage_attempt_id == "APPS_RESTART_FAIL").first()
        updated_state = db.get(AdaptivePositionStateORM, "PS_RESTART_FAIL")

    assert resolved.reconciliation_status == "CONFIRMED_NOT_EXECUTED"
    assert updated_state.partial_profit_stage == "NONE"  # safe to retry


def test_restart_reconciliation_ambiguous_pending_never_auto_resubmits(monkeypatch):
    # The Execution Manager's own order is STILL "SUBMITTED" (neither confirmed executed nor
    # confirmed rejected) -- genuinely ambiguous whether the broker ever saw the request. Must
    # become RECONCILIATION_REQUIRED, never silently retried.
    from backend.portfolio_execution.orm import ExecutionOrderORM

    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("PS_RESTART_AMBIGUOUS")
    with SessionLocal() as db:
        db.merge(state)
        order = ExecutionOrderORM(execution_id="EXE_RESTART_AMBIG", idempotency_key="KEY_RESTART_AMBIG", source="adaptive_trade_manager", symbol="XAUUSD", action_type="DEAL", requested_volume=0.25, state="SUBMITTED")
        db.merge(order)
        stage_row = AdaptivePartialProfitStageORM(stage_attempt_id="APPS_RESTART_AMBIG", position_id="PS_RESTART_AMBIGUOUS", broker_ticket="PS_RESTART_AMBIGUOUS", stage="PARTIAL_1_PENDING", requested_volume=0.25, idempotency_key="KEY_RESTART_AMBIG", requested_at=datetime.now(timezone.utc), reconciliation_status="PENDING")
        db.merge(stage_row)
        db.commit()

        import asyncio as _asyncio
        _asyncio.run(adaptive_management_service._reconcile_pending_partial_stages(db, {"PS_RESTART_AMBIGUOUS"}))

        resolved = db.query(AdaptivePartialProfitStageORM).filter(AdaptivePartialProfitStageORM.stage_attempt_id == "APPS_RESTART_AMBIG").first()
        updated_state = db.get(AdaptivePositionStateORM, "PS_RESTART_AMBIGUOUS")

    assert resolved.reconciliation_status == "RECONCILIATION_REQUIRED"
    assert updated_state.partial_profit_stage == "RECONCILIATION_REQUIRED"


def test_breakeven_price_uses_real_symbol_point_not_generic_fx_guess():
    # _point_guess() only distinguishes "JPY pair" (0.01) from "everything else" (0.0001) and
    # has no idea Gold exists. On a real broker (XAUUSD, digits=2, point=0.01) that made the
    # breakeven cost buffer (2 points x the guessed point value) only 0.0002 -- so the requested
    # SL (entry + 0.0002) rounded, at the broker's own 2-decimal precision, to a price
    # bit-identical to entry: a "breakeven" stop with zero real buffer and zero risk distance
    # (see test_sl_at_entry_does_not_crash_position_sync). Passing the real symbol_info fixes it.
    state = AdaptivePositionStateORM(position_id="XAU_BE", symbol="XAUUSD", direction="LONG", entry_price=4279.93)
    generic_guess_price = service._breakeven_price(state, symbol_info=None)
    real_symbol_info = type("Sym", (), {"point": 0.01, "trade_tick_size": 0.01})()
    real_price = service._breakeven_price(state, symbol_info=real_symbol_info)

    assert round(generic_guess_price, 2) == 4279.93  # rounds away to nothing at 2 decimals
    assert round(real_price, 2) != 4279.93  # real point-based buffer survives rounding
    assert real_price > generic_guess_price


def test_equity_profit_strong_tier_generates_account_profit_lock(monkeypatch):
    # $100 profit on a $10,000 account is exactly the 1.00% ADAPTIVE_STRONG_PROFIT_EQUITY_PCT
    # tier -- this is the account-scaled second protection layer, independent of R, that the
    # XAUUSD incident showed was missing: a trade can reach a meaningful fraction of account
    # equity while still below this manager's R-based thresholds for THIS trade's own (possibly
    # oversized) initial risk. 2026-08-18: this tier now fires a real ACCOUNT_PROFIT_LOCK partial
    # close (previously only logged HOLD_WITH_GIVEBACK_RISK, never executed) -- user-requested,
    # account-size-scaled profit taking. Pinned explicitly since real .env now lowers these
    # thresholds; this test verifies the tier boundary logic, not today's specific tuning.
    monkeypatch.setenv("ADAPTIVE_PROFIT_REASSESS_EQUITY_PCT", "0.50")
    monkeypatch.setenv("ADAPTIVE_PROFIT_PROTECT_EQUITY_PCT", "0.75")
    monkeypatch.setenv("ADAPTIVE_STRONG_PROFIT_EQUITY_PCT", "1.00")
    monkeypatch.setenv("ADAPTIVE_ACCOUNT_PROFIT_LOCK_FRACTION", "0.30")
    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("EQ1")
    state.max_achieved_r = 0.3
    state.current_sl = state.original_sl  # no protective SL move has happened yet
    payload = {"price_current": 4005.0, "profit": 100.0}

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, [], account_equity=10000.0)

    lock_candidates = [c for c in candidates if c.action_type == "ACCOUNT_PROFIT_LOCK"]
    assert len(lock_candidates) == 1
    assert not [c for c in candidates if c.action_type == "HOLD_WITH_GIVEBACK_RISK"]
    lock = lock_candidates[0]
    evidence = lock.evidence
    assert evidence["equity_profit_pct"] == pytest.approx(1.0)
    assert evidence["tier"] == "strong_profit_no_explicit_protection"
    assert evidence["profit_usd"] == 100.0
    assert lock.requested_volume == pytest.approx(float(state.current_volume) * 0.30)


def test_equity_profit_below_reassess_threshold_does_not_trigger(monkeypatch):
    # Pinned: real .env now sets ADAPTIVE_PROFIT_REASSESS_EQUITY_PCT=0.10, which this test's own
    # 0.10%-of-equity scenario would land exactly ON (not below) -- pinned to the historically-
    # documented default so this verifies the "below floor" boundary case specifically.
    monkeypatch.setenv("ADAPTIVE_PROFIT_REASSESS_EQUITY_PCT", "0.50")
    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("EQ2")
    payload = {"price_current": 4001.0, "profit": 10.0}  # 0.10% of 10k, below the 0.50% floor

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, [], account_equity=10000.0)

    assert not [c for c in candidates if c.action_type == "HOLD_WITH_GIVEBACK_RISK"]


def test_hold_with_giveback_risk_does_not_fire_when_stronger_protection_selected(monkeypatch):
    # The equity layer is a fallback for "nothing else protected this profit," not an
    # independent close/resize trigger -- it must never outrank a real protective candidate
    # (e.g. the existing MFE giveback close) that already fired this cycle.
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.delenv("ADAPTIVE_MFE_MIN_R", raising=False)
    state = _managed_state("EQ3")
    state.max_achieved_r = 0.55
    payload = {"price_current": 4001.32, "profit": 200.0}  # 2% of equity -- well above every tier

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, [], account_equity=10000.0)

    action_types = {c.action_type for c in candidates}
    assert "MFE_PROTECTION_CLOSE" in action_types
    assert "HOLD_WITH_GIVEBACK_RISK" not in action_types


def test_hold_with_giveback_risk_never_executes(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    state = _managed_state("EQ4")
    activation = AdaptiveActivationORM(activation_id="ACT_EQ")
    activation.policy_id = service.ACTIVE_POLICY_ID
    activation.policy_version = service.ACTIVE_POLICY_VERSION
    activation.mode = "demo_active"
    activation.demo_account = "123"
    activation.effective_from = datetime.now(timezone.utc)
    activation.approved_by = "test"
    activation.approved_at = activation.effective_from
    activation.eligible_symbols = []
    activation.eligible_strategies = []
    activation.maximum_actions_per_hour = 6
    activation.rollback_policy = {}
    activation.emergency_state = "normal"
    activation.configuration_snapshot = {}
    activation.active = True
    action = AdaptiveManagementActionORM(action_id="A_EQ", activation_id="ACT_EQ", position_id="EQ4", policy_id=service.ACTIVE_POLICY_ID, action_type="HOLD_WITH_GIVEBACK_RISK", priority=90, mode="demo_active", status="selected", idempotency_key="A_EQ-KEY", broker_mutation_attempted=False)

    with SessionLocal() as db:
        db.merge(activation)
        db.merge(state)
        db.merge(action)
        breaker = service._breaker(db)
        db.commit()

        assert adaptive_management_service._can_execute(action, state, activation, breaker, "demo_active") is False


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


def test_parse_bsm_comment_does_not_mistake_the_order_timestamp_for_a_timeframe():
    """Root-cause fix (this session's ATM auto-replay investigation): real live order comments
    are built by brokers/mt5/autonomous.py::_mt5_order_comment() as "BSM|{strategy}|{HHMM order
    timestamp}" -- only 3 pipe-segments, and that function's own docstring confirms timeframe was
    deliberately DROPPED from the format ("timeframe was always 'M15' for every trade this bot
    places"). The old code read parts[2] as "timeframe", which for a real comment is actually an
    HHMM timestamp like "0415"/"2215" -- confirmed as the root cause of ~94% of closed positions
    carrying a corrupted timeframe value. Must now return "M15" regardless of what the timestamp
    segment actually contains."""
    assert service._lineage("BSM|mtfai1|0415", "timeframe") == "M15"
    assert service._lineage("BSM|mtfai1|2215", "timeframe") == "M15"
    assert service._lineage("BSM|mtfai1|0000", "timeframe") == "M15"
    # Strategy attribution (the part that DOES vary) must still parse correctly.
    assert service._lineage("BSM|mtfai1|0415", "strategy") == "mtfai1"


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

    assert first_strategy == "mtfai1"
    assert first_timeframe == "M15"
    assert second_strategy == "mtfai1"
    assert second_timeframe == "M15"


def test_sync_position_state_captures_entry_regime_once_not_live_regime(monkeypatch):
    # First sync: a clear uptrend -> detect_regime should classify "trending_up" and it must be
    # captured onto entry_regime permanently (this is the market condition the TRADE WAS ENTERED
    # IN, not whatever the market happens to be doing on a later management cycle).
    SessionLocal = _session_factory(monkeypatch)
    start = datetime(2026, 8, 4, 8, 0, tzinfo=timezone.utc)
    trending_candles = [{"time": (start + timedelta(minutes=5 * i)).isoformat(), "open": 1.1000 + i * 0.0008, "high": 1.1002 + i * 0.0008, "low": 1.0999 + i * 0.0008, "close": 1.1001 + i * 0.0008, "spread": 1} for i in range(14)]
    payload = {"ticket": 902, "identifier": 902, "symbol": "EURUSD", "type": 0, "volume": 1.0, "price_open": 1.1000, "price_current": 1.1010, "sl": 1.0980, "tp": 1.1100, "time": "2026-08-04T08:00:00+00:00", "comment": "BSM|MTFAI1|M5|EURUSD0800"}

    with SessionLocal() as db:
        state = adaptive_management_service._sync_position_state(db, payload, None, {}, trending_candles)
        db.commit()
        first_entry_regime = state.entry_regime
        first_entry_atr = state.entry_atr

    assert first_entry_regime == "trending_up"
    assert first_entry_atr is not None

    # Second sync: flat/ranging candles (a completely different live regime) must NOT overwrite
    # the entry-time snapshot already captured above.
    flat_candles = [{"time": (start + timedelta(minutes=5 * (14 + i))).isoformat(), "open": 1.1100, "high": 1.11005, "low": 1.10995, "close": 1.1100, "spread": 1} for i in range(14)]
    with SessionLocal() as db:
        state = adaptive_management_service._sync_position_state(db, payload, None, {}, flat_candles)
        db.commit()
        second_entry_regime = state.entry_regime

    assert second_entry_regime == first_entry_regime == "trending_up"


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
    # adaptive_management_service is AdaptiveManagementMultiAccountOrchestrator -- the actual
    # _reconcile_recently_closed call below runs as a bound method of .default_service (self is
    # default_service there), so patches need to land on that inner object, not the orchestrator
    # wrapper (which only affects attribute lookups made ON the orchestrator itself, never
    # internal `self.x` calls made from within default_service's own methods).
    SessionLocal = _session_factory(monkeypatch)
    adaptive_management_service.default_service._last_reconciliation_at = None
    calls = []

    async def _fake_import(*, days=7, session_id=None):
        calls.append(days)
        return {"status": "ok"}

    monkeypatch.setattr(adaptive_management_service.default_service, "import_mt5_session", _fake_import)

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
