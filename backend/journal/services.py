from __future__ import annotations


def generate_journal_entry(order: dict, execution: dict, risk: dict, evidence: list[dict] | None = None) -> dict:
  symbol = str(order.get("symbol") or order.get("instrument_id") or "UNKNOWN")
  anomalies = execution.get("anomalies") or []
  narrative = f"{symbol} trade reviewed with deterministic risk={risk.get('decision', 'UNKNOWN')} and {len(anomalies)} execution anomalies."
  return {
    "symbol": symbol,
    "strategy_id": order.get("strategy_id") or order.get("deployment_id"),
    "result": "OPEN" if not order.get("realized_pnl") else "CLOSED",
    "risk_decision": risk.get("decision"),
    "execution_quality": execution,
    "ai_narrative": narrative,
    "evidence": evidence or [],
    "review_status": "PENDING_REVIEW",
  }
