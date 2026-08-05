from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MIDDLEWARE_ENABLED", "0")
os.environ.setdefault("E2E_DEV_AUTH", "1")

from backend.ai_provider.base import ProviderRequest, ProviderResponse  # noqa: E402
from backend.ai_provider.citations import append_default_citation, citations_for_bundle  # noqa: E402
from backend.ai_provider.conversations import ConversationStore  # noqa: E402
from backend.ai_provider.openai_provider import _local_fallback  # noqa: E402
from backend.ai_provider.prompt_builder import build_prompt  # noqa: E402
from backend.ai_provider.token_budget import enforce_prompt_budget  # noqa: E402
from backend.ai_provider.validation import INSUFFICIENT, validate_output  # noqa: E402
from backend.auth.deps import get_current_user  # noqa: E402
from backend.main import app  # noqa: E402
from backend.models.user import UserRole  # noqa: E402


TRADING_DATA = {
    "accounts": {"acct_test": {"account_id": "acct_test", "version": 1, "owner_user_id": "user_a"}},
    "deployments": {
        "deploy_test": {
            "deployment_id": "deploy_test",
            "account_id": "acct_test",
            "owner_user_id": "user_a",
            "candidate_id": "cand_test",
            "strategy_id": "ema_trend_continuation_v1",
            "strategy_version": "1.0.0",
            "version": 2,
            "created_at": "2026-07-24T00:00:00Z",
            "status": "APPROVED",
        }
    },
    "risk_evaluations": {
        "risk_test": {
            "evaluation_id": "risk_test",
            "account_id": "acct_test",
            "owner_user_id": "user_a",
            "deployment_id": "deploy_test",
            "decision": "APPROVED",
            "risk_policy_id": "risk_v1",
            "created_at": "2026-07-24T00:00:00Z",
            "rules_evaluated": [{"rule": "max_exposure", "passed": True}],
        }
    },
    "orders": {
        "order_test": {
            "order_id": "order_test",
            "account_id": "acct_test",
            "owner_user_id": "user_a",
            "deployment_id": "deploy_test",
            "strategy_id": "ema_trend_continuation_v1",
            "strategy_version": "1.0.0",
            "risk_evaluation_id": "risk_test",
            "symbol": "AAPL",
            "side": "BUY",
            "quantity": 10,
            "status": "FILLED",
            "version": 3,
            "created_at": "2026-07-24T00:01:00Z",
        }
    },
    "fills": {
        "fill_test": {
            "fill_id": "fill_test",
            "order_id": "order_test",
            "account_id": "acct_test",
            "owner_user_id": "user_a",
            "deployment_id": "deploy_test",
            "instrument_id": "AAPL",
            "sequence": 1,
            "quantity": 10,
            "price": 100,
            "created_at": "2026-07-24T00:02:00Z",
        }
    },
    "ledger": [],
    "audits": [],
    "emergency_controls": {},
    "reconciliations": [
        {
            "reconciliation_id": "recon_test",
            "account_id": "acct_test",
            "owner_user_id": "user_a",
            "status": "MATCHED",
            "created_at": "2026-07-24T00:03:00Z",
            "differences": [],
        }
    ],
}


class DummyUser:
    def __init__(self, user_id: str = "user_a", role: UserRole = UserRole.TRADER) -> None:
        self.id = user_id
        self.role = role


class FakeStore:
    def load(self):
        return TRADING_DATA


class FakeRepository:
    def __init__(self) -> None:
        self.bundles: dict[str, dict] = {}
        self.audit: list[dict] = []

    def save_bundle(self, bundle: dict) -> None:
        self.bundles[bundle["bundle_id"]] = bundle

    def get_bundle(self, bundle_id: str) -> dict | None:
        return self.bundles.get(bundle_id)

    def append_audit(self, record: dict) -> None:
        self.audit.append(record)


class FakeProvider:
    default_model = "phase9d-test"
    name = "test"

    def __init__(self, text: str = "Order status is FILLED") -> None:
        self.text = text
        self.prompts: list[str] = []

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.prompts.append(request.prompt)
        return ProviderResponse(text=self.text, provider=self.name, model=request.model, prompt_tokens=11, completion_tokens=5, latency_ms=1.5)


