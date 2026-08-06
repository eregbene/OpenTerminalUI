from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.adaptive_management.orm import AdaptivePositionStateORM
from backend.adaptive_management.service import adaptive_management_service
from backend.adaptive_management import service as adaptive_service_module
from backend.brokers.mt5 import account_registry
from backend.brokers.mt5.autonomous import mt5_autonomous_service
from backend.portfolio_execution import service as execution_service_module
from backend.portfolio_execution.orm import ExecutionOrderORM
from backend.portfolio_execution.service import execution_manager
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


def _adaptive_session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(adaptive_service_module, "SessionLocal", SessionLocal)
    return SessionLocal


def _execution_session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(execution_service_module, "SessionLocal", SessionLocal)
    monkeypatch.setattr(account_registry, "SessionLocal", SessionLocal)
    return SessionLocal


# --- economic guard never forces a close, but explicit deterministic risk rules still fire ---


def test_economic_manage_existing_only_suppresses_nonessential_sltp_changes(monkeypatch):
    SessionLocal = _adaptive_session_factory(monkeypatch)
    state = _managed_state("XAU_ECON_BE")
    state.max_tp_progress = 0.35
    state.tp_progress = 0.35
    state.winner_classification = "healthy_pullback"
    payload = {"price_current": 4020.0}
    economic_result = {"guard": {"decision": "MANAGE_EXISTING_ONLY", "reason_codes": ["HIGH_IMPACT_EVENT_PRE_BLOCK"], "size_multiplier": 1.0}}

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, [], economic_result=economic_result)

    types = {c.action_type for c in candidates}
    assert "MOVE_SL_BREAKEVEN" not in types
    assert "TRAIL_STOP" not in types
    assert "ECONOMIC_MANAGE_EXISTING_ONLY" in types


def test_economic_guard_does_not_prevent_explicit_invalidation_close(monkeypatch):
    """Do not automatically close all positions before news -- but an explicit deterministic
    risk rule (two opposing completed candles after an adverse move) must still fire and win
    the priority selection even while the economic guard is active."""
    SessionLocal = _adaptive_session_factory(monkeypatch)
    state = _managed_state("XAU_ECON_INVALID")
    payload = {"price_current": 3990.0}
    candles = [
        {"time": "2026-08-05T10:00:00+00:00", "open": 4010, "high": 4012, "low": 4005, "close": 4006, "spread": 10},
        {"time": "2026-08-05T10:05:00+00:00", "open": 4006, "high": 4007, "low": 3999, "close": 3990, "spread": 10},
    ]
    economic_result = {"guard": {"decision": "MANAGE_EXISTING_ONLY", "reason_codes": ["HIGH_IMPACT_EVENT_PRE_BLOCK"], "size_multiplier": 1.0}}

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, candles, economic_result=economic_result)
        choice = adaptive_management_service._select_action(candidates)

    types = {c.action_type for c in candidates}
    assert "THESIS_INVALIDATION_CLOSE" in types
    assert choice.action_type == "THESIS_INVALIDATION_CLOSE"


def test_economic_reduce_size_only_trims_profitable_positions(monkeypatch):
    SessionLocal = _adaptive_session_factory(monkeypatch)
    state = _managed_state("XAU_ECON_REDUCE")
    state.tp_progress = 0.2
    payload = {"price_current": 4020.0}  # r_now = 1.0, profitable
    economic_result = {"guard": {"decision": "REDUCE_SIZE", "reason_codes": ["MEDIUM_IMPACT_EVENT_PRE_REDUCE"], "size_multiplier": 0.5}}

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, [], economic_result=economic_result)

    reduce_candidates = [c for c in candidates if c.action_type == "ECONOMIC_REDUCE_SIZE"]
    assert reduce_candidates
    assert reduce_candidates[0].requested_volume == 0.5  # current_volume(1.0) * (1 - 0.5)


def test_economic_reduce_size_never_trims_a_losing_position(monkeypatch):
    SessionLocal = _adaptive_session_factory(monkeypatch)
    state = _managed_state("XAU_ECON_REDUCE_LOSS")
    payload = {"price_current": 3990.0}  # r_now = -0.5, losing
    economic_result = {"guard": {"decision": "REDUCE_SIZE", "reason_codes": ["MEDIUM_IMPACT_EVENT_PRE_REDUCE"], "size_multiplier": 0.5}}

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, [], economic_result=economic_result)

    assert not any(c.action_type == "ECONOMIC_REDUCE_SIZE" for c in candidates)


def test_economic_evaluation_missing_defaults_to_no_new_restriction(monkeypatch):
    """economic_result=None (e.g. Forex Factory outage) must not introduce any new
    restriction -- existing management behavior is unaffected."""
    SessionLocal = _adaptive_session_factory(monkeypatch)
    state = _managed_state("XAU_ECON_NONE")
    state.max_tp_progress = 0.35
    state.tp_progress = 0.35
    state.winner_classification = "healthy_pullback"
    payload = {"price_current": 4020.0}

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, [], economic_result=None)

    types = {c.action_type for c in candidates}
    assert "MOVE_SL_BREAKEVEN" in types
    assert "ECONOMIC_MANAGE_EXISTING_ONLY" not in types


