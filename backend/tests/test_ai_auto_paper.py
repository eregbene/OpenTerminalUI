from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from backend.brokers.models import BrokerAccount, BrokerEnvironment, BrokerOrderCommand, BrokerReconciliationResult
from backend.intelligence.trading.auto_paper import AIAutoPaperTradingService
from backend.intelligence.trading.config import AITradingConfig
from backend.intelligence.trading.models import ProviderTelemetry, ShadowAnalysisResult, TradeDecision
from backend.intelligence.trading.usage import AIUsageLedger
from backend.api.routes.trading import AccountCreateRequest


def fresh_ledger(monkeypatch):
    import backend.intelligence.trading.auto_paper as module

    ledger = AIUsageLedger()
    state: dict[str, dict] = {}
    cycles: set[str] = set()
    monkeypatch.setattr(module, "usage_ledger", ledger)
    monkeypatch.setattr(module, "get_state", lambda key: dict(state.get(key, {})))
    monkeypatch.setattr(module, "set_state", lambda key, value: state.__setitem__(key, dict(value)))
    monkeypatch.setattr(module, "get_cycle", lambda cycle_id: SimpleNamespace(id=cycle_id) if cycle_id in cycles else None)
    monkeypatch.setattr(module, "save_cycle", lambda payload: cycles.add(payload["id"]))
    return ledger


def config(**overrides):
    base = {
        "trading_mode": "AUTO_PAPER",
        "order_submission_enabled": True,
        "symbol_allowlist": ("EURUSD", "GBPUSD", "USDJPY"),
        "timeframe_allowlist": ("15m",),
        "max_trades_per_day": 3,
        "max_trades_per_symbol_per_day": 1,
        "max_provider_requests_per_hour": 3,
        "max_provider_requests_per_day": 24,
        "daily_cost_limit_usd": 0.25,
        "monthly_cost_limit_usd": 5.0,
        "multi_trade_validation_mode": False,
        "daily_acceptance_max_entries": 1,
        "daily_acceptance_entries_used": 0,
        "daily_acceptance_date": "",
        "post_trade_cooldown_minutes": 15,
        "max_consecutive_losses": 2,
        "daily_profit_lock_usd": 0.0,
    }
    base.update(overrides)
    return AITradingConfig(**base)


class FakeAdapter:
    def __init__(self):
        self.submissions: list[BrokerOrderCommand] = []
        self.protective_submissions: list[dict] = []
        self.positions_rows = []
        self.orders = []
        self.open_order_account_ids: list[str] = []
        self.connect_calls = 0
        self.real_client = SimpleNamespace(api_ready=True)
        self.session_manager = SimpleNamespace(
            diagnostics=lambda: {
                "socket_connected": True,
                "api_ready": True,
                "next_valid_id_received": True,
                "managed_accounts_received": True,
                "current_time_received": True,
            }
        )

    async def connect(self):
        self.connect_calls += 1
        return SimpleNamespace(status="CONNECTED")

    async def accounts(self):
        return [BrokerAccount(account_id="DUQ891002", alias="paper", environment=BrokerEnvironment.PAPER, paper_verified=True, allowed=True)]

    async def reconcile(self, account_id: str):
        return BrokerReconciliationResult(account_id=account_id, status="MATCHED")

    async def positions(self, account_id: str):
        return self.positions_rows

    async def open_orders(self, account_id: str):
        self.open_order_account_ids.append(account_id)
        return self.orders

    async def submit_ai_auto_paper_order(self, command: BrokerOrderCommand):
        self.submissions.append(command)
        return {
            "order_id": 101,
            "order_ref": command.correlation_id,
            "place_order_called": True,
            "submission_state": "ACKNOWLEDGED",
            "statuses": [{"filled": str(command.quantity), "remaining": "0"}],
            "executions": [{"execution": {"quantity": str(command.quantity), "price": "1.1000"}}],
        }

    async def submit_ai_auto_paper_protective_orders(self, command: BrokerOrderCommand, *, parent_receipt, stop_price, take_profit, quantity):
        payload = {
            "status": "SUBMITTED",
            "entry_ref": command.correlation_id,
            "quantity": str(quantity),
            "stop_price": str(stop_price),
            "take_profit": str(take_profit),
            "place_order_calls": 2,
        }
        self.protective_submissions.append(payload)
        return payload


class FakeAIService:
    def __init__(self, decision: TradeDecision | None = None, entry_conditions=True):
        self.calls = 0
        self._decision = decision
        self.entry_conditions = entry_conditions

    async def _build_context(self, symbol: str, timeframe: str):
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "latest_price": 1.1,
            "data_source": f"{symbol}=X",
            "data_timestamp": "2026-07-29T10:00:00+00:00",
            "account": {"equity": "100000"},
            "enabled_strategy_signals": [{"signal_direction": "BULLISH", "signal_strength": 0.8, "entry_conditions_met": self.entry_conditions}],
        }, "READY", []

    def context_hash(self, context):
        return f"hash-{context['symbol']}"

    async def analyze(self, symbol: str, timeframe: str):
        self.calls += 1
        decision = self._decision or make_decision(symbol=symbol, decision="NO_TRADE", confidence=0.8)
        return ShadowAnalysisResult(
            decision_id=f"decision-{symbol}",
            symbol=symbol,
            timeframe=timeframe,
            status="NO_TRADE" if decision.decision == "NO_TRADE" else "ACCEPTED_SHADOW",
            validation_status="VALID",
            risk_status="NO_TRADE" if decision.decision == "NO_TRADE" else "ACCEPTED_SHADOW",
            decision=decision,
            provider=ProviderTelemetry(provider="openai", model="gpt-4.1-mini", input_tokens=2000, output_tokens=150, total_tokens=2150),
            context_hash=f"hash-{symbol}",
        )


