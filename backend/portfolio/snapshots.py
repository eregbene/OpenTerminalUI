from __future__ import annotations

from backend.portfolio.accounting import stable_hash


SNAPSHOT_CATEGORIES = {
  "account",
  "cash",
  "position",
  "portfolio_equity",
  "strategy_equity",
  "exposure",
  "risk",
  "performance",
  "broker_reconciliation",
  "operational_health",
}


def snapshot_content_hash(portfolio_id: str, category: str, payload: dict) -> str:
  return stable_hash({"portfolio_id": portfolio_id, "category": category, "payload": payload})