def _patch_ai(monkeypatch, tmp_path: Path, provider: FakeProvider | None = None) -> FakeProvider:
    monkeypatch.setattr("backend.ai_assistant.adapters.trading.TradingStore", lambda: FakeStore())
    monkeypatch.setattr("backend.ai_assistant.lineage.TradingStore", lambda: FakeStore())
    monkeypatch.setattr("backend.ai_assistant.adapters.strategy.TradingStore", lambda: FakeStore(), raising=False)
    repo = FakeRepository()
    monkeypatch.setattr("backend.ai_assistant.bundles.repository", repo)
    monkeypatch.setattr("backend.ai_assistant.audit.repository", repo)
    store = ConversationStore(tmp_path / "ai")
    monkeypatch.setattr("backend.api.routes.ai.conversation_store", store)
    provider = provider or FakeProvider()
    monkeypatch.setattr("backend.api.routes.ai.provider_registry.get", lambda _name=None: provider)
    return provider


def _as(user_id: str, role: UserRole = UserRole.TRADER) -> None:
    app.dependency_overrides[get_current_user] = lambda: DummyUser(user_id, role)


def test_provider_fallback_uses_exact_insufficient_answer() -> None:
    response = _local_fallback(ProviderRequest(prompt="Evidence Details\n{}", model="x", max_tokens=50, temperature=0, timeout_seconds=1), provider="local", reason="test")
    assert response.text == INSUFFICIENT


def test_prompt_budget_citations_and_validation_are_grounded(monkeypatch, tmp_path: Path) -> None:
    _patch_ai(monkeypatch, tmp_path)
    _as("user_a")
    client = TestClient(app)
    bundle = client.post("/api/ai/evidence/retrieve", json={"domain": "order", "entity_id": "order_test"}).json()["bundle"]

    prompt = build_prompt(user_request="Explain this order", bundle=bundle)
    budget = enforce_prompt_budget(prompt)
    citations = citations_for_bundle(bundle)
    assert budget.prompt_tokens > 0
    assert citations[0]["marker"] == "[Order]"
    assert "Evidence Details" in prompt
    assert append_default_citation("Order status is FILLED", citations).endswith("[Order]")

    answer, warnings = validate_output("Order status is FILLED", bundle=bundle)
    assert answer.endswith("[Order]")
    assert warnings == []

    rejected, warnings = validate_output("Order quantity is 999", bundle=bundle)
    assert rejected.startswith(INSUFFICIENT)
    assert "unsupported_numeric_claim" in warnings
    app.dependency_overrides.pop(get_current_user, None)


def test_chat_stream_and_conversation_ownership(monkeypatch, tmp_path: Path) -> None:
    provider = _patch_ai(monkeypatch, tmp_path)
    _as("user_a")
    client = TestClient(app)

    response = client.post("/api/ai/chat", json={"message": "Explain the order", "domain": "order", "entity_id": "order_test"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"].endswith("[Order]")
    assert payload["provider"] == "test"
    assert payload["evidence_bundle_id"].startswith("bundle_")
    assert payload["conversation_id"].startswith("conv_")
    assert provider.prompts and '"status": "FILLED"' in provider.prompts[0]

    streamed = client.post("/api/ai/chat/stream", json={"message": "Summarize the order", "domain": "order", "entity_id": "order_test"})
    assert streamed.status_code == 200
    assert '"type": "final"' in streamed.text
    assert '"conversation_id"' in streamed.text

    listed = client.get("/api/ai/conversations")
    assert listed.status_code == 200
    assert len(listed.json()["items"]) >= 2

    _as("user_b")
    forbidden = client.get(f"/api/ai/conversations/{payload['conversation_id']}")
    assert forbidden.status_code == 403
    app.dependency_overrides.pop(get_current_user, None)


def test_chat_without_evidence_refuses_to_answer(monkeypatch, tmp_path: Path) -> None:
    _patch_ai(monkeypatch, tmp_path, FakeProvider("Invented answer"))
    _as("user_a")
    client = TestClient(app)
    response = client.post("/api/ai/chat", json={"message": "What should I trade?"})
    assert response.status_code == 200
    assert response.json()["answer"] == INSUFFICIENT
    assert response.json()["grounding"]["warnings"] == ["missing_evidence"]
    app.dependency_overrides.pop(get_current_user, None)
