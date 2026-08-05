from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

from backend.ai_assistant.repository import AIAssistantRepository
from backend.ai_assistant.security import SafeAPIError, rate_limiter, sanitize_value, validate_identifier
from backend.auth.deps import get_current_user
from backend.main import app
from backend.models.user import UserRole


TRADING_DATA = {
    "accounts": {
        "acct_a": {"account_id": "acct_a", "owner_user_id": "user_a", "cash_balance": 1000},
        "acct_b": {"account_id": "acct_b", "owner_user_id": "user_b", "cash_balance": 1000},
    },
    "deployments": {
        "deploy_a": {
            "deployment_id": "deploy_a",
            "account_id": "acct_a",
            "owner_user_id": "user_a",
            "candidate_id": "cand_a",
            "strategy_id": "ema_trend_continuation_v1",
            "strategy_version": "1.0.0",
            "version": 1,
            "status": "APPROVED",
            "created_at": "2026-07-24T00:00:00Z",
            "api_key": "must-not-leak",
        },
        "deploy_b": {
            "deployment_id": "deploy_b",
            "account_id": "acct_b",
            "owner_user_id": "user_b",
            "candidate_id": "cand_b",
            "strategy_id": "ema_trend_continuation_v1",
            "strategy_version": "1.0.0",
            "version": 1,
            "status": "APPROVED",
            "created_at": "2026-07-24T00:00:00Z",
        },
    },
    "risk_evaluations": {
        "risk_a": {
            "evaluation_id": "risk_a",
            "account_id": "acct_a",
            "owner_user_id": "user_a",
            "deployment_id": "deploy_a",
            "decision": "APPROVED",
            "risk_policy_id": "risk_v1",
            "created_at": "2026-07-24T00:00:00Z",
            "rules_evaluated": [{"rule": "max_exposure", "passed": True}],
            "password": "must-not-leak",
        },
        "risk_b": {
            "evaluation_id": "risk_b",
            "account_id": "acct_b",
            "owner_user_id": "user_b",
            "deployment_id": "deploy_b",
            "decision": "APPROVED",
            "risk_policy_id": "risk_v1",
            "created_at": "2026-07-24T00:00:00Z",
            "rules_evaluated": [{"rule": "max_exposure", "passed": True}],
        },
    },
    "orders": {
        "order_a": {
            "order_id": "order_a",
            "account_id": "acct_a",
            "owner_user_id": "user_a",
            "deployment_id": "deploy_a",
            "risk_evaluation_id": "risk_a",
            "status": "FILLED",
            "symbol": "AAPL",
            "side": "BUY",
            "quantity": 1,
            "version": 1,
            "created_at": "2026-07-24T00:01:00Z",
            "secret": "must-not-leak",
        },
        "order_b": {
            "order_id": "order_b",
            "account_id": "acct_b",
            "owner_user_id": "user_b",
            "deployment_id": "deploy_b",
            "risk_evaluation_id": "risk_b",
            "status": "FILLED",
            "symbol": "MSFT",
            "side": "BUY",
            "quantity": 1,
            "version": 1,
            "created_at": "2026-07-24T00:01:00Z",
        },
    },
    "fills": {
        "fill_a": {
            "fill_id": "fill_a",
            "order_id": "order_a",
            "account_id": "acct_a",
            "owner_user_id": "user_a",
            "deployment_id": "deploy_a",
            "instrument_id": "AAPL",
            "sequence": 1,
            "quantity": 1,
            "price": 100,
            "created_at": "2026-07-24T00:02:00Z",
        },
        "fill_b": {
            "fill_id": "fill_b",
            "order_id": "order_b",
            "account_id": "acct_b",
            "owner_user_id": "user_b",
            "deployment_id": "deploy_b",
            "instrument_id": "MSFT",
            "sequence": 1,
            "quantity": 1,
            "price": 100,
            "created_at": "2026-07-24T00:02:00Z",
        },
    },
    "ledger": [],
    "audits": [],
    "emergency_controls": {},
    "reconciliations": [
        {"reconciliation_id": "recon_a", "account_id": "acct_a", "owner_user_id": "user_a", "status": "MATCHED", "created_at": "2026-07-24T00:03:00Z"},
        {"reconciliation_id": "recon_b", "account_id": "acct_b", "owner_user_id": "user_b", "status": "MATCHED", "created_at": "2026-07-24T00:03:00Z"},
    ],
}


