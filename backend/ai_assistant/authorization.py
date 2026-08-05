from __future__ import annotations

from dataclasses import dataclass

from backend.services.ai_research_assistant import PROHIBITED_ACTIONS
from backend.ai_assistant.models import AssistantIntent, ToolSpec


class AuthorizationError(PermissionError):
    pass


@dataclass(frozen=True)
class AuthorizationContext:
    user_id: str | None = None
    intent: AssistantIntent = AssistantIntent.EXPLAIN
    role: str = "trader"
    account_ids: tuple[str, ...] = ()
    research_owner: str | None = None

    def can_read_account(self, account_id: str | None) -> bool:
        return self.role == "admin" or account_id is None or account_id in self.account_ids

    def can_read_account_row(self, row: dict | None) -> bool:
        if self.role == "admin":
            return True
        if not row:
            return False
        owner = row.get("owner_user_id") or row.get("user_id")
        if owner is not None:
            return str(owner) == str(self.user_id) or self.can_read_account(row.get("account_id"))
        return self.can_read_account(row.get("account_id"))

    def can_read_research(self, owner: str | None) -> bool:
        if self.role == "admin":
            return True
        if owner is None:
            return self.research_owner is None
        return str(owner) in {str(self.user_id), str(self.research_owner)}


class AssistantAuthorizationService:
    def authorize_tool(self, tool: ToolSpec, context: AuthorizationContext) -> None:
        if not tool.read_only or not tool.authorization.read_only:
            raise AuthorizationError(f"{tool.name} is not read-only")
        if context.intent not in tool.authorization.allowed_intents:
            raise AuthorizationError(f"{context.intent.value} is not allowed for {tool.name}")
        denied = set(tool.authorization.denied_capabilities)
        blocked = denied.intersection(PROHIBITED_ACTIONS)
        if blocked:
            return
        raise AuthorizationError(f"{tool.name} does not declare prohibited action guardrails")


authorization_service = AssistantAuthorizationService()
