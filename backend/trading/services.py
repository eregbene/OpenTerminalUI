from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from typing import Any

from backend.trading.accounts import create_paper_account
from backend.trading.deployments import approve_deployment, mark_stale, request_approval
from backend.trading.execution import PaperExecutionSimulator
from backend.trading.models import (
    AuditRecord,
    EmergencyControl,
    EmergencyStatus,
    InstrumentReference,
    LedgerEntry,
    MarketReference,
    OrderCancellation,
    OrderIntent,
    OrderStatus,
    PaperAccount,
    PaperFill,
    PaperOrder,
    PortfolioSnapshot,
    PnLSnapshot,
    ExposureSnapshot,
    RiskEvaluation,
    StrategyDeployment,
)
from backend.trading.oms import OmsService
from backend.trading.portfolio import PortfolioLedger
from backend.trading.persistence import TradingStore
from backend.trading.reconciliation import reconcile_account
from backend.trading.risk import RiskEngine


class TradingControlService:
    def __init__(self, store: TradingStore | None = None) -> None:
        self.store = store or TradingStore()
        self.risk = RiskEngine()
        self.oms = OmsService()
        self.execution = PaperExecutionSimulator()
        self.ledger = PortfolioLedger()

    def _data(self) -> dict[str, Any]:
        return self.store.load()

    def create_account(self, name: str, initial_cash: Decimal, base_currency: str = "USD") -> PaperAccount:
        account, ledger_entry, audit = create_paper_account(name, initial_cash, base_currency)
        self.store.upsert_account(account)
        self.store.append_ledger([ledger_entry])
        self.store.append_audit(audit)
        return account

    def create_deployment(
        self,
        *,
        account_id: str,
        candidate_id: str,
        strategy_id: str,
        strategy_version: str,
        instrument: InstrumentReference,
        selected_parameters: dict[str, Any] | None = None,
    ) -> StrategyDeployment:
        deployment = StrategyDeployment(
            account_id=account_id,
            candidate_id=candidate_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            selected_parameters=selected_parameters or {},
            instruments=[instrument],
        )
        audit = request_approval(deployment)
        self.store.upsert_deployment(deployment)
        self.store.append_audit(audit)
        return deployment

    def approve_deployment(self, deployment_id: str, approver: str, notes: str, strategy_hash: str, candidate_hash: str) -> StrategyDeployment:
        deployment = self.get_deployment(deployment_id)
        audit = approve_deployment(deployment, approver=approver, notes=notes, strategy_hash=strategy_hash, candidate_hash=candidate_hash)
        self.store.upsert_deployment(deployment)
        self.store.append_audit(audit)
        return deployment

    def mark_deployment_stale(self, deployment_id: str, reasons: list[str]) -> StrategyDeployment:
        deployment = self.get_deployment(deployment_id)
        audit = mark_stale(deployment, reasons)
        self.store.upsert_deployment(deployment)
        self.store.append_audit(audit)
        return deployment

    def set_emergency_disable(self, account_id: str, enabled: bool, reason: str, updated_by: str = "system") -> EmergencyControl:
        control = EmergencyControl(account_id=account_id, status=EmergencyStatus.ENABLED if enabled else EmergencyStatus.CLEAR, reason=reason, updated_by=updated_by)
        self.store.upsert_emergency(control)
        self.store.append_audit(AuditRecord(event_type="paper_emergency_control_updated", entity_type="emergency_control", entity_id=control.control_id, account_id=account_id, payload={"enabled": enabled, "reason": reason}))
        return control

    def submit_intent(self, intent: OrderIntent, market: MarketReference) -> dict[str, Any]:
        account = self.get_account(intent.account_id)
        deployment = self.get_deployment(intent.deployment_id)
        data = self._data()
        positions = self._positions_for(account.account_id)
        pending_orders = [PaperOrder.model_validate(row) for row in data["orders"].values() if row.get("account_id") == account.account_id and row.get("status") not in {"FILLED", "CANCELLED", "REJECTED", "RISK_REJECTED", "EXPIRED"}]
        emergency = self._emergency_status(account.account_id)
        risk_eval = self.risk.evaluate(
            account=account,
            deployment=deployment,
            intent=intent,
            market=market,
            existing_positions={key: pos.quantity for key, pos in positions.items()},
            pending_orders=pending_orders,
            emergency_status=emergency,
        )
        self.store.upsert_risk(risk_eval)
        self.store.append_audit(AuditRecord(event_type="paper_risk_evaluated", entity_type="risk_evaluation", entity_id=risk_eval.evaluation_id, account_id=account.account_id, deployment_id=deployment.deployment_id, correlation_id=intent.correlation_id, payload={"decision": risk_eval.decision.value, "blocking_reasons": risk_eval.blocking_reasons, "resizing_reasons": risk_eval.resizing_reasons}))
        order, audit = self.oms.create_order(intent=intent, deployment=deployment, risk=risk_eval, existing_orders=pending_orders)
        self.store.append_audit(audit)
        if order is not None:
            self.store.upsert_order(order)
        return {"risk_evaluation": risk_eval, "order": order}

    def simulate_order(self, order_id: str, market: MarketReference) -> dict[str, Any]:
        order = self.get_order(order_id)
        deployment = self.get_deployment(order.deployment_id)
        audits = [
            self.oms.transition(order, OrderStatus.SUBMITTED_TO_SIMULATOR, "submitted to internal simulator"),
            self.oms.transition(order, self.execution.acknowledge(order), "simulator acknowledged order"),
        ]
        fill = self.execution.simulate_next_event_fill(order=order, market=market, execution_model=deployment.execution_model)
        if fill is None:
            self.store.upsert_order(order)
            self.store.append_audit(audits)
            return {"order": order, "fill": None}
        audits.append(self.oms.apply_fill(order, fill.quantity, fill.price))
        self.store.upsert_order(order)
        self.store.append_fill(fill)
        self.store.append_audit(audits)
        instrument = deployment.instruments[0]
        entries = self.ledger.entries_for_fill(fill, instrument.quote_currency)
        self.store.append_ledger(entries)
        self._rebuild_and_store_account(order.account_id, marks={fill.instrument_id: fill.price})
        return {"order": order, "fill": fill, "ledger_entries": entries}

    def cancel_order(self, order_id: str, reason: str, requested_by: str = "system") -> PaperOrder:
        order = self.get_order(order_id)
        audits = self.oms.cancel(order, OrderCancellation(order_id=order_id, reason=reason, requested_by=requested_by))
        self.store.upsert_order(order)
        self.store.append_audit(audits)
        return order

    def snapshot(self, account_id: str) -> PortfolioSnapshot:
        account = self.get_account(account_id)
        positions = list(self._positions_for(account_id).values())
        data = self._data()
        orders = [PaperOrder.model_validate(row) for row in data["orders"].values() if row.get("account_id") == account_id]
        long_exposure = sum((pos.quantity * pos.average_price for pos in positions if pos.quantity > 0), Decimal("0"))
        short_exposure = sum((abs(pos.quantity) * pos.average_price for pos in positions if pos.quantity < 0), Decimal("0"))
        return PortfolioSnapshot(
            account=account_snapshot(account),
            positions=positions,
            orders=orders,
            exposure=ExposureSnapshot(gross=account.gross_exposure, net=account.net_exposure, long=long_exposure, short=short_exposure),
            pnl=PnLSnapshot(realized=account.realized_pnl, unrealized=account.unrealized_pnl, drawdown_percent=drawdown_percent(account)),
            risk_state={"emergency_status": self._emergency_status(account_id).value},
            deployment_state={"active_deployments": [row for row in data["deployments"].values() if row.get("account_id") == account_id]},
        )

    def reconcile(self, account_id: str) -> Any:
        stored = self.get_account(account_id)
        rebuilt = deepcopy(stored)
        entries = [LedgerEntry.model_validate(row) for row in self._data()["ledger"] if row.get("account_id") == account_id]
        rebuilt, _ = self.ledger.rebuild(account=rebuilt, entries=entries)
        result = reconcile_account(stored, rebuilt)
        self.store.append_reconciliation(result)
        return result

    def get_account(self, account_id: str) -> PaperAccount:
        row = self._data()["accounts"].get(account_id)
        if not row:
            raise KeyError(f"paper account not found: {account_id}")
        return PaperAccount.model_validate(row)

    def get_deployment(self, deployment_id: str) -> StrategyDeployment:
        row = self._data()["deployments"].get(deployment_id)
        if not row:
            raise KeyError(f"deployment not found: {deployment_id}")
        return StrategyDeployment.model_validate(row)

    def get_order(self, order_id: str) -> PaperOrder:
        row = self._data()["orders"].get(order_id)
        if not row:
            raise KeyError(f"order not found: {order_id}")
        return PaperOrder.model_validate(row)

    def list_accounts(self) -> list[PaperAccount]:
        return [PaperAccount.model_validate(row) for row in self._data()["accounts"].values()]

    def list_deployments(self) -> list[StrategyDeployment]:
        return [StrategyDeployment.model_validate(row) for row in self._data()["deployments"].values()]

    def list_orders(self, account_id: str | None = None) -> list[PaperOrder]:
        return [PaperOrder.model_validate(row) for row in self._data()["orders"].values() if account_id is None or row.get("account_id") == account_id]

    def _rebuild_and_store_account(self, account_id: str, marks: dict[str, Decimal] | None = None) -> PaperAccount:
        account = self.get_account(account_id)
        entries = [LedgerEntry.model_validate(row) for row in self._data()["ledger"] if row.get("account_id") == account_id]
        rebuilt, _ = self.ledger.rebuild(account=account, entries=entries, marks=marks)
        self.store.upsert_account(rebuilt)
        return rebuilt

    def _positions_for(self, account_id: str) -> dict[str, Any]:
        account = self.get_account(account_id)
        entries = [LedgerEntry.model_validate(row) for row in self._data()["ledger"] if row.get("account_id") == account_id]
        _, positions = self.ledger.rebuild(account=account, entries=entries)
        return positions

    def _emergency_status(self, account_id: str) -> EmergencyStatus:
        row = self._data()["emergency_controls"].get(account_id)
        if not row:
            return EmergencyStatus.CLEAR
        return EmergencyControl.model_validate(row).status


def account_snapshot(account: PaperAccount):
    from backend.trading.models import PaperAccountSnapshot

    return PaperAccountSnapshot(
        account_id=account.account_id,
        cash_balance=account.cash_balance,
        reserved_cash=account.reserved_cash,
        equity=account.equity,
        buying_power=account.buying_power,
        gross_exposure=account.gross_exposure,
        net_exposure=account.net_exposure,
        realized_pnl=account.realized_pnl,
        unrealized_pnl=account.unrealized_pnl,
        fees=account.fees,
    )


def drawdown_percent(account: PaperAccount) -> Decimal:
    if account.high_water_mark <= 0:
        return Decimal("0")
    return (account.high_water_mark - account.equity) / account.high_water_mark * Decimal("100")
