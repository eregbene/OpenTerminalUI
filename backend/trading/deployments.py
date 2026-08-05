from __future__ import annotations

from datetime import datetime

from backend.trading.models import AuditRecord, DeploymentStatus, StrategyDeployment, utcnow


def request_approval(deployment: StrategyDeployment) -> AuditRecord:
    deployment.status = DeploymentStatus.PENDING_APPROVAL
    deployment.updated_at = utcnow()
    deployment.version += 1
    return AuditRecord(event_type="paper_deployment_pending_approval", entity_type="strategy_deployment", entity_id=deployment.deployment_id, account_id=deployment.account_id, deployment_id=deployment.deployment_id)


def approve_deployment(
    deployment: StrategyDeployment,
    *,
    approver: str,
    notes: str,
    strategy_hash: str,
    candidate_hash: str,
    expires_at: datetime | None = None,
) -> AuditRecord:
    deployment.status = DeploymentStatus.ENABLED
    deployment.approved_by = approver
    deployment.approved_at = utcnow()
    deployment.approval_notes = notes
    deployment.approved_strategy_hash = strategy_hash
    deployment.approved_candidate_hash = candidate_hash
    deployment.expires_at = expires_at
    deployment.updated_at = utcnow()
    deployment.version += 1
    return AuditRecord(
        event_type="paper_deployment_human_approved",
        entity_type="strategy_deployment",
        entity_id=deployment.deployment_id,
        account_id=deployment.account_id,
        deployment_id=deployment.deployment_id,
        payload={"approver": approver, "strategy_hash": strategy_hash, "candidate_hash": candidate_hash, "expires_at": expires_at.isoformat() if expires_at else None},
    )


def mark_stale(deployment: StrategyDeployment, reasons: list[str]) -> AuditRecord:
    deployment.stale = True
    deployment.stale_reasons = reasons
    deployment.updated_at = utcnow()
    deployment.version += 1
    return AuditRecord(event_type="paper_deployment_marked_stale", entity_type="strategy_deployment", entity_id=deployment.deployment_id, account_id=deployment.account_id, deployment_id=deployment.deployment_id, payload={"reasons": reasons})
