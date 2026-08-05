from __future__ import annotations

import asyncio
import os

from backend.api.routes.ai_trading import AnalyzeRequest, ai_trading_status, shadow_analyze  # noqa: E402
from backend.models.user import UserRole  # noqa: E402


class DummyUser:
    id = "user_ai"
    role = UserRole.ADMIN


def test_api_key_never_returned(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    body = asyncio.run(ai_trading_status(current_user=DummyUser()))
    assert body["api_key_configured"] is True
    assert "secret-value" not in str(body)


def test_analyze_rejects_non_allowlisted_symbol() -> None:
    body = asyncio.run(shadow_analyze(AnalyzeRequest(symbol="USDCHF", timeframe="15m"), current_user=DummyUser()))
    assert body["status"] == "REJECTED_INVALID"


def test_historical_incidents_do_not_block_current_readiness(monkeypatch) -> None:
    from backend.api.routes import brokers

    async def fake_status():
        return {"connection_state": "ACCOUNT_VERIFIED", "account_verified": True, "reconciliation_status": "MATCHED", "recovery": {"blocking": False}}

    class Incident:
        def model_dump(self, mode="json"):
            return {"code": "IBKR_CLIENT_ID_CONFLICT", "blocking": True}

    monkeypatch.setattr(brokers.ibkr_acceptance_service, "status", fake_status)
    monkeypatch.setattr(brokers.ibkr_acceptance_service, "incidents", lambda: [Incident()])
    body = asyncio.run(brokers.ibkr_acceptance_status(current_user=DummyUser()))
    assert body["result"] == "READY"
    assert body["current_blocking_incidents"] == []
    assert body["historical_incidents"][0]["code"] == "IBKR_CLIENT_ID_CONFLICT"
