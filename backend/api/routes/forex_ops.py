"""Phase 13 (Forex/MT5 roadmap): read-only observability APIs for the analytics built across the
14-phase roadmap -- system degradation state, per-account sequence-risk (Monte Carlo), execution
quality, broker/DB reconciliation, strategy correlation, and edge-decay. Every endpoint here is
read-only: it surfaces analytics that ALREADY exist and ALREADY run on their own schedule
(reconciliation every MT5 cycle, statistics computed from the trusted historical corpus) -- no
endpoint here triggers a new simulation, ingestion, or replay run on every call except the
Monte Carlo endpoint, which is bounded (num_paths capped) and explicitly documented as such."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from backend.auth.deps import get_current_user
from backend.models.user import User

router = APIRouter(prefix="/api/forex-ops", tags=["forex-ops"])


def _orm_dict(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


@router.get("/degradation")
async def degradation_snapshot(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Phase 9: real-time HARD/SOFT/OPTIONAL health of every subsystem, globally and per account."""
    from backend.shared import degradation

    return await degradation.system_snapshot()


@router.get("/sequence-risk/{account_id}")
async def sequence_risk_for_account(account_id: str, num_paths: int = Query(2000, ge=100, le=5000), trades_per_path: int = Query(200, ge=20, le=1000), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Phase 2: Monte Carlo sequence-risk lab for one account. num_paths/trades_per_path are
    capped (see Query bounds) so this endpoint can never be used to run an unbounded simulation."""
    from backend.brokers.mt5.sequence_risk import run_for_account

    return await run_for_account(account_id, num_paths=num_paths, trades_per_path=trades_per_path)


@router.get("/execution-quality/{account_id}")
async def execution_quality_for_account(account_id: str, window: int = Query(200, ge=10, le=1000), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Phase 4: GOOD/DEGRADED/POOR/UNTRUSTED execution-quality classification, overall and per
    symbol, from the already-captured latency/slippage/spread/rejection data on real orders."""
    from backend.portfolio_execution.execution_quality import account_execution_quality

    return account_execution_quality(account_id, window=window)


@router.get("/reconciliation/{account_id}")
async def reconciliation_status(account_id: str, finding_limit: int = Query(50, le=200), current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Phase 3: latest reconciliation verdict + recent findings for one account. Read-only lookup
    of the LATEST already-persisted pass -- never triggers a fresh broker comparison inline."""
    from backend.brokers.mt5.orm import MT5AccountReconciliationFindingORM, MT5AccountReconciliationStatusORM
    from backend.shared.db import SessionLocal

    with SessionLocal() as db:
        status_row = db.get(MT5AccountReconciliationStatusORM, account_id)
        findings = (
            db.query(MT5AccountReconciliationFindingORM)
            .filter(MT5AccountReconciliationFindingORM.account_id == account_id)
            .order_by(MT5AccountReconciliationFindingORM.created_at.desc())
            .limit(finding_limit)
            .all()
        )
    return {
        "account_id": account_id,
        "status": _orm_dict(status_row) if status_row else None,
        "trustworthy": bool(status_row.trustworthy) if status_row else False,
        "recent_findings": [_orm_dict(row) for row in findings],
    }


@router.get("/correlation")
async def correlation_report(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Phase 6: pairwise strategy/symbol/regime return correlation, analytics-only."""
    from backend.historical_intelligence.correlation import full_report

    return full_report()


@router.get("/edge-decay")
async def edge_decay_report(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Phase 7: long-term vs rolling-100/50/20-trade edge decay status, per strategy."""
    from backend.historical_intelligence.edge_decay import all_strategies_edge_decay

    return all_strategies_edge_decay()


@router.get("/profit-retention")
async def profit_retention_report(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Phase 11: milestone-segmented (+0.25R-+2R) management-policy comparison against the
    existing counterfactual-replay corpus, plus a best-policy-per-milestone ranking. Informational
    only -- policy promotion remains exclusively adaptive_management's own manual-approval-gated
    champion/challenger path."""
    from backend.adaptive_management.profit_retention_analysis import best_policy_per_milestone, milestone_policy_comparison

    comparison = milestone_policy_comparison()
    return {**comparison, "best_policy_per_milestone": best_policy_per_milestone(comparison)}


@router.get("/edge-stability")
async def edge_stability_report(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Phase 1: latest persisted walk-forward (train/OOS) edge-stability verdict per strategy --
    the SAME gate entry_intelligence.py's live third gate reads before ever letting historical
    intelligence influence a decision."""
    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM
    from backend.historical_intelligence.walk_forward import latest_edge_stability
    from backend.shared.db import SessionLocal

    with SessionLocal() as db:
        strategies = [row[0] for row in db.query(HistoricalPatternFingerprintORM.anchor_strategy).distinct().all()]
    return {strategy: latest_edge_stability(anchor_strategy=strategy) for strategy in strategies}