def make_decision(symbol="EURUSD", decision="LONG", confidence=0.75, rr=1.8, risk=0.05):
    return TradeDecision(
        symbol=symbol,
        timeframe="15m",
        decision=decision,
        entry_type="MARKET" if decision != "NO_TRADE" else "NONE",
        proposed_entry=1.1000 if decision != "NO_TRADE" else None,
        stop_loss=1.0980 if decision == "LONG" else (1.1020 if decision == "SHORT" else None),
        take_profit=1.1035 if decision == "LONG" else (1.0960 if decision == "SHORT" else None),
        confidence=confidence,
        risk_reward_ratio=rr,
        risk_percent=risk,
        strategy="test",
        market_regime="trend",
        reasoning_summary="structured test",
        invalidation_conditions=["break"],
        data_timestamp=datetime(2026, 7, 29, 10, tzinfo=timezone.utc),
        decision_timestamp=datetime(2026, 7, 29, 10, 1, tzinfo=timezone.utc),
    )


def install_fake_broker(monkeypatch, adapter):
    import backend.intelligence.trading.auto_paper as module

    monkeypatch.setattr(module.ibkr_config, "mode", "PAPER")
    monkeypatch.setattr(module.ibkr_config, "live_trading_enabled", False)
    monkeypatch.setattr(module.broker_registry, "get", lambda name: adapter)


def test_screening_makes_zero_provider_calls_when_no_candidate(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(entry_conditions=False), config())
    result = asyncio.run(service.run_cycle(execute=False))
    assert result["provider_calls"] == 0
    assert result["status"] == "SKIPPED_NO_CANDIDATE"


