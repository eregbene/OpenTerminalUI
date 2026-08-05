from __future__ import annotations

from decimal import Decimal

from backend.trading.models import (
    AuditRecord,
    OrderCancellation,
    OrderIntent,
    OrderStatus,
    PaperOrder,
    RiskEvaluation,
    StrategyDeployment,
    utcnow,
)
from backend.trading.oms.state_machine import assert_transition
from backend.trading.oms.validation import validate_order_intent


class OmsService:
    def create_order(
        self,
        *,
        intent: OrderIntent,
        deployment: StrategyDeployment,
        risk: RiskEvaluation,
        existing_orders: list[PaperOrder],
    ) -> tuple[PaperOrder | None, AuditRecord]:
        duplicate = next(
            (
                order
                for order in existing_orders
                if order.deployment_id == intent.deployment_id
                and order.proposal_id == intent.proposal_id
                and order.idempotency_key == intent.idempotency_key
            ),
            None,
        )
        if duplicate:
            return duplicate, AuditRecord(
                event_type="paper_order_duplicate_prevented",
                entity_type="paper_order",
                entity_id=duplicate.order_id,
                account_id=intent.account_id,
                deployment_id=intent.deployment_id,
                correlation_id=intent.correlation_id,
                payload={"proposal_id": intent.proposal_id, "idempotency_key": intent.idempotency_key},
            )
        errors = validate_order_intent(intent, deployment, risk)
        if errors:
            return None, AuditRecord(
                event_type="paper_order_rejected",
                entity_type="paper_order",
                account_id=intent.account_id,
                deployment_id=intent.deployment_id,
                correlation_id=intent.correlation_id,
                payload={"errors": errors, "proposal_id": intent.proposal_id},
            )
        order = PaperOrder(
            account_id=intent.account_id,
            strategy_id=intent.strategy_id,
            strategy_version=intent.strategy_version,
            deployment_id=intent.deployment_id,
            instrument_id=intent.instrument.instrument_id,
            asset_class=intent.instrument.asset_class,
            side=intent.side,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            quantity=risk.approved_quantity,
            remaining_quantity=risk.approved_quantity,
            limit_price=intent.limit_price,
            stop_price=intent.stop_price,
            status=OrderStatus.APPROVED,
            risk_evaluation_id=risk.evaluation_id,
            proposal_id=intent.proposal_id,
            correlation_id=intent.correlation_id,
            causation_id=intent.intent_id,
            idempotency_key=intent.idempotency_key,
        )
        return order, AuditRecord(
            event_type="paper_order_created",
            entity_type="paper_order",
            entity_id=order.order_id,
            account_id=order.account_id,
            deployment_id=order.deployment_id,
            correlation_id=order.correlation_id,
            causation_id=order.causation_id,
            payload={"status": order.status.value, "quantity": str(order.quantity)},
        )

    @staticmethod
    def transition(order: PaperOrder, target: OrderStatus, reason: str) -> AuditRecord:
        assert_transition(order.status, target)
        previous = order.status
        order.status = target
        order.updated_at = utcnow()
        order.version += 1
        return AuditRecord(
            event_type="paper_order_state_transition",
            entity_type="paper_order",
            entity_id=order.order_id,
            account_id=order.account_id,
            deployment_id=order.deployment_id,
            correlation_id=order.correlation_id,
            causation_id=order.causation_id,
            payload={"from": previous.value, "to": target.value, "reason": reason},
        )

    def cancel(self, order: PaperOrder, cancellation: OrderCancellation) -> list[AuditRecord]:
        if order.status == OrderStatus.CANCELLED:
            return [
                AuditRecord(
                    event_type="paper_order_cancel_idempotent",
                    entity_type="paper_order",
                    entity_id=order.order_id,
                    account_id=order.account_id,
                    deployment_id=order.deployment_id,
                    payload={"reason": cancellation.reason},
                )
            ]
        audits = [self.transition(order, OrderStatus.CANCEL_PENDING, cancellation.reason)]
        audits.append(self.transition(order, OrderStatus.CANCELLED, cancellation.reason))
        return audits

    def apply_fill(self, order: PaperOrder, fill_quantity: Decimal, fill_price: Decimal) -> AuditRecord:
        filled_before = order.filled_quantity
        total_value = (order.average_fill_price or Decimal("0")) * filled_before + fill_price * fill_quantity
        order.filled_quantity += fill_quantity
        order.remaining_quantity = max(Decimal("0"), order.quantity - order.filled_quantity)
        order.average_fill_price = total_value / order.filled_quantity if order.filled_quantity > 0 else None
        target = OrderStatus.FILLED if order.remaining_quantity == 0 else OrderStatus.PARTIALLY_FILLED
        assert_transition(order.status, target)
        order.status = target
        order.updated_at = utcnow()
        order.version += 1
        return AuditRecord(
            event_type="paper_order_fill_applied",
            entity_type="paper_order",
            entity_id=order.order_id,
            account_id=order.account_id,
            deployment_id=order.deployment_id,
            correlation_id=order.correlation_id,
            payload={"fill_quantity": str(fill_quantity), "fill_price": str(fill_price), "status": target.value},
        )
