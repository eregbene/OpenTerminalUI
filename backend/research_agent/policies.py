from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from backend.ai_assistant.security import SafeAPIError, validate_identifier
from backend.research_agent.audit import audit
from backend.research_agent.models import PolicyStatus, ResearchAgentMode, ResearchPolicy, now_iso
from backend.research_agent.store import store


class PolicyService:
    def create(self, payload: dict[str, Any], *, user_id: str) -> dict[str, Any]:
        policy = ResearchPolicy(
            policy_id=f"pol_{uuid4().hex[:12]}",
            owner_user_id=validate_identifier(user_id),
            workspace_id=validate_identifier(str(payload.get("workspace_id") or "default")),
            allowed_instruments=tuple(payload.get("allowed_instruments") or ("TEST", "NSE:RELIANCE", "AAPL")),
            allowed_research_templates=tuple(payload.get("allowed_research_templates") or ("baseline_validation",)),
            approval_required=bool(payload.get("approval_required", True)),
        )
        row = store.put("policies", policy.policy_id, policy.to_dict())
        audit("policy.create", actor=user_id, target=policy.policy_id)
        return row

    def activate(self, policy_id: str, *, user_id: str) -> dict[str, Any]:
        row = self.get(policy_id, user_id=user_id)
        if row["status"] == PolicyStatus.REVOKED.value:
            raise SafeAPIError(409, "POLICY_REVOKED", "policy revoked")
        row = {**row, "status": PolicyStatus.ACTIVE.value, "activated_at": now_iso(), "version": int(row.get("version") or 1)}
        store.put("policies", row["policy_id"], row)
        audit("policy.activate", actor=user_id, target=policy_id)
        return row

    def revoke(self, policy_id: str, *, user_id: str) -> dict[str, Any]:
        row = self.get(policy_id, user_id=user_id)
        row = {**row, "status": PolicyStatus.REVOKED.value, "revoked_at": now_iso()}
        store.put("policies", row["policy_id"], row)
        audit("policy.revoke", actor=user_id, target=policy_id)
        return row

    def active(self, *, user_id: str, workspace_id: str = "default") -> dict[str, Any] | None:
        for row in self.list(user_id=user_id):
            if row.get("workspace_id") == workspace_id and row.get("status") == PolicyStatus.ACTIVE.value and not self._expired(row):
                return row
        return None

    def list(self, *, user_id: str) -> list[dict[str, Any]]:
        return [row for row in store.read().get("policies", {}).values() if row.get("owner_user_id") == user_id]

    def get(self, policy_id: str, *, user_id: str) -> dict[str, Any]:
        row = store.read().get("policies", {}).get(validate_identifier(policy_id))
        if not row or row.get("owner_user_id") != user_id:
            raise LookupError("policy not found")
        return row

    def validate_mode(self, mode: str, policy: dict[str, Any] | None) -> ResearchAgentMode:
        parsed = ResearchAgentMode(mode)
        if parsed is ResearchAgentMode.AUTONOMOUS_RESEARCH and not policy:
            raise SafeAPIError(403, "POLICY_REQUIRED", "active policy required")
        return parsed

    def _expired(self, row: dict[str, Any]) -> bool:
        value = row.get("expiry_date")
        if not value:
            return False
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")) <= datetime.now(timezone.utc)


policy_service = PolicyService()
