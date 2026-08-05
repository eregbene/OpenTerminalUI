from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.economic_intelligence import calendar_guard, macro_context, news_guard
from backend.economic_intelligence.config import economic_intelligence_config

CFG = economic_intelligence_config()
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def _event(*, currency="USD", impact="high", minutes_from_now, central_bank=False, name="Non-Farm Payrolls"):
    return {"currency": currency, "impact": impact, "scheduled_at_utc": (NOW + timedelta(minutes=minutes_from_now)).isoformat(), "is_central_bank_event": central_bank, "raw_name": name}


def test_high_impact_event_blocks_new_entry_before_release():
    events = [_event(minutes_from_now=10)]
    result = calendar_guard.evaluate(["USD"], NOW, events, CFG)
    assert result["decision"] == "BLOCK"
    assert any("HIGH_IMPACT_EVENT_PRE_BLOCK" in reason for reason in result["reason_codes"])


def test_high_impact_event_delays_after_release():
    events = [_event(minutes_from_now=-5)]
    result = calendar_guard.evaluate(["USD"], NOW, events, CFG)
    assert result["decision"] == "DELAY"


def test_medium_impact_event_reduces_size_before_release():
    events = [_event(minutes_from_now=5, impact="medium")]
    result = calendar_guard.evaluate(["USD"], NOW, events, CFG)
    assert result["decision"] == "REDUCE_SIZE"
    assert result["size_multiplier"] < 1.0


def test_low_impact_event_normally_allows():
    events = [_event(minutes_from_now=5, impact="low")]
    result = calendar_guard.evaluate(["USD"], NOW, events, CFG)
    assert result["decision"] == "ALLOW"


def test_low_impact_event_far_in_future_allows():
    events = [_event(minutes_from_now=600, impact="low")]
    result = calendar_guard.evaluate(["USD"], NOW, events, CFG)
    assert result["decision"] == "ALLOW"


def test_central_bank_speech_produces_configured_protection():
    events = [_event(minutes_from_now=30, central_bank=True, name="FOMC Press Conference")]
    result = calendar_guard.evaluate(["USD"], NOW, events, CFG)
    assert result["decision"] == "BLOCK"
    assert any("CENTRAL_BANK_EVENT" in reason for reason in result["reason_codes"])


def test_post_event_delay_expires_after_configured_window():
    events = [_event(minutes_from_now=-(CFG.ff_high_impact_delay_after_minutes + 5))]
    result = calendar_guard.evaluate(["USD"], NOW, events, CFG)
    assert result["decision"] == "ALLOW"


def test_spread_not_normalized_keeps_trade_delayed_past_normal_window():
    minutes_past = -(CFG.ff_high_impact_delay_after_minutes + 5)
    events = [_event(minutes_from_now=minutes_past)]
    normalized_result = calendar_guard.evaluate(["USD"], NOW, events, CFG, spread_normalized=True)
    not_normalized_result = calendar_guard.evaluate(["USD"], NOW, events, CFG, spread_normalized=False)
    assert normalized_result["decision"] == "ALLOW"
    assert not_normalized_result["decision"] == "DELAY"
    assert "SPREAD_NOT_NORMALIZED" in not_normalized_result["reason_codes"]


def test_guard_considers_both_currencies_of_the_pair():
    events = [_event(currency="EUR", minutes_from_now=10, impact="high")]
    eurusd_result = calendar_guard.evaluate(["EUR", "USD"], NOW, events, CFG)
    gbpjpy_result = calendar_guard.evaluate(["GBP", "JPY"], NOW, events, CFG)
    assert eurusd_result["decision"] == "BLOCK"
    assert gbpjpy_result["decision"] == "ALLOW"


def test_no_relevant_events_allows():
    assert calendar_guard.evaluate(["USD"], NOW, [], CFG)["decision"] == "ALLOW"


def test_precedence_combine_takes_most_restrictive():
    block = calendar_guard.empty_result("BLOCK", ["X"])
    reduce = calendar_guard.empty_result("REDUCE_SIZE", ["Y"])
    allow = calendar_guard.empty_result("ALLOW")
    assert calendar_guard.combine(allow, reduce, block)["decision"] == "BLOCK"
    assert calendar_guard.combine(allow, reduce)["decision"] == "REDUCE_SIZE"
    assert calendar_guard.combine(allow)["decision"] == "ALLOW"


def test_openai_cannot_override_a_deterministic_block():
    block = calendar_guard.empty_result("BLOCK", ["HIGH_IMPACT_EVENT_PRE_BLOCK"])
    allow_advisory = {"recommended_action": "allow", "risk_level": "low", "confidence": 0.99}
    combined = macro_context.combine_with_advisory(block, advisory=allow_advisory)
    assert combined["decision"] == "BLOCK"


def test_openai_advisory_can_still_tighten_an_allow():
    allow = calendar_guard.empty_result("ALLOW")
    block_advisory = {"recommended_action": "block", "risk_level": "critical", "confidence": 0.9}
    combined = macro_context.combine_with_advisory(allow, advisory=block_advisory)
    assert combined["decision"] == "BLOCK"


def test_openai_malformed_output_falls_back_safely(monkeypatch):
    class _FakeProvider:
        async def complete(self, request):
            class _Resp:
                text = "not valid json {{{"
                prompt_tokens = 10
                completion_tokens = 5
                estimated_cost = 0.0001

            return _Resp()

    class _FakeRegistry:
        def get(self, name):
            return _FakeProvider()

    monkeypatch.setattr(macro_context, "provider_registry", _FakeRegistry())
    import asyncio

    result = asyncio.run(macro_context.classify({"proposed_trade": {}}, idempotency_key="test", config=CFG))
    assert result is None


def test_openai_low_confidence_is_rejected(monkeypatch):
    import json

    class _FakeProvider:
        async def complete(self, request):
            class _Resp:
                text = json.dumps({"macro_alignment": "neutral", "risk_level": "low", "affected_currencies": [], "directional_bias": {}, "confidence": 0.1, "recommended_action": "allow", "recommended_constraints": [], "reasoning_summary": ""})
                prompt_tokens = 10
                completion_tokens = 5
                estimated_cost = 0.0001

            return _Resp()

    class _FakeRegistry:
        def get(self, name):
            return _FakeProvider()

    monkeypatch.setattr(macro_context, "provider_registry", _FakeRegistry())
    import asyncio

    result = asyncio.run(macro_context.classify({"proposed_trade": {}}, idempotency_key="test", config=CFG))
    assert result is None


def test_news_guard_never_blocks_from_low_confidence_alone():
    classification = {"risk_level": "critical", "confidence": 0.1, "urgency": "low"}
    result = news_guard.evaluate(classification, spread_ratio=None, volatility_state=None, portfolio_exposure=None, position_open=False, config=CFG)
    assert result["decision"] != "BLOCK"


def test_news_guard_downgrades_block_to_manage_existing_only_for_open_position():
    classification = {"risk_level": "critical", "confidence": 0.95, "urgency": "high"}
    result = news_guard.evaluate(classification, spread_ratio=None, volatility_state=None, portfolio_exposure=None, position_open=True, config=CFG)
    assert result["decision"] == "MANAGE_EXISTING_ONLY"


def test_news_guard_no_classification_allows():
    result = news_guard.evaluate(None, spread_ratio=None, volatility_state=None, portfolio_exposure=None, position_open=False, config=CFG)
    assert result["decision"] == "ALLOW"
