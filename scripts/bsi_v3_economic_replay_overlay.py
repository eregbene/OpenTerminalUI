from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.economic_intelligence import calendar_guard
from backend.economic_intelligence.config import economic_intelligence_config
from backend.economic_intelligence.event_mapping import normalize_broker_symbol
from backend.economic_intelligence.persistence import query_events


SOURCE = ROOT / os.getenv("BSI_V3_ECONOMIC_SOURCE", "data/research/bsi_v3_prep_2026_09_07_ytd_full.json")
OUT = ROOT / os.getenv("BSI_V3_ECONOMIC_OUTPUT", "data/research/bsi_v3_economic_overlay_2026_ytd.json")
DOC = ROOT / os.getenv("BSI_V3_ECONOMIC_DOC", "docs/bsi_updated_faiz/v3_validation/BSI_V3_ECONOMIC_OVERLAY_2026_YTD.md")


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _summary(values: list[float]) -> dict[str, Any]:
    wins = [v for v in values if v > 0]
    losses = [v for v in values if v <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    equity = peak = max_dd = 0.0
    losing = max_losing = 0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if value <= 0:
            losing += 1
        else:
            losing = 0
        max_losing = max(max_losing, losing)
    return {
        "trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(values) * 100, 2) if values else None,
        "net_r": round(sum(values), 4),
        "expectancy": round(sum(values) / len(values), 4) if values else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "max_losing_streak": max_losing,
        "max_drawdown_r": round(max_dd, 4),
    }


def _outcomes(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in report.get("strategy_results", {}).values():
        rows.extend(item.get("outcomes", []))
    return rows


def _allowed_keys(report: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (row["strategy_id"], row["symbol"])
        for row in report.get("adaptive_manager_tuning", {}).get("allowed_buckets", [])
    }


def _decision_for(outcome: dict[str, Any], config: Any) -> dict[str, Any]:
    entry_time = _dt(outcome["entry_time"])
    parts = normalize_broker_symbol(outcome["symbol"])
    events = query_events(
        currencies=list(parts.currencies),
        start=entry_time - timedelta(hours=1),
        end=entry_time + timedelta(hours=6),
        limit=50,
    )
    decision = calendar_guard.evaluate(list(parts.currencies), entry_time, events, config, spread_normalized=True)
    return {
        "decision": decision["decision"],
        "reason_codes": decision["reason_codes"],
        "size_multiplier": float(decision.get("size_multiplier") or 1.0),
        "minutes_to_event": decision.get("minutes_to_event"),
        "nearest_event": {
            "currency": decision["nearest_event"].get("currency"),
            "impact": decision["nearest_event"].get("impact"),
            "raw_name": decision["nearest_event"].get("raw_name"),
            "scheduled_at_utc": decision["nearest_event"].get("scheduled_at_utc"),
        }
        if decision.get("nearest_event")
        else None,
    }


def _table(rows: list[list[Any]]) -> str:
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    lines = []
    for idx, row in enumerate(rows):
        lines.append("| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(row))) + " |")
        if idx == 0:
            lines.append("| " + " | ".join("-" * width for width in widths) + " |")
    return "\n".join(lines)


def main() -> None:
    report = json.loads(SOURCE.read_text(encoding="utf-8"))
    config = economic_intelligence_config()
    allowed = _allowed_keys(report)
    candidates = [
        row
        for row in _outcomes(report)
        if (row["strategy_id"], row["symbol"]) in allowed and row.get("managed_r") is not None
    ]
    baseline_values = [float(row["managed_r"]) for row in candidates]
    economic_values: list[float] = []
    decisions = {"ALLOW": 0, "BLOCK": 0, "DELAY": 0, "REDUCE_SIZE": 0, "MANAGE_EXISTING_ONLY": 0}
    blocked_examples = []
    reduced_examples = []
    for row in candidates:
        econ = _decision_for(row, config)
        decisions[econ["decision"]] = decisions.get(econ["decision"], 0) + 1
        managed_r = float(row["managed_r"])
        if econ["decision"] in {"BLOCK", "DELAY", "MANAGE_EXISTING_ONLY"}:
            if len(blocked_examples) < 25:
                blocked_examples.append({"outcome": row, "economic": econ})
            continue
        if econ["decision"] == "REDUCE_SIZE":
            managed_r *= econ["size_multiplier"]
            if len(reduced_examples) < 25:
                reduced_examples.append({"outcome": row, "economic": econ})
        economic_values.append(managed_r)

    result = {
        "methodology": "BSI_V3_ADAPTIVE_WITH_SCHEDULED_ECONOMIC_CALENDAR_OVERLAY",
        "source": str(SOURCE.relative_to(ROOT)),
        "window": report.get("window"),
        "symbols": report.get("selected_symbols"),
        "baseline_adaptive": _summary(baseline_values),
        "economic_calendar_overlay": _summary(economic_values),
        "economic_decision_counts": decisions,
        "blocked_or_delayed_examples": blocked_examples,
        "reduced_size_examples": reduced_examples,
        "limitations": [
            "Scheduled Forex Factory calendar events are replayed at each historical entry time.",
            "Unscheduled news classification is not replayed point-in-time here because the current news query path is live-age based and has no as-of parameter.",
            "Spread-normalization is assumed true for the historical overlay; live MT5 still evaluates live spread.",
        ],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    rows = [
        ["Metric", "V3 Adaptive", "V3 Adaptive + Calendar"],
        ["Trades", result["baseline_adaptive"]["trades"], result["economic_calendar_overlay"]["trades"]],
        ["Win Rate", result["baseline_adaptive"]["win_rate"], result["economic_calendar_overlay"]["win_rate"]],
        ["Net R", result["baseline_adaptive"]["net_r"], result["economic_calendar_overlay"]["net_r"]],
        ["Profit Factor", result["baseline_adaptive"]["profit_factor"], result["economic_calendar_overlay"]["profit_factor"]],
        ["Max DD R", result["baseline_adaptive"]["max_drawdown_r"], result["economic_calendar_overlay"]["max_drawdown_r"]],
    ]
    DOC.write_text(
        "# BSI V3 Economic Calendar Overlay Replay\n\n"
        f"Source: `{result['source']}`\n\n"
        + _table(rows)
        + "\n\n"
        f"Economic decisions: `{decisions}`\n\n"
        "This replays scheduled economic calendar protection only. Unscheduled news is not point-in-time replayed yet.\n",
        encoding="utf-8",
    )
    print(json.dumps({"out": str(OUT), "doc": str(DOC), "decisions": decisions, "baseline": result["baseline_adaptive"], "economic": result["economic_calendar_overlay"]}, indent=2))


if __name__ == "__main__":
    main()
