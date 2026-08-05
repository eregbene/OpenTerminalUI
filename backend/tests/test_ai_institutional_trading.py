from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from backend.intelligence.trading.config import AITradingConfig
from backend.intelligence.trading.consensus import build_consensus
from backend.intelligence.trading.market_context import build_market_context
from backend.intelligence.trading.memory import performance_metrics, persist_trade_memory, persist_trade_review
from backend.intelligence.trading.models import ProviderTelemetry, TradeDecision
from backend.intelligence.trading.service import AITradingService
from backend.intelligence.trading.strategies import StrategyOutput, evaluate_strategies
from backend.intelligence.trading.usage import AIUsageLedger


def candles(count: int = 220, *, drift: float = 0.0001) -> list[dict[str, float]]:
    base_ts = int(datetime(2026, 7, 29, 8, tzinfo=timezone.utc).timestamp())
    price = 1.08
    out = []
    for idx in range(count):
        price += drift
        high = price + 0.00035
        low = price - 0.00025
        out.append({"t": base_ts + idx * 900, "o": price - drift / 2, "h": high, "l": low, "c": price, "v": 1000 + idx})
    return out


def config(**overrides):
    values = {
        "trading_mode": "AUTO_PAPER",
        "order_submission_enabled": False,
        "symbol_allowlist": ("EURUSD",),
        "timeframe_allowlist": ("15m",),
        "consensus_threshold": 0.8,
    }
    values.update(overrides)
    return AITradingConfig(**values)


def fresh_state(monkeypatch):
    import backend.intelligence.trading.memory as memory
    import backend.intelligence.trading.service as service

    state: dict[str, dict] = {}
    ledger = AIUsageLedger()
    monkeypatch.setattr(memory, "get_state", lambda key: dict(state.get(key, {})))
    monkeypatch.setattr(memory, "set_state", lambda key, value: state.__setitem__(key, dict(value)))
    monkeypatch.setattr(service, "usage_ledger", ledger)
    return state, ledger


class FakeProvider:
    def __init__(self):
        self.calls = 0

    async def generate_trade_decision(self, context):
        self.calls += 1
        decision = TradeDecision(
            symbol=context["symbol"],
            timeframe=context["timeframe"],
            decision="LONG",
            entry_type="MARKET",
            proposed_entry=1.1,
            stop_loss=1.098,
            take_profit=1.104,
            confidence=0.8,
            risk_reward_ratio=2,
            risk_percent=0.05,
            strategy="institutional_consensus",
            market_regime="TRENDING",
            reasoning_summary="CIO accepts deterministic consensus.",
            invalidation_conditions=["break below stop"],
            data_timestamp=datetime.fromisoformat(context["data_timestamp"]),
            decision_timestamp=datetime(2026, 7, 29, 12, tzinfo=timezone.utc),
        )
        telemetry = ProviderTelemetry(provider="openai", model="test", input_tokens=100, output_tokens=50, total_tokens=150)
        return decision, telemetry, decision.model_dump_json()


def test_market_context_generation_contains_ai3_fields():
    context = build_market_context(candles(), symbol="EURUSD", timeframe="15m", spread=0.8)
    payload = context.model_dump()
    assert payload["market"]["symbol"] == "EURUSD"
    assert payload["trend"]["ema20"] is not None
    assert {"HH", "HL", "LH", "LL", "BOS", "CHOCH"} <= set(payload["market_structure"])
    assert {"rsi", "stochastic_rsi", "macd", "mfi"} <= set(payload["momentum"])
    assert "nearest_support" in payload["support_resistance"]
    assert payload["news_risk"]["status"] == "placeholder"


def test_strategy_outputs_and_consensus_engine():
    context = build_market_context(candles(drift=0.0002), symbol="EURUSD", timeframe="15m", spread=0.8, higher_timeframe_trend="bullish")
    outputs = evaluate_strategies(context)
    consensus = build_consensus(outputs)
    assert len(outputs) == 10
    assert all(row.strategy for row in outputs)
    assert consensus.recommended_direction in {"LONG", "SHORT", "NO_TRADE"}
    assert 0 <= consensus.agreement_score <= 1


def test_multi_timeframe_aggregation(monkeypatch):
    service = AITradingService(provider=FakeProvider(), config=config())

    async def fake_chart(symbol, interval, range_str):
        return {"candles": candles(80, drift=0.0001 if interval != "5m" else -0.00005), "source_symbol": f"{symbol}=X", "current_rate": 1.1}

    import backend.intelligence.trading.service as module

    monkeypatch.setattr(module.forex_service, "get_pair_chart", fake_chart)
    out = asyncio.run(service._multi_timeframe_context("EURUSD", 0.8))
    assert set(out) == {"4h", "1h", "15m", "5m"}
    assert out["4h"]["status"] == "READY"


def test_trade_memory_review_and_performance(monkeypatch):
    fresh_state(monkeypatch)
    trade = persist_trade_memory({"id": "t1", "strategy": "EMA Trend", "session": "London", "weekday": "Wednesday", "volatility": "normal", "market_regime": "TRENDING", "pnl": 12, "reward": 2})
    review = persist_trade_review(trade)
    metrics = performance_metrics([trade])
    assert trade["id"] == "t1"
    assert review["why_winner_loser"] == "winner"
    assert metrics["win_rate"] == 1
    assert metrics["win_rate_by_strategy"]["EMA Trend"] == 1


