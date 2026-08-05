from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from backend.trading.models import OrderIntent, PaperOrderType, RiskDecision, RiskEvaluation, StrategyDeployment, TimeInForce


def validate_order_intent(intent: OrderIntent, deployment: StrategyDeployment, risk: RiskEvaluation) -> list[str]:
    errors: list[str] = []
    if intent.deployment_id != deployment.deployment_id:
        errors.append("intent deployment does not match deployment")
    if risk.deployment_id != deployment.deployment_id or risk.proposal_id != intent.proposal_id:
        errors.append("risk approval does not match intent")
    if risk.decision not in {RiskDecision.APPROVED, RiskDecision.APPROVED_WITH_RESIZE}:
        errors.append("risk decision is not approved")
    if risk.expires_at <= datetime.now(timezone.utc):
        errors.append("risk approval expired")
    if risk.approved_quantity <= 0:
        errors.append("approved quantity must be positive")
    if intent.instrument.minimum_tick <= 0 or intent.instrument.lot_size <= 0:
        errors.append("invalid instrument tick or lot size")
    if intent.limit_price is not None and intent.limit_price % intent.instrument.minimum_tick != Decimal("0"):
        errors.append("limit price is not tick aligned")
    if intent.stop_price is not None and intent.stop_price % intent.instrument.minimum_tick != Decimal("0"):
        errors.append("stop price is not tick aligned")
    if risk.approved_quantity % intent.instrument.lot_size != Decimal("0"):
        errors.append("quantity is not lot aligned")
    if intent.order_type == PaperOrderType.STOP_LIMIT and (intent.stop_price is None or intent.limit_price is None):
        errors.append("stop-limit orders require both stop and limit prices")
    if intent.order_type == PaperOrderType.LIMIT and intent.limit_price is None:
        errors.append("limit orders require limit price")
    if intent.order_type == PaperOrderType.STOP and intent.stop_price is None:
        errors.append("stop orders require stop price")
    if intent.time_in_force in {TimeInForce.IOC, TimeInForce.FOK} and intent.order_type in {PaperOrderType.MARKET_ON_OPEN, PaperOrderType.MARKET_ON_CLOSE}:
        errors.append("unsupported time-in-force for session intent")
    if not intent.idempotency_key:
        errors.append("idempotency key is required")
    return errors
