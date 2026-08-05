from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MIDDLEWARE_ENABLED", "0")
os.environ.setdefault("E2E_DEV_AUTH", "1")

from backend.ai_assistant.adapters.trading import TradingEvidenceAdapter  # noqa: E402
from backend.ai_assistant.authorization import AuthorizationContext  # noqa: E402
from backend.ai_assistant.freshness import domain_freshness, domain_quality  # noqa: E402
from backend.ai_assistant.models import (  # noqa: E402
    AssistantIntent,
    AuthorizationDecision,
    EntityReference,
    ExplanationDomain,
    Freshness,
    Quality,
)
from backend.ai_assistant.services import AIAssistantService  # noqa: E402
from backend.auth.deps import get_current_user  # noqa: E402
from backend.models.user import UserRole  # noqa: E402
from backend.main import app  # noqa: E402


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


def _patch_store(monkeypatch) -> None:
    monkeypatch.setattr("backend.ai_assistant.adapters.trading.TradingStore", lambda: FakeStore())
    monkeypatch.setattr("backend.ai_assistant.lineage.TradingStore", lambda: FakeStore())
    monkeypatch.setattr("backend.ai_assistant.adapters.strategy.TradingStore", lambda: FakeStore(), raising=False)


def _patch_repository(monkeypatch) -> FakeRepository:
    repo = FakeRepository()
    monkeypatch.setattr("backend.ai_assistant.bundles.repository", repo)
    monkeypatch.setattr("backend.ai_assistant.audit.repository", repo)
    return repo


class DummyUser:
    def __init__(self, user_id: str = "user_a", role: UserRole = UserRole.TRADER) -> None:
        self.id = user_id
        self.role = role


def test_entity_reference_rejects_unsupported_and_malformed_ids() -> None:
    try:
        EntityReference(domain=ExplanationDomain.ORDER, entity_type="paper_order", entity_id="../bad")
    except ValueError as exc:
        assert "malformed entity_id" in str(exc)
    else:
        raise AssertionError("malformed entity id was accepted")

    try:
        EntityReference(domain=ExplanationDomain.ORDER, entity_type="deployment", entity_id="deploy_test")
    except ValueError as exc:
        assert "unsupported entity_type" in str(exc)
    else:
        raise AssertionError("unsupported entity type was accepted")


def test_trading_adapter_reads_file_backed_order_and_scope_checks(monkeypatch) -> None:
    _patch_store(monkeypatch)
    reference = EntityReference(
        domain=ExplanationDomain.ORDER,
        entity_type="paper_order",
        entity_id="order_test",
    )

    allowed = TradingEvidenceAdapter().get_entity(
        reference,
        authorization_context=AuthorizationContext(intent=AssistantIntent.EXPLAIN, account_ids=("acct_test",)),
    )
    assert allowed.authorization_result is AuthorizationDecision.ALLOWED
    assert allowed.evidence is not None
    assert allowed.evidence.persistence.value == "file-backed"
    assert allowed.evidence.version == "3"
    assert allowed.evidence.freshness is Freshness.IMMUTABLE
    assert allowed.evidence.quality is Quality.SIMULATED
    assert allowed.evidence.content_hash

    denied = TradingEvidenceAdapter().get_entity(
        reference,
        authorization_context=AuthorizationContext(intent=AssistantIntent.EXPLAIN, account_ids=("other",)),
    )
    assert denied.authorization_result is AuthorizationDecision.SCOPE_MISMATCH
    assert denied.evidence is None


def test_domain_freshness_and_quality_are_domain_aware() -> None:
    assert domain_freshness(ExplanationDomain.RESEARCH, {"status": "done"}, timestamp=None) is Freshness.IMMUTABLE
    assert domain_freshness(ExplanationDomain.RISK, {"status": "EXPIRED"}, timestamp=None) is Freshness.SUPERSEDED
    assert domain_quality(ExplanationDomain.ORDER, {"order_id": "order_test"}) is Quality.SIMULATED
    assert domain_quality(ExplanationDomain.RESEARCH, {}) is Quality.INVALID


def test_service_retrieves_persistent_evidence_bundle_and_lineage(monkeypatch) -> None:
    _patch_store(monkeypatch)
    repo = _patch_repository(monkeypatch)

    result = AIAssistantService().retrieve_evidence(
        payload={"domain": "order", "entity_id": "order_test"},
        user=DummyUser(),
    )

    bundle = result["bundle"]
    assert bundle["bundle_id"] in repo.bundles
    assert bundle["primary_entity"]["entity_type"] == "paper_order"
    assert bundle["items"][0]["source"] == "trading.orders"
    assert bundle["items"][0]["content_hash"]
    assert any(edge["relation"] == "filled_by" for edge in bundle["lineage"]["edges"])


def test_explanation_uses_adapter_evidence_and_records_bundle(monkeypatch) -> None:
    _patch_store(monkeypatch)
    _patch_repository(monkeypatch)

    result = AIAssistantService().explain(
        domain=ExplanationDomain.ORDER,
        payload={"entity_id": "order_test"},
        user=DummyUser(),
    )

    assert result["evidence"][0]["source"] == "trading.orders"
    assert result["evidence_bundle_id"].startswith("bundle_")
    assert result["tool_call"]["authorization_result"] == "ALLOWED"
    assert result["guardrails"]["llm_used"] is False
    assert result["guardrails"]["read_only"] is True


def test_evidence_api_retrieve_get_and_lineage(monkeypatch) -> None:
    _patch_store(monkeypatch)
    _patch_repository(monkeypatch)
    app.dependency_overrides[get_current_user] = lambda: DummyUser()
    client = TestClient(app)

    retrieved = client.post(
        "/api/ai/evidence/retrieve",
        json={"domain": "order", "entity_id": "order_test", "account_ids": ["other"], "role": "admin"},
    )
    assert retrieved.status_code == 200
    bundle_id = retrieved.json()["bundle"]["bundle_id"]

    fetched = client.get(f"/api/ai/evidence/{bundle_id}")
    assert fetched.status_code == 200
    assert fetched.json()["bundle_id"] == bundle_id

    lineage = client.post(
        "/api/ai/evidence/lineage",
        json={"domain": "order", "entity_id": "order_test"},
    )
    assert lineage.status_code == 200
    assert lineage.json()["lineage"]["nodes"][0]["id"] == "order_test"
    app.dependency_overrides.pop(get_current_user, None)
