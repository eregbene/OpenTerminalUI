from __future__ import annotations

from datetime import datetime, timezone
import asyncio

from backend.intelligence.trading.config import AITradingConfig
from backend.intelligence.trading.models import ProviderTelemetry, TradeDecision
from backend.intelligence.trading.service import AITradingService


class FakeProvider:
    def __init__(self, decision: TradeDecision | None = None) -> None:
        self.calls = 0
        self.decision = decision

    async def generate_trade_decision(self, context):
        self.calls += 1
        return self.decision, ProviderTelemetry(provider="openai", model="test", request_id="req", total_tokens=10), self.decision.model_dump_json() if self.decision else None


def _decision(**kwargs) -> TradeDecision:
    now = datetime.now(timezone.utc)
    payload = {
        "symbol": "EURUSD",
        "timeframe": "15m",
        "decision": "LONG",
        "entry_type": "MARKET",
        "proposed_entry": 1.1,
        "stop_loss": 1.09,
        "take_profit": 1.12,
        "confidence": 0.8,
        "risk_reward_ratio": 2.0,
        "risk_percent": 0.1,
        "strategy": "session_breakout",
        "market_regime": "bullish_expansion",
        "reasoning_summary": "Breakout.",
        "invalidation_conditions": ["close back inside range"],
        "data_timestamp": now,
        "decision_timestamp": now,
    }
    payload.update(kwargs)
    return TradeDecision.model_validate(payload)


def test_stale_data_skip_before_model_call(monkeypatch) -> None:
    provider = FakeProvider()
    service = AITradingService(provider=provider, config=AITradingConfig())
    monkeypatch.setattr(service, "_build_context", lambda symbol, timeframe: _async(({"symbol": symbol, "timeframe": timeframe}, "BLOCKED", ["STALE_DATA"])))
    result = asyncio.run(service.analyze("EURUSD", "15m"))
    assert result.status == "SKIPPED_STALE_DATA"
    assert provider.calls == 0


def test_symbol_allowlist() -> None:
    service = AITradingService(provider=FakeProvider(), config=AITradingConfig(symbol_allowlist=("EURUSD",)))
    result = asyncio.run(service.analyze("GBPUSD", "15m"))
    assert result.status == "REJECTED_INVALID"
    assert "SYMBOL_NOT_ALLOWLISTED" in result.rejection_reasons


def test_risk_engine_rejection(monkeypatch) -> None:
    decision = _decision(confidence=0.4)
    service = AITradingService(provider=FakeProvider(decision), config=AITradingConfig(min_confidence=0.65))
    ctx = {"symbol": "EURUSD", "timeframe": "15m", "data_timestamp": decision.data_timestamp.isoformat(), "data_source": "test"}
    monkeypatch.setattr(service, "_build_context", lambda symbol, timeframe: _async((ctx, "READY", [])))
    result = asyncio.run(service.analyze("EURUSD", "15m"))
    assert result.status == "REJECTED_INVALID"
    assert "CONFIDENCE_TOO_LOW" in result.rejection_reasons


def test_decision_and_shadow_trade_persistence(monkeypatch) -> None:
    decision = _decision()
    service = AITradingService(provider=FakeProvider(decision), config=AITradingConfig())
    ctx = {"symbol": "EURUSD", "timeframe": "15m", "data_timestamp": decision.data_timestamp.isoformat(), "data_source": "test"}
    monkeypatch.setattr(service, "_build_context", lambda symbol, timeframe: _async((ctx, "READY", [])))
    result = asyncio.run(service.analyze("EURUSD", "15m"))
    assert result.status == "ACCEPTED_SHADOW"
    assert result.context_hash
    assert result.shadow_trade_created is True
    assert service.decision(result.decision_id)["context_hash"] == result.context_hash


def test_shadow_mode_cannot_call_place_order(monkeypatch) -> None:
    calls = {"place_order": 0}

    def fake_place_order(*args, **kwargs):
        calls["place_order"] += 1

    monkeypatch.setattr("backend.brokers.ibkr.client.RealTwsReadOnlyClient.placeOrder", fake_place_order, raising=False)
    decision = _decision(decision="NO_TRADE", entry_type="NONE", proposed_entry=None, stop_loss=None, take_profit=None, risk_reward_ratio=0, risk_percent=0)
    service = AITradingService(provider=FakeProvider(decision), config=AITradingConfig())
    ctx = {"symbol": "EURUSD", "timeframe": "15m", "data_timestamp": decision.data_timestamp.isoformat(), "data_source": "test"}
    monkeypatch.setattr(service, "_build_context", lambda symbol, timeframe: _async((ctx, "READY", [])))
    asyncio.run(service.analyze("EURUSD", "15m"))
    assert calls["place_order"] == 0


async def _async(value):
    return value
