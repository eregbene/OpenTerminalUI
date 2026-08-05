from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MIDDLEWARE_ENABLED", "0")
os.environ.setdefault("E2E_DEV_AUTH", "1")

from backend.auth.deps import get_current_user  # noqa: E402
from backend.models.user import UserRole  # noqa: E402
from backend.main import app  # noqa: E402


class DummyUser:
    id = "dev-user"
    role = UserRole.ADMIN


def _auth() -> None:
    app.dependency_overrides[get_current_user] = lambda: DummyUser()


def test_ai_research_brief_requires_symbol() -> None:
    _auth()
    client = TestClient(app)

    response = client.post("/api/ai/research-brief", json={})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_REFERENCE"
    app.dependency_overrides.pop(get_current_user, None)


def test_ai_research_brief_returns_grounded_evidence() -> None:
    _auth()
    client = TestClient(app)

    response = client.post(
        "/api/ai/research-brief",
        json={
            "symbol": "reliance",
            "horizon": "intraday",
            "question": "Explain the setup",
            "context": {
                "quote": {"last_price": 2500, "change_pct": 1.2},
                "market_structure": {"trend": "bullish", "status": "validated"},
                "risk": {"risk_score": 0.25},
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "research_brief"
    assert payload["symbol"] == "RELIANCE"
    assert payload["decision_support"]["posture"] == "constructive"
    assert payload["decision_support"]["confidence"] == "low-medium"
    assert {item["id"] for item in payload["evidence"]} == {
        "quote.context",
        "market_structure.context",
        "risk.context",
    }
    assert payload["guardrails"]["execution_authority"] == "none"
    assert payload["guardrails"]["requires_human_review"] is True
    app.dependency_overrides.pop(get_current_user, None)


def test_ai_research_brief_blocks_execution_authority() -> None:
    _auth()
    client = TestClient(app)

    response = client.post(
        "/api/ai/research-brief",
        json={
            "symbol": "AAPL",
            "context": {
                "strategy": {"status": "passed", "sharpe": 1.4},
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert "submit_orders" in payload["decision_support"]["not_in_scope"]
    assert "approve_risk" in payload["decision_support"]["not_in_scope"]
    assert "promote_strategy" in payload["decision_support"]["not_in_scope"]
    app.dependency_overrides.pop(get_current_user, None)
