from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MIDDLEWARE_ENABLED", "0")
os.environ.setdefault("E2E_DEV_AUTH", "1")

from backend.auth.deps import get_current_user  # noqa: E402
from backend.main import app  # noqa: E402
from backend.models.user import UserRole  # noqa: E402
from backend.ai_assistant.security import rate_limiter  # noqa: E402


def setup_function() -> None:
    rate_limiter._buckets.clear()


class DummyUser:
    def __init__(self, user_id: str = "researcher_a", role: UserRole = UserRole.ADMIN) -> None:
        self.id = user_id
        self.role = role


def test_research_agent_policy_plan_execution_and_reports(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("backend.research_agent.store.store", __import__("backend.research_agent.store", fromlist=["ResearchAgentStore"]).ResearchAgentStore(tmp_path / "ra"))
    app.dependency_overrides[get_current_user] = lambda: DummyUser()
    client = TestClient(app)

    policy = client.post("/api/research-agent/policies", json={"workspace_id": "default", "allowed_research_templates": ["baseline_validation"]})
    assert policy.status_code == 200
    policy_id = policy.json()["policy_id"]
    assert client.post(f"/api/research-agent/policies/{policy_id}/activate").status_code == 200

    hypotheses = client.post("/api/research-agent/hypotheses", json={"objective": "test validation quality", "instrument": "TEST"})
    assert hypotheses.status_code == 200
    assert hypotheses.json()["items"][0]["confidence"] == "UNASSESSED"

    plan = client.post("/api/research-agent/plans", json={"objective": "validate baseline strategy", "template": "baseline_validation", "hypotheses": hypotheses.json()["items"]})
    assert plan.status_code == 200
    plan_id = plan.json()["plan_id"]
    assert plan.json()["status"] == "WAITING_APPROVAL"

    assert client.post(f"/api/research-agent/plans/{plan_id}/approve").status_code == 200
    started = client.post(f"/api/research-agent/plans/{plan_id}/start")
    assert started.status_code == 200
    assert started.json()["status"] == "COMPLETED"
    assert all(task["status"] == "COMPLETED" for task in started.json()["tasks"])

    reports = client.get(f"/api/research-agent/plans/{plan_id}/reports")
    assert reports.status_code == 200
    assert reports.json()["items"]
    app.dependency_overrides.pop(get_current_user, None)


def test_research_agent_rejects_unapproved_and_forbidden_trading_objective(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("backend.research_agent.store.store", __import__("backend.research_agent.store", fromlist=["ResearchAgentStore"]).ResearchAgentStore(tmp_path / "ra2"))
    app.dependency_overrides[get_current_user] = lambda: DummyUser()
    client = TestClient(app)
    policy_id = client.post("/api/research-agent/policies", json={"workspace_id": "default"}).json()["policy_id"]
    client.post(f"/api/research-agent/policies/{policy_id}/activate")

    plan = client.post("/api/research-agent/plans", json={"objective": "create order from research", "template": "baseline_validation"})
    assert plan.status_code == 200
    start = client.post(f"/api/research-agent/plans/{plan.json()['plan_id']}/start")
    assert start.status_code == 403
    assert start.json()["detail"]["code"] in {"APPROVAL_REQUIRED", "FORBIDDEN_RESEARCH_ACTION"}
    app.dependency_overrides.pop(get_current_user, None)


def test_research_agent_tenant_isolation(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("backend.research_agent.store.store", __import__("backend.research_agent.store", fromlist=["ResearchAgentStore"]).ResearchAgentStore(tmp_path / "ra3"))
    client = TestClient(app)
    app.dependency_overrides[get_current_user] = lambda: DummyUser("owner_a")
    policy_id = client.post("/api/research-agent/policies", json={}).json()["policy_id"]

    app.dependency_overrides[get_current_user] = lambda: DummyUser("owner_b")
    assert client.get(f"/api/research-agent/policies/{policy_id}").status_code == 404
    app.dependency_overrides.pop(get_current_user, None)
