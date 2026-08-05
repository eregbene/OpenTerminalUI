from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MIDDLEWARE_ENABLED", "0")
os.environ.setdefault("E2E_DEV_AUTH", "1")

from backend.ai_provider.base import ProviderRequest, ProviderResponse  # noqa: E402
from backend.ai_provider.budgets import BudgetManager  # noqa: E402
from backend.ai_provider.openai_provider import _local_fallback  # noqa: E402
from backend.ai_provider.pricing import pricing_table  # noqa: E402
from backend.ai_provider.resilience import CircuitState, ProviderCircuitBreaker  # noqa: E402
from backend.ai_provider.usage import UsageLedger  # noqa: E402
from backend.ai_secrets.environment import EnvironmentSecretStore  # noqa: E402
from backend.auth.deps import get_current_user  # noqa: E402
from backend.main import app  # noqa: E402
from backend.models.user import UserRole  # noqa: E402


class DummyUser:
    def __init__(self, user_id: str = "user_a", role: UserRole = UserRole.ADMIN) -> None:
        self.id = user_id
        self.role = role


def test_provider_usage_cost_and_secret_redaction(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret-value")
    assert EnvironmentSecretStore().metadata("openai", "api_key").present is True
    assert "secret-value" not in str(EnvironmentSecretStore().metadata("openai", "api_key"))

    cost, currency, version = pricing_table.estimate(provider="openai", model="gpt-4.1-mini", input_tokens=1000, output_tokens=500, cached_input_tokens=100)
    assert currency == "USD"
    assert version
    assert float(cost) >= 0

    ledger = UsageLedger(tmp_path / "usage")
    response = ProviderResponse(text="ok", provider="openai", model="gpt-4.1-mini", prompt_tokens=1000, completion_tokens=500, total_tokens=1500, estimated_cost=cost)
    record = ledger.record(user_id="user_a", conversation_id="conv_test", research_job_id="job_test", provider_response=response)
    assert record["usage_reported"] is False
    assert ledger.aggregate(user_id="user_a")["aggregates"]["user:user_a"]["tokens"] == 1500


def test_budget_reservation_and_circuit_breaker() -> None:
    manager = BudgetManager()
    reservation = manager.reserve(user_id="user_budget", conversation_id=None, estimated_tokens=100, estimated_cost="0")
    assert reservation["status"] == "active"
    manager.reconcile(reservation["reservation_id"], status="completed", actual_tokens=90, actual_cost="0")

    breaker = ProviderCircuitBreaker(failure_threshold=1, open_seconds=30)
    breaker.failure("openai", reason="timeout")
    assert breaker.status("openai").state is CircuitState.OPEN


def test_provider_status_api_never_returns_secret(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    app.dependency_overrides[get_current_user] = lambda: DummyUser()
    client = TestClient(app)

    providers = client.get("/api/ai/providers")
    assert providers.status_code == 200
    body = providers.json()
    assert "must-not-leak" not in str(body)
    assert any(row["provider"] == "openai" for row in body["items"])

    health = client.get("/api/ai/providers/health")
    assert health.status_code == 200

    budgets = client.get("/api/ai/providers/budgets")
    assert budgets.status_code == 200
    app.dependency_overrides.pop(get_current_user, None)


def test_local_fallback_has_cost_metadata() -> None:
    response = _local_fallback(ProviderRequest(prompt="hello", model="local-grounded", max_tokens=20, temperature=0, timeout_seconds=1), provider="local", reason="test")
    assert response.total_tokens > 0
    assert response.currency == "USD"
    assert response.pricing_version
