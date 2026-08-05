from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MIDDLEWARE_ENABLED", "0")
os.environ.setdefault("E2E_DEV_AUTH", "1")

from backend.ai_assistant.authorization import AuthorizationContext, AuthorizationError, authorization_service  # noqa: E402
from backend.ai_assistant.evidence import evidence_builder  # noqa: E402
from backend.ai_assistant.intents import parse_intent  # noqa: E402
from backend.ai_assistant.models import AssistantIntent, ExplanationDomain, ToolAuthorization, ToolSpec  # noqa: E402
from backend.ai_assistant.services import AIAssistantService  # noqa: E402
from backend.ai_assistant.tool_registry import tool_registry  # noqa: E402
from backend.auth.deps import get_current_user  # noqa: E402
from backend.models.user import UserRole  # noqa: E402
from backend.main import app  # noqa: E402


class DummyUser:
    id = "dev-user"
    role = UserRole.ADMIN


def test_intent_parsing_is_deterministic() -> None:
    assert parse_intent("explain this strategy decision") is AssistantIntent.REVIEW_STRATEGY
    assert parse_intent("trace lineage for this research run") is AssistantIntent.TRACE_LINEAGE
    assert parse_intent("compare these risk evaluations") is AssistantIntent.REVIEW_RISK
    assert parse_intent("") is AssistantIntent.EXPLAIN


def test_tool_registry_exposes_read_only_metadata() -> None:
    tools = {tool.name: tool for tool in tool_registry.list_tools()}

    for expected in {
        "get_market_structure_snapshot",
        "get_strategy_decision",
        "get_research_run",
        "get_scorecard",
        "get_candidate",
        "get_deployment",
        "get_risk_evaluation",
        "get_order",
        "get_fills",
        "get_position",
        "get_account_snapshot",
        "get_reconciliation",
    }:
        spec = tools[expected]
        assert spec.read_only is True
        assert spec.authorization.read_only is True
        assert spec.timeout_seconds > 0
        assert spec.max_results >= 1
        assert spec.schema["type"] == "object"


def test_authorization_rejects_mutating_tools() -> None:
    mutating = ToolSpec(
        name="bad_mutator",
        description="bad",
        schema={"type": "object"},
        authorization=ToolAuthorization(
            read_only=False,
            allowed_intents=(AssistantIntent.EXPLAIN,),
            denied_capabilities=("submit_orders",),
        ),
        timeout_seconds=1,
        max_results=1,
        read_only=False,
    )

    try:
        authorization_service.authorize_tool(mutating, AuthorizationContext(intent=AssistantIntent.EXPLAIN))
    except AuthorizationError:
        pass
    else:
        raise AssertionError("mutating tool was authorized")


def test_evidence_generation_marks_stale_and_partial_entities() -> None:
    evidence = evidence_builder.build(
        source="risk_evaluation",
        entity="risk",
        entity_id="risk-1",
        values={"timestamp": "2000-01-01T00:00:00Z", "risk_score": 0.8, "reason": None},
    )

    assert evidence.freshness.value == "stale"
    assert evidence.quality.value == "partial"
    assert evidence.source == "risk_evaluation"


def test_explanation_generation_is_deterministic_and_read_only() -> None:
    result = AIAssistantService().explain(
        domain=ExplanationDomain.ORDER,
        payload={
            "entity_id": "ord-1",
            "entity": {
                "order_id": "ord-1",
                "symbol": "AAPL",
                "side": "BUY",
                "quantity": 10,
                "status": "accepted",
                "timestamp": "2026-07-24T00:00:00Z",
            },
        },
    )

    assert result["domain"] == "order"
    assert result["guardrails"]["llm_used"] is False
    assert result["guardrails"]["execution_authority"] == "none"
    assert result["tool"]["read_only"] is True
    assert "order_id: ord-1" in result["points"]


def test_unsupported_tool_is_rejected() -> None:
    try:
        tool_registry.get("shell")
    except KeyError as exc:
        assert "unsupported AI tool" in str(exc)
    else:
        raise AssertionError("unsupported tool was returned")


def test_api_reports_missing_entity() -> None:
    app.dependency_overrides[get_current_user] = lambda: DummyUser()
    client = TestClient(app)

    response = client.post("/api/ai/explain/risk", json={"entity_id": "missing"})

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NOT_FOUND"
    app.dependency_overrides.pop(get_current_user, None)


def test_api_reports_stale_entity_without_mutation() -> None:
    app.dependency_overrides[get_current_user] = lambda: DummyUser()
    client = TestClient(app)

    response = client.post(
        "/api/ai/explain/reconciliation",
        json={
            "entity_id": "rec-1",
            "entity": {
                "status": "matched",
                "break_count": 0,
                "as_of": "2000-01-01T00:00:00Z",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence"][0]["freshness"] == "IMMUTABLE"
    assert payload["audit"]["mutable_action"] is False
    app.dependency_overrides.pop(get_current_user, None)
