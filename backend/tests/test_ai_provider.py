from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import asyncio

from backend.intelligence.providers.openai_client import OpenAITradeDecisionClient
from backend.intelligence.trading.config import AITradingConfig


def _response(text: str, *, status: str = "completed"):
    return SimpleNamespace(
        id="resp_test",
        model="gpt-4.1-mini",
        status=status,
        output_text=text,
        usage=SimpleNamespace(input_tokens=111, output_tokens=55, total_tokens=166),
    )


def _decision_json(symbol: str = "EURUSD") -> str:
    now = datetime.now(timezone.utc).isoformat()
    return (
        "{"
        f'"symbol":"{symbol}","timeframe":"15m","decision":"NO_TRADE","entry_type":"NONE",'
        '"proposed_entry":null,"stop_loss":null,"take_profit":null,"confidence":0.7,'
        '"risk_reward_ratio":0,"risk_percent":0,"strategy":"session_breakout",'
        '"market_regime":"neutral","reasoning_summary":"Insufficient evidence.",'
        f'"invalidation_conditions":["fresh breakout"],"data_timestamp":"{now}","decision_timestamp":"{now}"'
        "}"
    )


def test_openai_provider_success(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    client = OpenAITradeDecisionClient(AITradingConfig())
    monkeypatch.setattr(client, "_responses_create", lambda context: _response(_decision_json()))

    decision, telemetry, raw = asyncio.run(client.generate_trade_decision({"symbol": "EURUSD"}))

    assert decision is not None
    assert decision.decision == "NO_TRADE"
    assert telemetry.request_id == "resp_test"
    assert telemetry.total_tokens == 166
    assert "secret" not in str(telemetry.model_dump())
    assert raw


def test_openai_timeout(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    client = OpenAITradeDecisionClient(AITradingConfig(request_timeout_seconds=0.01, max_retries=0))

    def slow(context):
        import time

        time.sleep(0.1)

    monkeypatch.setattr(client, "_responses_create", slow)
    decision, telemetry, _ = asyncio.run(client.generate_trade_decision({"symbol": "EURUSD"}))
    assert decision is None
    assert telemetry.error_category in {"TimeoutError", "TimeoutError"}


def test_openai_authentication_failure(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    client = OpenAITradeDecisionClient(AITradingConfig(max_retries=0))
    monkeypatch.setattr(client, "_responses_create", lambda context: (_ for _ in ()).throw(PermissionError("bad key")))
    decision, telemetry, _ = asyncio.run(client.generate_trade_decision({"symbol": "EURUSD"}))
    assert decision is None
    assert telemetry.error_category == "PermissionError"


def test_openai_rate_limit_response(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    client = OpenAITradeDecisionClient(AITradingConfig(max_retries=0))
    monkeypatch.setattr(client, "_responses_create", lambda context: (_ for _ in ()).throw(RuntimeError("rate limit")))
    decision, telemetry, _ = asyncio.run(client.generate_trade_decision({"symbol": "EURUSD"}))
    assert decision is None
    assert telemetry.error_category == "RuntimeError"


def test_malformed_structured_response(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    client = OpenAITradeDecisionClient(AITradingConfig(max_retries=0))
    monkeypatch.setattr(client, "_responses_create", lambda context: _response("{}"))
    decision, telemetry, _ = asyncio.run(client.generate_trade_decision({"symbol": "EURUSD"}))
    assert decision is None
    assert telemetry.error_category == "ValidationError"