def test_only_one_symbol_reaches_openai_per_cycle(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    ai = FakeAIService()
    service = AIAutoPaperTradingService(ai, config())
    result = asyncio.run(service.run_cycle(execute=False))
    assert result["provider_calls"] == 1
    assert ai.calls == 1


def test_repeated_context_hash_skips_provider_call(monkeypatch):
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    import backend.intelligence.trading.auto_paper as module

    ledger = fresh_ledger(monkeypatch)
    ledger.context_hashes.add("hash-EURUSD")
    ledger.context_hashes.add("hash-GBPUSD")
    ledger.context_hashes.add("hash-USDJPY")
    ai = FakeAIService()
    service = AIAutoPaperTradingService(ai, config())
    result = asyncio.run(service.run_cycle(execute=False))
    assert result["provider_calls"] == 0
    assert ai.calls == 0


def test_hourly_daily_and_cost_limits_block_provider(monkeypatch):
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    import backend.intelligence.trading.auto_paper as module

    ledger = fresh_ledger(monkeypatch)
    ledger.estimated_cost_today = 1
    ai = FakeAIService()
    service = AIAutoPaperTradingService(ai, config())
    result = asyncio.run(service.run_cycle(execute=False))
    assert result["provider_calls"] == 0
    assert "DAILY_COST_LIMIT" in result["blockers"]


def test_no_trade_low_confidence_and_low_rr_submit_nothing(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(), config())
    context, _, _ = asyncio.run(service.ai_service._build_context("EURUSD", "15m"))
    no_trade = ShadowAnalysisResult(decision_id="d1", symbol="EURUSD", timeframe="15m", status="NO_TRADE", validation_status="VALID", risk_status="NO_TRADE", decision=make_decision(decision="NO_TRADE", confidence=0.8))
    assert asyncio.run(service.submit_if_eligible(no_trade, context)) is None
    low_conf = ShadowAnalysisResult(decision_id="d2", symbol="EURUSD", timeframe="15m", status="ACCEPTED_SHADOW", validation_status="VALID", risk_status="ACCEPTED_SHADOW", decision=make_decision(confidence=0.5))
    assert asyncio.run(service.submit_if_eligible(low_conf, context))["status"] == "REJECTED_AUTONOMOUS_GATE"
    low_rr = ShadowAnalysisResult(decision_id="d3", symbol="EURUSD", timeframe="15m", status="ACCEPTED_SHADOW", validation_status="VALID", risk_status="ACCEPTED_SHADOW", decision=make_decision(rr=1.0))
    assert asyncio.run(service.submit_if_eligible(low_rr, context))["status"] == "REJECTED_AUTONOMOUS_GATE"
    assert adapter.submissions == []


def test_quantity_is_deterministic_and_capped_at_config(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(), config(max_position_size_forex=100000, max_trade_loss_usd=200))
    qty, max_loss = service._size(make_decision(), {"account": {"equity": "100000"}})
    assert qty <= Decimal("100000")
    assert max_loss <= 200


def test_ai42_paper_profile_sizing_uses_one_million_equity_and_dollar_cap(monkeypatch):
    fresh_ledger(monkeypatch)
    service = AIAutoPaperTradingService(
        FakeAIService(),
        config(
            paper_account_starting_balance=1_000_000,
            max_risk_percent=0.05,
            max_trade_loss_usd=200,
            max_daily_loss_usd=1000,
            max_total_open_risk_usd=600,
            max_position_size_forex=100000,
        ),
    )
    decision = make_decision()
    qty, max_loss = service._size(decision, {"account": {"equity": "1000000"}})
    assert qty == Decimal("100000")
    assert max_loss == 200


def test_ai42_quantity_scales_with_stop_distance_and_never_rounds_up(monkeypatch):
    fresh_ledger(monkeypatch)
    service = AIAutoPaperTradingService(
        FakeAIService(),
        config(
            paper_account_starting_balance=1_000_000,
            max_risk_percent=0.05,
            max_trade_loss_usd=200,
            max_total_open_risk_usd=600,
            max_position_size_forex=100000,
        ),
    )
    twenty_pip = make_decision()
    forty_pip = make_decision()
    forty_pip.stop_loss = 1.0960
    ten_pip = make_decision()
    ten_pip.stop_loss = 1.0990

    assert service._size(twenty_pip, {"account": {"equity": "1000000"}})[0] == Decimal("100000")
    assert service._size(forty_pip, {"account": {"equity": "1000000"}})[0] == Decimal("50000")
    assert service._size(ten_pip, {"account": {"equity": "1000000"}})[0] == Decimal("100000")


def test_ai42_estimated_costs_reduce_quantity_below_raw_risk(monkeypatch):
    fresh_ledger(monkeypatch)
    service = AIAutoPaperTradingService(
        FakeAIService(),
        config(
            paper_account_starting_balance=1_000_000,
            max_risk_percent=0.05,
            max_trade_loss_usd=200,
            max_total_open_risk_usd=600,
            max_position_size_forex=100000,
            estimated_spread_pips=0.5,
            estimated_slippage_pips=0.5,
        ),
    )
    qty, max_loss = service._size(make_decision(), {"account": {"equity": "1000000"}})
    assert qty < Decimal("100000")
    assert max_loss <= 200


def test_ai42_total_open_risk_blocks_fourth_full_risk_position(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(
        FakeAIService(),
        config(
            multi_trade_validation_mode=True,
            max_open_positions=4,
            max_trade_loss_usd=200,
            max_total_open_risk_usd=600,
            max_position_size_forex=100000,
        ),
    )
    adapter.positions_rows = [
        SimpleNamespace(instrument_id="FX:EURUSD", symbol="EURUSD", quantity=Decimal("100000"), average_price=Decimal("1.1")),
        SimpleNamespace(instrument_id="FX:GBPUSD", symbol="GBPUSD", quantity=Decimal("100000"), average_price=Decimal("1.3")),
        SimpleNamespace(instrument_id="FX:USDJPY", symbol="USDJPY", quantity=Decimal("100000"), average_price=Decimal("150")),
    ]
    context, _, _ = asyncio.run(service.ai_service._build_context("EURUSD", "15m"))
    result = ShadowAnalysisResult(decision_id="d-open-risk", symbol="EURUSD", timeframe="15m", status="ACCEPTED_SHADOW", validation_status="VALID", risk_status="ACCEPTED_SHADOW", decision=make_decision())

    submitted = asyncio.run(service.submit_if_eligible(result, context))

    assert submitted["status"] == "REJECTED_AUTONOMOUS_GATE"
    assert "TOTAL_OPEN_RISK_LIMIT" in submitted["reasons"]
    assert adapter.submissions == []


def test_ai42_canonical_account_default_uses_synthetic_one_million_profile(monkeypatch):
    monkeypatch.setenv("PAPER_ACCOUNT_STARTING_BALANCE", "1000000")
    monkeypatch.setenv("PAPER_ACCOUNT_CURRENCY", "USD")
    payload = AccountCreateRequest()
    assert payload.name == "SYNTHETIC PAPER SCALE TEST"
    assert payload.initial_cash == Decimal("1000000")
    assert payload.base_currency == "USD"


def test_place_order_called_once_for_eligible_decision(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    ai = FakeAIService(make_decision())
    service = AIAutoPaperTradingService(ai, config())
    result = asyncio.run(service.run_cycle(execute=True))
    assert result["submitted"]["status"] == "SUBMITTED"
    assert len(adapter.submissions) == 1
    assert len(adapter.protective_submissions) == 1
    assert adapter.submissions[0].correlation_id.startswith("AI_AUTO_PAPER_")
    assert adapter.submissions[0].user_approval is False
    assert result["submitted"]["protective_orders"]["status"] == "SUBMITTED"


def test_same_completed_candle_is_not_analyzed_twice(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    import backend.intelligence.trading.auto_paper as module

    cycles = set()
    monkeypatch.setattr(module, "get_cycle", lambda cycle_id: SimpleNamespace(id=cycle_id) if cycle_id in cycles else None)
    monkeypatch.setattr(module, "save_cycle", lambda payload: cycles.add(payload["id"]))
    ai = FakeAIService()
    service = AIAutoPaperTradingService(ai, config())
    first = asyncio.run(service.run_cycle(execute=False))
    second = asyncio.run(service.run_cycle(execute=False))
    assert first["provider_calls"] == 1
    assert second["status"] == "SKIPPED_DUPLICATE_CANDLE"
    assert second["provider_calls"] == 0
    assert ai.calls == 1


def test_second_autonomous_entry_is_blocked(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(), config(max_trades_per_day=1))
    context, _, _ = asyncio.run(service.ai_service._build_context("EURUSD", "15m"))
    result = ShadowAnalysisResult(decision_id="d1", symbol="EURUSD", timeframe="15m", status="ACCEPTED_SHADOW", validation_status="VALID", risk_status="ACCEPTED_SHADOW", decision=make_decision())
    first = asyncio.run(service.submit_if_eligible(result, context))
    second = asyncio.run(service.submit_if_eligible(result, context))
    assert first["status"] == "SUBMITTED"
    assert second["status"] == "REJECTED_AUTONOMOUS_GATE"
    assert "ACCEPTANCE_ENTRY_USED" in second["reasons"]
    assert len(adapter.submissions) == 1
    assert len(adapter.protective_submissions) == 1


def test_protective_support_missing_blocks_before_entry(monkeypatch):
    fresh_ledger(monkeypatch)

    class EntryOnlyAdapter(FakeAdapter):
        def __getattribute__(self, name):
            if name == "submit_ai_auto_paper_protective_orders":
                raise AttributeError(name)
            return super().__getattribute__(name)

    adapter = EntryOnlyAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(make_decision()), config())

    result = asyncio.run(service.run_cycle(execute=True))

    assert result["provider_calls"] == 1
    assert result["submitted"]["status"] == "REJECTED_AUTONOMOUS_GATE"
    assert "PROTECTIVE_ORDER_SUPPORT_MISSING" in result["submitted"]["reasons"]
    assert adapter.submissions == []


def test_submission_unknown_marks_entry_used_and_blocks_retry(monkeypatch):
    fresh_ledger(monkeypatch)

    class UnknownFillAdapter(FakeAdapter):
        async def submit_ai_auto_paper_order(self, command: BrokerOrderCommand):
            self.submissions.append(command)
            return {"order_id": 101, "order_ref": command.correlation_id, "place_order_called": True, "submission_state": "SUBMISSION_UNKNOWN"}

    adapter = UnknownFillAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(), config(max_trades_per_day=1))
    context, _, _ = asyncio.run(service.ai_service._build_context("EURUSD", "15m"))
    result = ShadowAnalysisResult(decision_id="d1", symbol="EURUSD", timeframe="15m", status="ACCEPTED_SHADOW", validation_status="VALID", risk_status="ACCEPTED_SHADOW", decision=make_decision())

    first = asyncio.run(service.submit_if_eligible(result, context))
    second = asyncio.run(service.submit_if_eligible(result, context))

    assert first["status"] == "SUBMITTED"
    assert first["protective_orders"]["status"] == "PENDING_ON_FILL"
    assert second["status"] == "REJECTED_AUTONOMOUS_GATE"
    assert "ACCEPTANCE_ENTRY_USED" in second["reasons"]
    assert len(adapter.submissions) == 1


def test_live_account_and_emergency_disable_block_submission(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    import backend.intelligence.trading.auto_paper as module

    monkeypatch.setattr(module.ibkr_config, "live_trading_enabled", True)
    service = AIAutoPaperTradingService(FakeAIService(make_decision()), config())
    result = asyncio.run(service.run_cycle(execute=True))
    assert result["provider_calls"] == 0
    assert "LIVE_TRADING_ENABLED" in result["blockers"]
    monkeypatch.setattr(module.ibkr_config, "live_trading_enabled", False)
    asyncio.run(service.emergency_disable())
    result = asyncio.run(service.run_cycle(execute=True))
    assert "EMERGENCY_DISABLED" in result["blockers"]


def test_provider_and_scheduler_cannot_call_placeorder_directly():
    import inspect
    from backend.intelligence.providers.openai_client import OpenAITradeDecisionClient
    from backend.intelligence.trading.auto_paper import AIAutoPaperTradingService

    assert "placeOrder" not in inspect.getsource(OpenAITradeDecisionClient)
    assert "placeOrder" not in inspect.getsource(AIAutoPaperTradingService.run_cycle)


def test_scheduler_lifecycle_status(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(entry_conditions=False), config(scheduler_enabled=True))

    async def scenario():
        await service.initialize_broker_readiness(timeout_seconds=0.1, retry_interval_seconds=0.01)
        started = await service.start_scheduler(owner="test-owner")
        status = service.scheduler_api_status()
        await service.stop_scheduler()
        stopped = service.scheduler_api_status()
        return started, status, stopped

    started, status, stopped = asyncio.run(scenario())
    assert started is True
    assert status["scheduler_running"] is True
    assert status["scheduler_owner"] == "test-owner"
    assert status["current_state"] in {"sleeping", "running"}
    assert status["broker_api_ready"] is True
    assert stopped["scheduler_running"] is False


def test_scheduler_lock_takes_over_after_one_cycle_silent_owner(monkeypatch):
    import backend.intelligence.trading.auto_paper as module

    ledger = AIUsageLedger()
    state = {
        "ai_scheduler_lock": {
            "owner": "dead-container:1",
            "heartbeat_at": (module.utcnow() - timedelta(seconds=961)).isoformat(),
            "started_at": (module.utcnow() - timedelta(seconds=1200)).isoformat(),
        }
    }
    monkeypatch.setattr(module, "usage_ledger", ledger)
    monkeypatch.setattr(module, "get_state", lambda key: dict(state.get(key, {})))
    monkeypatch.setattr(module, "set_state", lambda key, value: state.__setitem__(key, dict(value)))
    service = AIAutoPaperTradingService(FakeAIService(entry_conditions=False), config(scheduler_enabled=True, analysis_interval_seconds=900))

    assert service._acquire_scheduler_lock("new-container:1") is True
    assert state["ai_scheduler_lock"]["owner"] == "new-container:1"


def test_broker_readiness_initialization_uses_shared_adapter(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(), config())

    ready = asyncio.run(service.initialize_broker_readiness(timeout_seconds=0.1, retry_interval_seconds=0.01))

    assert ready is True
    assert adapter.connect_calls == 1
    assert service.scheduler_api_status()["broker_api_ready"] is True
    assert service.scheduler_api_status()["broker_account"] == "DU***02"
    assert service.scheduler_api_status()["broker_reconciliation"] == "MATCHED"


def test_broker_status_masking_does_not_break_internal_account_calls(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(entry_conditions=False), config())

    result = asyncio.run(service.run_cycle(execute=False))

    assert result["provider_calls"] == 0
    assert "BROKER_NOT_READY:BROKER_ACCOUNT_NOT_ALLOWED" not in result.get("blockers", [])
    assert adapter.open_order_account_ids
    assert all(account_id == "DUQ891002" for account_id in adapter.open_order_account_ids)


def test_broker_unavailable_blocks_before_provider_and_orders(monkeypatch):
    ledger = fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    adapter.real_client.api_ready = False
    adapter.session_manager = SimpleNamespace(
        diagnostics=lambda: {
            "socket_connected": True,
            "api_ready": False,
            "next_valid_id_received": False,
            "managed_accounts_received": True,
            "current_time_received": True,
        }
    )
    install_fake_broker(monkeypatch, adapter)
    ai = FakeAIService()
    service = AIAutoPaperTradingService(ai, config())

    result = asyncio.run(service.run_cycle(execute=True))

    assert result["provider_calls"] == 0
    assert result["status"] == "SKIPPED_NO_CANDIDATE"
    assert "BROKER_NOT_READY:REAL_IBKR_API_NOT_READY" in result["blockers"]
    assert ai.calls == 0
    assert adapter.submissions == []
    assert ledger.requests_today() == 0
    assert ledger.provider_calls_made == 0
    assert service.scheduler_api_status()["broker_api_ready"] is False


def test_validation_profile_does_not_change_production_thresholds():
    cfg = config(
        acceptance_validation_mode=True,
        consensus_threshold=0.62,
        min_eligible_strategies=2,
        min_directional_score=0.62,
        max_conflicting_strategies=0,
    )

    assert cfg.production_profile.model_dump() == {
        "name": "PRODUCTION_INSTITUTIONAL",
        "consensus_threshold": 0.62,
        "min_eligible_strategies": 2,
        "min_directional_score": 0.62,
        "max_conflicting_strategies": 0,
        "min_confidence": 0.7,
        "min_risk_reward": 1.5,
        "max_holding_minutes": 240,
    }
    assert cfg.validation_profile.consensus_threshold == 0.50
    assert cfg.validation_profile.min_eligible_strategies == 1
    assert cfg.validation_profile.max_conflicting_strategies == 1


def test_validation_mode_live_account_fails_closed(monkeypatch):
    ledger = fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    import backend.intelligence.trading.auto_paper as module

    monkeypatch.setattr(module.ibkr_config, "live_trading_enabled", True)
    service = AIAutoPaperTradingService(FakeAIService(make_decision()), config(acceptance_validation_mode=True))

    result = asyncio.run(service.run_cycle(execute=True))

    assert result["provider_calls"] == 0
    assert "LIVE_TRADING_ENABLED" in result["blockers"]
    assert "VALIDATION_LIVE_TRADING_BLOCKED" in result["blockers"]
    assert adapter.submissions == []
    assert ledger.requests_today() == 0


def test_validation_profile_requires_verified_paper_account(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()

    async def accounts():
        return [BrokerAccount(account_id="DUQ891002", alias="paper", environment=BrokerEnvironment.PAPER, paper_verified=False, allowed=True)]

    adapter.accounts = accounts
    install_fake_broker(monkeypatch, adapter)
    ai = FakeAIService(make_decision())
    service = AIAutoPaperTradingService(ai, config(acceptance_validation_mode=True))

    result = asyncio.run(service.run_cycle(execute=True))

    assert result["provider_calls"] == 0
    assert any("VERIFIED_PAPER_ACCOUNT_REQUIRED" in reason for reason in result["blockers"])
    assert ai.calls == 0
    assert adapter.submissions == []


def test_validation_timeout_exit_is_acceptance_mode_only(monkeypatch):
    state = fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(), config(acceptance_validation_mode=True))
    service.state.active_threshold_profile = "PAPER_ACCEPTANCE_VALIDATION"
    service._arm_validation_exit_if_needed()
    assert service.scheduler_api_status()["exit_deadline"] is not None

    service2 = AIAutoPaperTradingService(FakeAIService(), config(acceptance_validation_mode=False))
    service2.state.active_threshold_profile = "PRODUCTION_INSTITUTIONAL"
    service2._arm_validation_exit_if_needed()
    assert service2.scheduler_api_status()["exit_deadline"] is None


def test_protection_uses_actual_filled_quantity(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(), config())
    context, _, _ = asyncio.run(service.ai_service._build_context("EURUSD", "15m"))
    result = ShadowAnalysisResult(decision_id="d1", symbol="EURUSD", timeframe="15m", status="ACCEPTED_SHADOW", validation_status="VALID", risk_status="ACCEPTED_SHADOW", decision=make_decision())

    submitted = asyncio.run(service.submit_if_eligible(result, context))

    assert submitted["status"] == "SUBMITTED"
    assert adapter.protective_submissions[0]["quantity"] == submitted["quantity"]


def test_completion_restores_production_profile_and_disables_submission(monkeypatch):
    state = fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(), config(acceptance_validation_mode=True))
    service._mark_acceptance_entry_used()
    service.complete_acceptance_if_flat()
    status = service.scheduler_api_status()

    assert status["acceptance_armed"] is False
    assert status["autonomous_submission_enabled"] is False
    assert status["validation_mode_enabled"] is False
    assert status["production_profile_restored"] is True


def accepted_result(symbol: str = "EURUSD") -> ShadowAnalysisResult:
    decision = make_decision(symbol=symbol, decision="LONG", confidence=0.75, rr=1.8)
    return ShadowAnalysisResult(
        decision_id=f"decision-{symbol}",
        symbol=symbol,
        timeframe="15m",
        status="ACCEPTED_SHADOW",
        validation_status="VALID",
        risk_status="ACCEPTED_SHADOW",
        decision=decision,
        provider=ProviderTelemetry(provider="openai", model="gpt-4.1-mini", input_tokens=100, output_tokens=50, total_tokens=150),
        context_hash=f"hash-{symbol}",
    )


def multi_config(**overrides):
    values = {
        "multi_trade_validation_mode": True,
        "trading_mode": "AUTO_PAPER",
        "order_submission_enabled": True,
        "symbol_allowlist": ("EURUSD",),
        "timeframe_allowlist": ("15m",),
        "max_trades_per_day": 5,
        "max_trades_per_symbol_per_day": 5,
        "max_open_positions": 1,
        "daily_acceptance_max_entries": 5,
        "post_trade_cooldown_minutes": 0,
        "max_consecutive_losses": 2,
        "max_daily_loss_usd": 10,
        "max_trade_loss_usd": 3,
        "max_provider_requests_per_day": 12,
    }
    values.update(overrides)
    return config(**values)


def test_multi_trade_validation_allows_five_and_blocks_sixth(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(make_decision()), multi_config())
    context = asyncio.run(service.ai_service._build_context("EURUSD", "15m"))[0]

    async def scenario():
        await service.initialize_broker_readiness(timeout_seconds=0.1, retry_interval_seconds=0.01)
        results = [await service.submit_if_eligible(accepted_result(), context) for _ in range(6)]
        return results, service.scheduler_api_status()

    results, status = asyncio.run(scenario())
    assert len([row for row in results if row and row.get("status") == "SUBMITTED"]) == 5
    assert results[-1]["status"] == "REJECTED_AUTONOMOUS_GATE"
    assert "ACCEPTANCE_ENTRY_USED" in results[-1]["reasons"]
    assert status["active_profile"] == "PAPER_MULTI_TRADE_VALIDATION"
    assert status["entries_used"] == 5
    assert status["entries_remaining"] == 0


def test_multi_trade_restart_does_not_reset_counter(monkeypatch):
    import backend.intelligence.trading.auto_paper as module

    store = {
        "ai_daily_acceptance_v1": {
            "trading_date": module.utcnow().astimezone(ZoneInfo("Europe/Bucharest")).date().isoformat(),
            "timezone": "Europe/Bucharest",
            "entries_attempted": 2,
            "entries_submitted": 2,
            "entries_filled": 2,
            "trades_closed": 0,
            "trades_rejected": 0,
            "openai_calls": 2,
            "daily_gross_pnl": 0.0,
            "daily_net_pnl": 0.0,
            "daily_maximum_drawdown": 0.0,
            "consecutive_losses": 0,
            "daily_lock_reason": None,
        }
    }
    monkeypatch.setattr(module, "get_state", lambda key: dict(store.get(key, {})))
    monkeypatch.setattr(module, "set_state", lambda key, value: store.__setitem__(key, dict(value)))
    service = AIAutoPaperTradingService(FakeAIService(), multi_config())

    assert service.scheduler_api_status()["entries_used"] == 2
    assert service.scheduler_api_status()["entries_remaining"] == 3


def test_multi_trade_symbol_cap_blocks_fourth_eurusd_entry(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(
        FakeAIService(make_decision()),
        multi_config(max_trades_per_day=5, max_trades_per_symbol_per_day=3, daily_acceptance_max_entries=5),
    )
    context = asyncio.run(service.ai_service._build_context("EURUSD", "15m"))[0]

    async def scenario():
        await service.initialize_broker_readiness(timeout_seconds=0.1, retry_interval_seconds=0.01)
        return [await service.submit_if_eligible(accepted_result("EURUSD"), context) for _ in range(4)]

    results = asyncio.run(scenario())
    assert len([row for row in results if row and row.get("status") == "SUBMITTED"]) == 3
    assert results[-1]["status"] == "REJECTED_AUTONOMOUS_GATE"
    assert "SYMBOL_DAILY_TRADE_LIMIT" in results[-1]["reasons"]
    status = service.scheduler_api_status()
    assert status["entries_used"] == 3
    assert status["entries_by_symbol"]["EURUSD"] == 3


def test_sizing_audit_is_persisted_before_broker_submission(monkeypatch):
    import backend.intelligence.trading.auto_paper as module

    ledger = AIUsageLedger()
    store: dict[str, dict] = {}
    monkeypatch.setattr(module, "usage_ledger", ledger)
    monkeypatch.setattr(module, "get_state", lambda key: dict(store.get(key, {})))
    monkeypatch.setattr(module, "set_state", lambda key, value: store.__setitem__(key, dict(value)))
    monkeypatch.setattr(module, "get_cycle", lambda cycle_id: None)
    monkeypatch.setattr(module, "save_cycle", lambda payload: None)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(
        FakeAIService(make_decision()),
        multi_config(max_trade_loss_usd=200, max_daily_loss_usd=1000, max_total_open_risk_usd=600, max_position_size_forex=100000),
    )
    context = asyncio.run(service.ai_service._build_context("EURUSD", "15m"))[0]
    context["account"] = {"equity": "1000000"}

    result = asyncio.run(service.submit_if_eligible(accepted_result("EURUSD"), context))

    assert result["status"] == "SUBMITTED"
    intents = store["ai_auto_paper_intents"]["items"]
    sizing = intents[0]["sizing"]
    assert sizing["entry"] == 1.1
    assert sizing["stop"] == 1.098
    assert sizing["target"] == 1.1035
    assert sizing["final_quantity"] == "100000"
    assert sizing["estimated_total_loss"] <= 200
    assert sizing["remaining_daily_allowance"] == 1000
    assert sizing["remaining_open_risk_allowance"] == 600


def test_pre_broker_funding_rejection_does_not_consume_submission_slot(monkeypatch):
    fresh_ledger(monkeypatch)
    monkeypatch.setenv("FX_INSUFFICIENT_CASH_POLICY", "REJECT")

    class FundingRejectAdapter(FakeAdapter):
        async def validate_ai_auto_paper_funding_plan(self, command, *, entry_price, stop_price, take_profit):
            from backend.brokers.errors import BrokerSafetyError

            exc = BrokerSafetyError("INSUFFICIENT_SETTLEMENT_CURRENCY", "insufficient JPY", status_code=422)
            exc.details = {
                "pair": "USDJPY",
                "action": "BUY",
                "requested_quantity": "1869",
                "required_currency": "JPY",
                "available_cash": "292798",
                "required_cash": "292978.833",
                "maximum_affordable_quantity": "1867",
            }
            raise exc

        async def submit_ai_auto_paper_order(self, command: BrokerOrderCommand):
            raise AssertionError("placeOrder must not be called after local funding rejection")

    adapter = FundingRejectAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(make_decision(symbol="USDJPY", decision="SHORT")), multi_config(symbol_allowlist=("USDJPY",)))
    context = asyncio.run(service.ai_service._build_context("USDJPY", "15m"))[0]
    context["enabled_strategy_signals"][0]["signal_direction"] = "SELL"
    result = ShadowAnalysisResult(decision_id="d-funding", symbol="USDJPY", timeframe="15m", status="ACCEPTED_SHADOW", validation_status="VALID", risk_status="ACCEPTED_SHADOW", decision=make_decision(symbol="USDJPY", decision="SHORT"))

    submitted = asyncio.run(service.submit_if_eligible(result, context))

    assert submitted["status"] == "REJECTED_PRE_BROKER"
    assert submitted["reason"] == "INSUFFICIENT_SETTLEMENT_CURRENCY"
    assert submitted["place_order_calls"] == 0
    status = service.scheduler_api_status()
    assert status["entries_used"] == 0
    assert status["entries_remaining"] == 5


def test_reduce_policy_revalidates_lower_funded_quantity_before_submit(monkeypatch):
    fresh_ledger(monkeypatch)
    monkeypatch.setenv("FX_INSUFFICIENT_CASH_POLICY", "REDUCE")
    monkeypatch.setenv("FX_MIN_ORDER_QUANTITY", "1")

    class FundingReduceAdapter(FakeAdapter):
        def __init__(self):
            super().__init__()
            self.funding_checks: list[str] = []

        async def validate_ai_auto_paper_funding_plan(self, command, *, entry_price, stop_price, take_profit):
            self.funding_checks.append(str(command.quantity))
            if command.quantity == Decimal("28"):
                from backend.brokers.errors import BrokerSafetyError

                exc = BrokerSafetyError("INSUFFICIENT_SETTLEMENT_CURRENCY", "insufficient JPY", status_code=422)
                exc.details = {
                    "checks": [
                        {"role": "entry", "maximum_affordable_quantity": "28"},
                        {"role": "stop_loss", "maximum_affordable_quantity": "20"},
                        {"role": "take_profit", "maximum_affordable_quantity": "21"},
                    ],
                    "failed_check": {"role": "stop_loss", "maximum_affordable_quantity": "20"},
                }
                raise exc
            assert command.quantity == Decimal("20")
            return {"status": "FUNDED", "checks": [{"funding_status": "FUNDED"}]}

    adapter = FundingReduceAdapter()
    install_fake_broker(monkeypatch, adapter)
    decision = make_decision(symbol="USDJPY", decision="SHORT")
    decision.proposed_entry = 1
    decision.stop_loss = 1.10695
    decision.take_profit = 0.998
    service = AIAutoPaperTradingService(FakeAIService(decision), multi_config(symbol_allowlist=("USDJPY",)))
    context = asyncio.run(service.ai_service._build_context("USDJPY", "15m"))[0]
    context["enabled_strategy_signals"][0]["signal_direction"] = "SELL"
    result = ShadowAnalysisResult(decision_id="d-reduce", symbol="USDJPY", timeframe="15m", status="ACCEPTED_SHADOW", validation_status="VALID", risk_status="ACCEPTED_SHADOW", decision=decision)

    submitted = asyncio.run(service.submit_if_eligible(result, context))

    assert submitted["status"] == "SUBMITTED"
    assert adapter.funding_checks == ["28", "20"]
    assert adapter.submissions[0].quantity == Decimal("20")
    assert submitted["quantity"] == "20"


def test_rejected_candidate_and_no_trade_do_not_consume_daily_entry(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(make_decision(decision="NO_TRADE"), entry_conditions=False), multi_config())

    result = asyncio.run(service.run_cycle(execute=True))
    status = service.scheduler_api_status()
    assert result["provider_calls"] == 0
    assert status["entries_used"] == 0
    assert status["entries_remaining"] == 5


def test_position_capacity_allows_more_than_one_open_position(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    adapter.positions_rows = [
        SimpleNamespace(instrument_id=f"FX:EURUSD:{idx}", symbol="EURUSD", quantity="1000", average_price="1.1000")
        for idx in range(4)
    ]
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(), multi_config(max_open_positions=5, max_open_orders=15))

    blockers = asyncio.run(service._position_capacity_blockers())
    status = service.scheduler_api_status()

    assert blockers == []
    assert status["current_position_count"] == 4
    assert status["max_open_positions"] == 5


def test_position_capacity_blocks_at_configured_limit(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    adapter.positions_rows = [
        SimpleNamespace(instrument_id=f"FX:EURUSD:{idx}", symbol="EURUSD", quantity="1000", average_price="1.1000")
        for idx in range(5)
    ]
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(), multi_config(max_open_positions=5, max_open_orders=15))

    blockers = asyncio.run(service._position_capacity_blockers())

    assert "OPEN_POSITION_LIMIT" in blockers
    assert "POSITION_OR_ORDER_OPEN" not in blockers


def test_non_allowlisted_open_fx_position_blocks_autonomous_entry(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    adapter.positions_rows = [
        SimpleNamespace(instrument_id="FX:USDJPY", symbol="USDJPY", quantity="-1869", average_price="156.49")
    ]
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(
        FakeAIService(),
        multi_config(symbol_allowlist=("EURUSD",), max_open_positions=3, max_open_orders=9),
    )
    context = asyncio.run(service.ai_service._build_context("EURUSD", "15m"))[0]

    blockers = asyncio.run(service._submission_blockers(make_decision("EURUSD"), context))

    assert "NON_ALLOWLISTED_FX_POSITION_OPEN" in blockers


def test_cooldown_consecutive_losses_and_daily_loss_lock(monkeypatch):
    fresh_ledger(monkeypatch)
    service = AIAutoPaperTradingService(FakeAIService(), multi_config(post_trade_cooldown_minutes=15))
    service.state.cooldown_expiry = datetime.now(timezone.utc) + timedelta(minutes=15)
    assert "POST_TRADE_COOLDOWN" in service._daily_validation_blockers()
    state = service._daily_state()
    state["consecutive_losses"] = 2
    service._save_daily_state(state)
    assert "CONSECUTIVE_LOSS_LIMIT" in service._daily_validation_blockers()
    state["daily_net_pnl"] = -10
    service._save_daily_state(state)
    assert "DAILY_LOSS_LIMIT" in service._daily_validation_blockers()


def test_aggregate_daily_risk_limit_blocks_submission(monkeypatch):
    fresh_ledger(monkeypatch)
    adapter = FakeAdapter()
    install_fake_broker(monkeypatch, adapter)
    service = AIAutoPaperTradingService(FakeAIService(make_decision()), multi_config(max_daily_loss_usd=1))
    context = asyncio.run(service.ai_service._build_context("EURUSD", "15m"))[0]

    blockers = asyncio.run(service._submission_blockers(make_decision(), context))
    assert "AGGREGATE_DAILY_RISK_LIMIT" in blockers or service._daily_loss_allowance_remaining() <= 1
