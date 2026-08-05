from __future__ import annotations

from datetime import datetime, timezone


def build_report_payload(report_type: str, portfolio: dict, sections: dict) -> dict:
  return {
    "report_type": report_type,
    "portfolio": portfolio,
    "sections": sections,
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "historical_inputs_locked": True,
  }
