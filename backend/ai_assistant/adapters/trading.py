from __future__ import annotations

from backend.ai_assistant.adapters.base import AdapterResult
from backend.ai_assistant.authorization import AuthorizationContext
from backend.ai_assistant.evidence import evidence_builder
from backend.ai_assistant.models import AuthorizationDecision, EntityReference, ExplanationDomain, SourcePersistence
from backend.ai_assistant.security import filter_evidence_values
from backend.trading.persistence import TradingStore
from backend.trading.services import TradingControlService


class TradingEvidenceAdapter:
    def get_entity(self, reference: EntityReference, *, authorization_context: AuthorizationContext) -> AdapterResult:
        data = TradingStore().load()
        if reference.entity_type == "risk_evaluation":
            return self._row(reference, authorization_context, data["risk_evaluations"], "trading.risk_evaluations", ExplanationDomain.RISK)
        if reference.entity_type == "paper_order":
            return self._row(reference, authorization_context, data["orders"], "trading.orders", ExplanationDomain.ORDER)
        if reference.entity_type == "paper_fill":
            return self._fill(reference, authorization_context, data)
        if reference.entity_type == "account_snapshot":
            return self._account_snapshot(reference, authorization_context)
        if reference.entity_type == "position":
            return self._position(reference, authorization_context)
        if reference.entity_type == "reconciliation":
            return self._reconciliation(reference, authorization_context, data)
        if reference.entity_type == "deployment":
            from backend.ai_assistant.adapters.strategy import StrategyEvidenceAdapter

            return StrategyEvidenceAdapter().get_entity(reference, authorization_context=authorization_context)
        return AdapterResult(AuthorizationDecision.UNSUPPORTED)

    def _row(
        self,
        reference: EntityReference,
        authorization_context: AuthorizationContext,
        rows: dict[str, dict],
        source: str,
        domain: ExplanationDomain,
    ) -> AdapterResult:
        row = rows.get(reference.entity_id)
        if row is None:
            return AdapterResult(AuthorizationDecision.NOT_FOUND)
        if not authorization_context.can_read_account_row(row):
            return AdapterResult(AuthorizationDecision.SCOPE_MISMATCH)
        version = str(row.get("version") or row.get("risk_policy_id") or row.get("execution_model_version") or row.get("updated_at") or row.get("created_at") or "1")
        if reference.entity_version and reference.entity_version != version:
            return AdapterResult(AuthorizationDecision.NOT_FOUND, warnings=("requested version not found",))
        evidence = evidence_builder.build(
            source=source,
            entity=reference.entity_type,
            entity_id=reference.entity_id,
            values=filter_evidence_values(reference.entity_type, row),
            version=version,
            domain=domain,
            persistence=SourcePersistence.FILE,
        )
        return AdapterResult(AuthorizationDecision.ALLOWED, evidence=evidence)

    def _fill(self, reference: EntityReference, authorization_context: AuthorizationContext, data: dict) -> AdapterResult:
        fill = data["fills"].get(reference.entity_id)
        if fill is None:
            fills = [row for row in data["fills"].values() if row.get("order_id") == reference.entity_id]
            if not fills:
                return AdapterResult(AuthorizationDecision.NOT_FOUND)
            fill = {"order_id": reference.entity_id, "fills": fills, "created_at": fills[-1].get("created_at")}
        first_fill = (fill.get("fills") or [{}])[0] if isinstance(fill.get("fills"), list) else fill
        if not authorization_context.can_read_account_row(first_fill):
            return AdapterResult(AuthorizationDecision.SCOPE_MISMATCH)
        evidence = evidence_builder.build(
            source="trading.fills",
            entity="paper_fill",
            entity_id=reference.entity_id,
            values=filter_evidence_values("paper_fill", fill),
            version=str(fill.get("sequence") or fill.get("created_at") or "fills"),
            domain=ExplanationDomain.ORDER,
            persistence=SourcePersistence.FILE,
        )
        return AdapterResult(AuthorizationDecision.ALLOWED, evidence=evidence)

    def _account_snapshot(self, reference: EntityReference, authorization_context: AuthorizationContext) -> AdapterResult:
        data = TradingStore().load()
        account = data["accounts"].get(reference.entity_id)
        if account is None:
            return AdapterResult(AuthorizationDecision.NOT_FOUND)
        if not authorization_context.can_read_account_row(account):
            return AdapterResult(AuthorizationDecision.SCOPE_MISMATCH)
        try:
            snapshot = TradingControlService().snapshot(reference.entity_id).model_dump(mode="json")
        except KeyError:
            return AdapterResult(AuthorizationDecision.NOT_FOUND)
        evidence = evidence_builder.build(
            source="trading.account_snapshot",
            entity="account_snapshot",
            entity_id=reference.entity_id,
            values=filter_evidence_values("account_snapshot", snapshot),
            version=snapshot.get("snapshot_id") or "snapshot",
            domain=ExplanationDomain.POSITION,
            persistence=SourcePersistence.FILE,
        )
        return AdapterResult(AuthorizationDecision.ALLOWED, evidence=evidence)

    def _position(self, reference: EntityReference, authorization_context: AuthorizationContext) -> AdapterResult:
        account_id = reference.account_id
        if not account_id:
            return AdapterResult(AuthorizationDecision.NOT_FOUND, warnings=("position lookup requires account_id",))
        data = TradingStore().load()
        account = data["accounts"].get(account_id)
        if account is None:
            return AdapterResult(AuthorizationDecision.NOT_FOUND)
        if not authorization_context.can_read_account_row(account):
            return AdapterResult(AuthorizationDecision.SCOPE_MISMATCH)
        try:
            snapshot = TradingControlService().snapshot(account_id)
        except KeyError:
            return AdapterResult(AuthorizationDecision.NOT_FOUND)
        for position in snapshot.positions:
            if position.position_id == reference.entity_id or position.instrument_id == reference.entity_id:
                row = position.model_dump(mode="json")
                evidence = evidence_builder.build(
                    source="trading.positions",
                    entity="position",
                    entity_id=reference.entity_id,
                    values=filter_evidence_values("position", row),
                    version=str(row.get("updated_at") or "position"),
                    domain=ExplanationDomain.POSITION,
                    persistence=SourcePersistence.FILE,
                )
                return AdapterResult(AuthorizationDecision.ALLOWED, evidence=evidence)
        return AdapterResult(AuthorizationDecision.NOT_FOUND)

    def _reconciliation(self, reference: EntityReference, authorization_context: AuthorizationContext, data: dict) -> AdapterResult:
        rows = data["reconciliations"]
        row = next((item for item in rows if item.get("reconciliation_id") == reference.entity_id), None)
        if row is None:
            row = next((item for item in reversed(rows) if item.get("account_id") == reference.entity_id), None)
        if row is None:
            return AdapterResult(AuthorizationDecision.NOT_FOUND)
        if not authorization_context.can_read_account_row(row):
            return AdapterResult(AuthorizationDecision.SCOPE_MISMATCH)
        evidence = evidence_builder.build(
            source="trading.reconciliations",
            entity="reconciliation",
            entity_id=reference.entity_id,
            values=filter_evidence_values("reconciliation", row),
            version=str(row.get("reconciliation_id") or row.get("created_at") or "reconciliation"),
            domain=ExplanationDomain.RECONCILIATION,
            persistence=SourcePersistence.FILE,
        )
        return AdapterResult(AuthorizationDecision.ALLOWED, evidence=evidence)
