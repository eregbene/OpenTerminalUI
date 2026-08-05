from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.trading.models import (
    AuditRecord,
    EmergencyControl,
    LedgerEntry,
    PaperAccount,
    PaperFill,
    PaperOrder,
    ReconciliationResult,
    RiskEvaluation,
    StrategyDeployment,
)
from backend.trading.serialization import model_to_jsonable, read_json, write_json


class TradingStore:
    def __init__(self, root: Path | str = "data/trading") -> None:
        self.root = Path(root)
        self.path = self.root / "trading_store.json"

    def load(self) -> dict[str, Any]:
        data = read_json(self.path)
        return {
            "accounts": data.get("accounts", {}),
            "deployments": data.get("deployments", {}),
            "risk_evaluations": data.get("risk_evaluations", {}),
            "orders": data.get("orders", {}),
            "fills": data.get("fills", {}),
            "ledger": data.get("ledger", []),
            "audits": data.get("audits", []),
            "emergency_controls": data.get("emergency_controls", {}),
            "reconciliations": data.get("reconciliations", []),
        }

    def save(self, data: dict[str, Any]) -> None:
        write_json(self.path, data)

    def upsert_account(self, account: PaperAccount) -> None:
        data = self.load()
        data["accounts"][account.account_id] = model_to_jsonable(account)
        self.save(data)

    def upsert_deployment(self, deployment: StrategyDeployment) -> None:
        data = self.load()
        data["deployments"][deployment.deployment_id] = model_to_jsonable(deployment)
        self.save(data)

    def upsert_risk(self, risk: RiskEvaluation) -> None:
        data = self.load()
        data["risk_evaluations"][risk.evaluation_id] = model_to_jsonable(risk)
        self.save(data)

    def upsert_order(self, order: PaperOrder) -> None:
        data = self.load()
        data["orders"][order.order_id] = model_to_jsonable(order)
        self.save(data)

    def append_fill(self, fill: PaperFill) -> None:
        data = self.load()
        data["fills"][fill.fill_id] = model_to_jsonable(fill)
        self.save(data)

    def append_ledger(self, entries: list[LedgerEntry]) -> None:
        data = self.load()
        data["ledger"].extend(model_to_jsonable(entries))
        self.save(data)

    def append_audit(self, records: list[AuditRecord] | AuditRecord) -> None:
        data = self.load()
        rows = records if isinstance(records, list) else [records]
        data["audits"].extend(model_to_jsonable(rows))
        self.save(data)

    def upsert_emergency(self, control: EmergencyControl) -> None:
        data = self.load()
        key = control.account_id or control.deployment_id or control.scope
        data["emergency_controls"][key] = model_to_jsonable(control)
        self.save(data)

    def append_reconciliation(self, result: ReconciliationResult) -> None:
        data = self.load()
        data["reconciliations"].append(model_to_jsonable(result))
        self.save(data)