def test_profit_lock_candidate_still_proceeds_when_economic_guard_allows(monkeypatch):
    """ALLOW must never suppress an explicit deterministic risk rule -- TP_PROGRESS_PROFIT_LOCK
    fires exactly as it would with no economic guard involved at all."""
    SessionLocal = _adaptive_session_factory(monkeypatch)
    state = _managed_state("XAU_ECON_PROFIT_LOCK")
    state.max_tp_progress = 0.85
    state.tp_progress = 0.20
    state.winner_classification = "weakening"
    payload = {"price_current": 4000.0}
    economic_result = {"guard": {"decision": "ALLOW", "reason_codes": [], "size_multiplier": 1.0}}

    with SessionLocal() as db:
        candidates = adaptive_management_service._evaluate_position(db, state, payload, {}, [], economic_result=economic_result)
        choice = adaptive_management_service._select_action(candidates)

    action_types = {c.action_type for c in candidates}
    assert "TP_PROGRESS_PROFIT_LOCK" in action_types
    assert choice.action_type == "TP_PROGRESS_PROFIT_LOCK"


def test_all_economic_candidate_types_route_through_execution_manager():
    """ECONOMIC_REDUCE_SIZE joins the same TRADE_ACTION_DEAL dispatch set every other closing/
    reducing action type already uses in _build_mt5_request -- no new broker-mutation code
    path was introduced; ECONOMIC_MANAGE_EXISTING_ONLY is deliberately in neither set (it is a
    pure advisory/suppression marker, never itself a broker mutation)."""
    import inspect

    source = inspect.getsource(adaptive_management_service._build_mt5_request)
    assert '"ECONOMIC_REDUCE_SIZE"' in source
    assert '"ECONOMIC_MANAGE_EXISTING_ONLY"' not in source
    assert "execution_manager.submit_mt5_request" in inspect.getsource(adaptive_management_service._execute_action)


# --- economic guard blockers in the autonomous entry pipeline ---


def test_economic_blockers_only_trigger_on_block_or_delay():
    allow_result = {"guard": {"decision": "ALLOW", "reason_codes": []}}
    reduce_result = {"guard": {"decision": "REDUCE_SIZE", "reason_codes": ["MEDIUM_IMPACT_EVENT_PRE_REDUCE"]}}
    block_result = {"guard": {"decision": "BLOCK", "reason_codes": ["HIGH_IMPACT_EVENT_PRE_BLOCK"]}}
    delay_result = {"guard": {"decision": "DELAY", "reason_codes": ["HIGH_IMPACT_EVENT_POST_DELAY"]}}

    assert mt5_autonomous_service._economic_blockers(allow_result) == []
    assert mt5_autonomous_service._economic_blockers(reduce_result) == []
    assert mt5_autonomous_service._economic_blockers(block_result) != []
    assert mt5_autonomous_service._economic_blockers(delay_result) != []


# --- execution journal records the economic decision, and live trading stays blocked ---


def test_execution_manager_journals_economic_context(monkeypatch):
    SessionLocal = _execution_session_factory(monkeypatch)
    monkeypatch.setenv("MT5_LIVE_TRADING_ENABLED", "false")

    class _FakeSymbol:
        trade_mode = 0
        volume_min = 0.01

    class _FakeTerminal:
        account_mode = "DEMO"
        trade_allowed = True
        external_python_trading_allowed = True

    class _FakeQuote:
        spread = 1

    class _FakeMT5:
        TRADE_RETCODE_DONE = 10009

        def order_send(self, request):
            class _Result:
                def _asdict(self):
                    return {"retcode": 10009, "order": 1, "deal": 2, "volume": request["volume"], "price": request.get("price"), "comment": "ok"}

            return _Result()

    class _FakeClient:
        def ensure_ready(self):
            return _FakeMT5()

    class _FakeAccount:
        login = 123456
        server = "Bensim-Demo"
        company = "Bensim"
        currency = "USD"

    class _FakeAdapter:
        client = _FakeClient()

        async def terminal_status(self):
            return _FakeTerminal()

        async def symbol_info(self, symbol):
            return _FakeSymbol()

        async def latest_tick(self, symbol):
            return _FakeQuote()

        async def mt5_account(self):
            return _FakeAccount()

    economic_context = {"guard": {"decision": "ALLOW", "reason_codes": []}, "calendar": {"decision": "ALLOW"}}
    result = execution_service_module.asyncio.run(
        execution_manager.submit_mt5_request(adapter=_FakeAdapter(), request={"action": 1, "symbol": "EURUSD", "volume": 1.0, "price": 1.1}, idempotency_key="ECON_KEY", source="adaptive_trade_manager", expected_price=1.1, economic_context=economic_context)
    )
    assert result["retcode"] == 10009
    with SessionLocal() as db:
        row = db.query(ExecutionOrderORM).filter(ExecutionOrderORM.idempotency_key == "ECON_KEY").first()
    assert row.economic_context["guard"]["decision"] == "ALLOW"


def test_live_trading_still_blocked_even_with_economic_context(monkeypatch):
    _execution_session_factory(monkeypatch)
    monkeypatch.setenv("MT5_LIVE_TRADING_ENABLED", "true")
    economic_context = {"guard": {"decision": "ALLOW", "reason_codes": []}}

    result = execution_service_module.asyncio.run(
        execution_manager.submit_mt5_request(adapter=None, request={"symbol": "EURUSD", "volume": 1}, idempotency_key="ECON_LIVE_KEY", source="mt5_autonomous_entry", economic_context=economic_context)
    )
    assert result["status"] == "REJECTED"
    assert result["comment"] == "LIVE_TRADING_BLOCKED"