def test_openai_skipped_when_consensus_is_weak(monkeypatch):
    fresh_state(monkeypatch)
    provider = FakeProvider()
    service = AITradingService(provider=provider, config=config(consensus_threshold=0.9))

    async def fake_context(symbol, timeframe):
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "data_source": "test",
            "data_timestamp": "2026-07-29T12:00:00+00:00",
            "strategy_consensus": {"agreement_score": 0.2, "recommended_direction": "LONG"},
        }, "READY", []

    service._build_context = fake_context  # type: ignore[method-assign]
    result = asyncio.run(service.analyze("EURUSD", "15m"))
    assert result.status == "SKIPPED_WEAK_CONSENSUS"
    assert provider.calls == 0


def test_strong_consensus_makes_only_one_provider_call(monkeypatch):
    state, ledger = fresh_state(monkeypatch)
    provider = FakeProvider()
    service = AITradingService(provider=provider, config=config(consensus_threshold=0.5))

    async def fake_context(symbol, timeframe):
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "data_source": "test",
            "data_timestamp": "2026-07-29T12:00:00+00:00",
            "strategy_consensus": {"agreement_score": 0.75, "recommended_direction": "LONG"},
            "market_context": {"market": {"session": "London"}, "market_regime": "TRENDING"},
            "strategy_outputs": [],
            "risk_summary": {},
        }, "READY", []

    import backend.intelligence.trading.service as module

    monkeypatch.setattr(module, "save_decision", lambda payload: state.__setitem__("decision", payload))
    service._build_context = fake_context  # type: ignore[method-assign]
    result = asyncio.run(service.analyze("EURUSD", "15m"))
    assert result.provider is not None
    assert provider.calls == 1
    assert ledger.requests_today() == 1


def test_validation_profile_tolerates_one_low_confidence_non_structural_conflict():
    service = AITradingService(provider=FakeProvider(), config=config(acceptance_validation_mode=True))
    consensus = {"agreement_score": 0.55, "recommended_direction": "LONG", "bull_score": 0.62, "bear_score": 0.30, "eligible_strategy_count": 2, "conflicting_strategies": ["VWAP"]}
    outputs = [
        StrategyOutput("Support Resistance Bounce", "LONG", 0.62, 1.8, 1.1, 1.098, 1.104, "long"),
        StrategyOutput("VWAP", "SHORT", 0.30, 1.8, 1.1, 1.102, 1.096, "small conflict"),
    ]
    mtf = {tf: {"status": "READY"} for tf in ("4h", "1h", "15m", "5m")}
    classification = service._classify_conflicts(consensus, outputs, mtf)
    eligibility = service._consensus_eligibility(consensus, mtf, service.config.validation_profile.model_dump(), classification)

    assert classification["tolerated"] is True
    assert eligibility["eligible"] is True
    assert eligibility["profile"] == "PAPER_ACCEPTANCE_VALIDATION"


def test_higher_timeframe_or_structural_conflict_is_rejected_in_validation_profile():
    service = AITradingService(provider=FakeProvider(), config=config(acceptance_validation_mode=True))
    consensus = {"agreement_score": 0.65, "recommended_direction": "LONG", "bull_score": 0.68, "bear_score": 0.20, "eligible_strategy_count": 2, "conflicting_strategies": ["Market Structure"]}
    outputs = [
        StrategyOutput("EMA Trend", "LONG", 0.68, 1.8, 1.1, 1.098, 1.104, "long"),
        StrategyOutput("Market Structure", "SHORT", 0.20, 1.8, 1.1, 1.102, 1.096, "structural conflict"),
    ]
    mtf = {tf: {"status": "READY"} for tf in ("4h", "1h", "15m", "5m")}
    classification = service._classify_conflicts(consensus, outputs, mtf)
    eligibility = service._consensus_eligibility(consensus, mtf, service.config.validation_profile.model_dump(), classification)

    assert classification["tolerated"] is False
    assert "STRUCTURAL_OR_BREAKOUT_CONFLICT" in eligibility["reasons"]


def test_trade_memory_records_threshold_profile_and_production_comparison(monkeypatch):
    state, _ = fresh_state(monkeypatch)
    provider = FakeProvider()
    service = AITradingService(provider=provider, config=config(acceptance_validation_mode=True, consensus_threshold=0.62))

    async def fake_context(symbol, timeframe):
        return {
            "symbol": symbol,
            "timeframe": timeframe,
            "data_source": "test",
            "data_timestamp": "2026-07-29T12:00:00+00:00",
            "strategy_consensus": {"agreement_score": 0.55, "recommended_direction": "LONG", "bull_score": 0.62, "bear_score": 0.0, "eligible_strategy_count": 1, "conflicting_strategies": []},
            "consensus_eligibility": {"eligible": True, "reasons": [], "profile": "PAPER_ACCEPTANCE_VALIDATION"},
            "market_context": {"market": {"session": "London"}, "market_regime": "PULLBACK"},
            "strategy_outputs": [],
            "threshold_profile": service.config.validation_profile.model_dump(),
            "active_threshold_profile": "PAPER_ACCEPTANCE_VALIDATION",
            "production_thresholds": service.config.production_profile.model_dump(),
            "validation_thresholds": service.config.validation_profile.model_dump(),
            "risk_summary": {},
        }, "READY", []

    import backend.intelligence.trading.service as module

    monkeypatch.setattr(module, "save_decision", lambda payload: state.__setitem__("decision", payload))
    service._build_context = fake_context  # type: ignore[method-assign]
    result = asyncio.run(service.analyze("EURUSD", "15m"))
    memory = state["ai_trade_memory_v1"]["items"][0]

    assert result.status == "ACCEPTED_SHADOW"
    assert memory["threshold_profile"]["name"] == "PAPER_ACCEPTANCE_VALIDATION"
    assert memory["used_validation_thresholds"] is True
    assert memory["would_pass_production_thresholds"] is False