class DummyUser:
    def __init__(self, user_id: str, role: UserRole = UserRole.TRADER) -> None:
        self.id = user_id
        self.role = role


class FakeStore:
    def __init__(self) -> None:
        self.saves = 0

    def load(self):
        return TRADING_DATA

    def save(self, data):
        self.saves += 1


class FakeRepository:
    def __init__(self) -> None:
        self.bundles: dict[str, dict] = {}
        self.audit: list[dict] = []

    def save_bundle(self, bundle: dict) -> None:
        self.bundles[bundle["bundle_id"]] = bundle

    def get_bundle(self, bundle_id: str) -> dict | None:
        return self.bundles.get(bundle_id)

    def append_audit(self, record: dict) -> None:
        import hashlib
        import json

        raw = json.dumps(record, sort_keys=True, default=str)
        record["content_hash"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        self.audit.append(record)


def _patch_store(monkeypatch, store: FakeStore | None = None) -> FakeStore:
    store = store or FakeStore()
    monkeypatch.setattr("backend.ai_assistant.adapters.trading.TradingStore", lambda: store)
    monkeypatch.setattr("backend.ai_assistant.lineage.TradingStore", lambda: store)
    monkeypatch.setattr("backend.ai_assistant.adapters.strategy.TradingStore", lambda: store, raising=False)
    return store


def _patch_repository(monkeypatch) -> FakeRepository:
    repo = FakeRepository()
    monkeypatch.setattr("backend.ai_assistant.bundles.repository", repo)
    monkeypatch.setattr("backend.ai_assistant.audit.repository", repo)
    return repo


def _as(user_id: str, role: UserRole = UserRole.TRADER) -> None:
    app.dependency_overrides[get_current_user] = lambda: DummyUser(user_id, role)


def test_ai_endpoint_requires_authentication(monkeypatch) -> None:
    old_auth = os.environ.get("AUTH_MIDDLEWARE_ENABLED")
    old_dev = os.environ.get("E2E_DEV_AUTH")
    app.dependency_overrides.pop(get_current_user, None)
    monkeypatch.setenv("AUTH_MIDDLEWARE_ENABLED", "1")
    monkeypatch.setenv("E2E_DEV_AUTH", "0")
    response = TestClient(app).post("/api/ai/evidence/retrieve", json={"domain": "order", "entity_id": "order_a"})
    assert response.status_code == 401
    if old_auth is not None:
        monkeypatch.setenv("AUTH_MIDDLEWARE_ENABLED", old_auth)
    if old_dev is not None:
        monkeypatch.setenv("E2E_DEV_AUTH", old_dev)


def test_request_body_identity_is_ignored_and_cross_account_denied(monkeypatch) -> None:
    _patch_store(monkeypatch)
    _patch_repository(monkeypatch)
    _as("user_a")
    client = TestClient(app)

    allowed = client.post("/api/ai/evidence/retrieve", json={"domain": "order", "entity_id": "order_a", "user_id": "user_b", "role": "admin"})
    assert allowed.status_code == 200
    assert allowed.json()["bundle"]["owner_user_id"] == "user_a"

    denied = client.post("/api/ai/evidence/retrieve", json={"domain": "order", "entity_id": "order_b"})
    assert denied.status_code == 404
    assert denied.json()["detail"]["code"] == "NOT_FOUND"
    assert "order_b" not in str(denied.json())
    app.dependency_overrides.pop(get_current_user, None)


def test_bundle_ownership_is_rechecked_on_retrieval(monkeypatch) -> None:
    _patch_store(monkeypatch)
    _patch_repository(monkeypatch)
    client = TestClient(app)
    _as("user_a")
    created = client.post("/api/ai/evidence/retrieve", json={"domain": "risk", "entity_id": "risk_a"})
    assert created.status_code == 200
    bundle_id = created.json()["bundle"]["bundle_id"]

    _as("user_b")
    rejected = client.get(f"/api/ai/evidence/{bundle_id}")
    assert rejected.status_code == 403

    _as("admin", UserRole.ADMIN)
    admin = client.get(f"/api/ai/evidence/{bundle_id}")
    assert admin.status_code == 200
    app.dependency_overrides.pop(get_current_user, None)


def test_identifier_validation_rejects_traversal_and_oversized_values() -> None:
    for bad in ("../x", "%2e%2e%2fx", "/abs", "a" * 200, "bad\nid"):
        try:
            validate_identifier(bad)
        except SafeAPIError as exc:
            assert exc.code == "INVALID_REFERENCE"
        else:
            raise AssertionError(f"accepted bad identifier {bad!r}")


def test_redaction_removes_secrets_without_corrupting_symbol_tokens() -> None:
    cleaned = sanitize_value({"password": "x", "api_key": "y", "instrument_token": "NIFTY24", "symbol": "TOKENSEC"})
    assert cleaned["password"] == "[redacted]"
    assert cleaned["api_key"] == "[redacted]"
    assert cleaned["instrument_token"] == "[redacted]"
    assert cleaned["symbol"] == "TOKENSEC"


def test_repository_rejects_corrupt_json_and_symlink_escape(tmp_path: Path) -> None:
    repo = AIAssistantRepository(tmp_path / "ai")
    repo.bundles_path.write_text("{bad-json", encoding="utf-8")
    try:
        repo.get_bundle("bundle_aaaaaaaaaaaa")
    except SafeAPIError as exc:
        assert exc.code == "CORRUPT_AI_STORE"
    else:
        raise AssertionError("corrupt JSON was accepted")

    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    link = tmp_path / "ai" / "audit.json"
    if link.exists():
        link.unlink()
    os.symlink(outside, link)
    try:
        repo.append_audit({"event": "x"})
    except SafeAPIError as exc:
        assert exc.code == "AI_STORE_UNSAFE"
    else:
        raise AssertionError("symlink escape was accepted")


def test_lineage_prunes_unauthorized_linked_entities(monkeypatch) -> None:
    _patch_store(monkeypatch)
    _patch_repository(monkeypatch)
    _as("user_a")
    client = TestClient(app)
    response = client.post("/api/ai/evidence/lineage", json={"domain": "order", "entity_id": "order_a"})
    assert response.status_code == 200
    payload = response.json()
    assert "order_b" not in str(payload)
    assert "fill_b" not in str(payload)
    app.dependency_overrides.pop(get_current_user, None)


def test_rate_limiter_uses_separate_keys() -> None:
    rate_limiter.check("ai-test-user-a", capacity=1)
    try:
        rate_limiter.check("ai-test-user-a", capacity=1)
    except SafeAPIError as exc:
        assert exc.code == "RATE_LIMITED"
    else:
        raise AssertionError("rate limit was not enforced")
    rate_limiter.check("ai-test-user-b", capacity=1)


def test_security_integration_risk_explanation_bundle_audit_and_no_mutation(monkeypatch) -> None:
    store = _patch_store(monkeypatch)
    repo = _patch_repository(monkeypatch)
    _as("user_a")
    client = TestClient(app)

    response = client.post("/api/ai/explain/risk", json={"entity_id": "risk_a"})
    assert response.status_code == 200
    payload = response.json()
    bundle_id = payload["evidence_bundle_id"]
    assert payload["evidence"][0]["structured_values"]["decision"] == "APPROVED"
    assert "password" not in str(payload)
    assert repo.bundles[bundle_id]["owner_user_id"] == "user_a"
    assert repo.audit[-1]["actor"] == "user_a"
    assert repo.audit[-1]["content_hash"]
    assert store.saves == 0

    owner_bundle = client.get(f"/api/ai/evidence/{bundle_id}")
    assert owner_bundle.status_code == 200

    _as("user_b")
    other_bundle = client.get(f"/api/ai/evidence/{bundle_id}")
    assert other_bundle.status_code == 403
    app.dependency_overrides.pop(get_current_user, None)
