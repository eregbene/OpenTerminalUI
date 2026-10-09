from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "research" / "bsi_v3_jan_aug_full_canonical.json"
OUT_JSON = ROOT / "data" / "research" / "bsi_v3_weekly_positive_step_jan_aug_2026.json"
OUT_MD = ROOT / "docs" / "bsi_updated_faiz" / "v3_validation" / "BSI_V3_WEEKLY_POSITIVE_STEP_JAN_AUG_2026.md"

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
END = datetime(2026, 9, 1, tzinfo=timezone.utc)
MAX_TRADES_PER_WEEK = 2
STARTING_WEEK_TARGET_USD = 10.0
WEEKLY_TARGET_STEP_USD = 10.0


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _loads(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda _value: float("inf"))


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        return default
    if number == float("inf") or number == float("-inf"):
        return default
    return number


def _candidate(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("simulated_execution") != "TRADE":
        return None
    outcome = row.get("outcome") or {}
    entry_time = _dt(outcome.get("entry_time") or row.get("execution_timestamp") or row.get("timestamp"))
    if entry_time < START or entry_time >= END:
        return None
    if entry_time.weekday() > 4:
        return None
    managed_r = _finite(outcome.get("managed_r"))
    if managed_r <= 0:
        return None
    return {
        "entry_time": entry_time.isoformat(),
        "exit_time": outcome.get("exit_time"),
        "iso_year": entry_time.isocalendar().year,
        "iso_week": entry_time.isocalendar().week,
        "weekday": entry_time.strftime("%A"),
        "symbol": row.get("symbol") or outcome.get("symbol"),
        "direction": row.get("direction") or outcome.get("direction"),
        "strategy_id": row.get("strategy_id") or outcome.get("strategy_id"),
        "execution_confidence": _finite(row.get("execution_confidence")),
        "rr": _finite(row.get("rr")),
        "outcome_status": outcome.get("status"),
        "managed_r": managed_r,
        "source_opportunity_id": row.get("source_opportunity_id") or outcome.get("opportunity_id"),
    }


def _select(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for item in candidates:
        grouped[(item["iso_year"], item["iso_week"])].append(item)

    selected: list[dict[str, Any]] = []
    for week_index, (week, rows) in enumerate(sorted(grouped.items()), start=1):
        target_usd = STARTING_WEEK_TARGET_USD + (week_index - 1) * WEEKLY_TARGET_STEP_USD
        rows.sort(
            key=lambda row: (
                row["managed_r"],
                row["execution_confidence"],
                row["rr"],
                row["entry_time"],
            ),
            reverse=True,
        )
        used_symbols: set[str] = set()
        picked: list[dict[str, Any]] = []
        for row in rows:
            symbol = str(row["symbol"])
            if symbol in used_symbols and len({str(item["symbol"]) for item in rows}) >= MAX_TRADES_PER_WEEK:
                continue
            picked.append(
                {
                    **row,
                    "week_key": f"{week[0]}-W{week[1]:02d}",
                    "sequence_week": week_index,
                    "weekly_target_usd": round(target_usd, 2),
                    "profit_usd": round(target_usd, 2),
                    "risk_usd_to_make_target": round(target_usd / row["managed_r"], 2),
                }
            )
            used_symbols.add(symbol)
            if len(picked) >= MAX_TRADES_PER_WEEK:
                break
        selected.extend(picked)
    selected.sort(key=lambda row: row["entry_time"])
    return selected


def _markdown_table(rows: list[list[Any]]) -> str:
    widths = [max(len(str(row[index])) for row in rows) for index in range(len(rows[0]))]
    lines = []
    for idx, row in enumerate(rows):
        lines.append("| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(row))) + " |")
        if idx == 0:
            lines.append("| " + " | ".join("-" * width for width in widths) + " |")
    return "\n".join(lines)


def main() -> None:
    source = _loads(SOURCE)
    candidates = [item for row in source.get("event_rows", []) if (item := _candidate(row))]
    selected = _select(candidates)
    by_symbol = Counter(str(row["symbol"]) for row in selected)
    by_weekday = Counter(str(row["weekday"]) for row in selected)
    total_profit = round(sum(row["profit_usd"] for row in selected), 2)
    total_risk = round(sum(row["risk_usd_to_make_target"] for row in selected), 2)
    summary = {
        "methodology": "BSI_V3_HISTORICAL_POSITIVE_ONLY_WEEKLY_STEP_TARGET_SIMULATION",
        "source": str(SOURCE.relative_to(ROOT)),
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "rules": {
            "max_trades_per_week": MAX_TRADES_PER_WEEK,
            "symbols": source.get("symbols", []),
            "positive_filter": "only historical trades with managed_r > 0 are eligible",
            "weekly_profit_target_usd": "week 1 = 10, week 2 = 20, then +10 each week",
            "sizing": "risk_usd_to_make_target = weekly_target_usd / managed_r",
        },
        "summary": {
            "eligible_positive_trades": len(candidates),
            "selected_trades": len(selected),
            "weeks_with_selected_trades": len({row["week_key"] for row in selected}),
            "total_profit_usd": total_profit,
            "total_risk_usd_if_all_stopped": total_risk,
            "average_profit_per_trade_usd": round(total_profit / len(selected), 2) if selected else 0.0,
            "average_risk_usd_to_make_target": round(mean(row["risk_usd_to_make_target"] for row in selected), 2) if selected else 0.0,
            "symbols_used": dict(sorted(by_symbol.items())),
            "weekdays_used": dict(sorted(by_weekday.items())),
        },
        "selected_trades": selected,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    preview = [["Week #", "ISO Week", "Date", "Day", "Symbol", "Dir", "Strategy", "R", "Target $", "Risk $"]]
    for row in selected[:40]:
        preview.append(
            [
                row["sequence_week"],
                row["week_key"],
                row["entry_time"][:10],
                row["weekday"],
                row["symbol"],
                row["direction"],
                row["strategy_id"].replace("bsi_v3_", ""),
                row["managed_r"],
                row["profit_usd"],
                row["risk_usd_to_make_target"],
            ]
        )
    OUT_MD.write_text(
        "# BSI V3 Weekly Positive Step Simulation Jan-Aug 2026\n\n"
        "This is a historical positive-only selector, not a guarantee future trades can all be positive. "
        "It filters the canonical Jan-Aug BSI V3 replay to trades that already ended positive, then picks up to two per ISO week across all configured symbols.\n\n"
        f"- Selected trades: `{summary['summary']['selected_trades']}`\n"
        f"- Weeks with trades: `{summary['summary']['weeks_with_selected_trades']}`\n"
        f"- Total stepped profit: `${summary['summary']['total_profit_usd']}`\n"
        f"- Average per selected trade: `${summary['summary']['average_profit_per_trade_usd']}`\n"
        f"- Total risk sizing required if every selected trade had stopped: `${summary['summary']['total_risk_usd_if_all_stopped']}`\n\n"
        "Weekly target: Week 1 $10 per trade, Week 2 $20 per trade, Week 3 $30 per trade, increasing by $10 every week. Max two trades per week.\n\n"
        "## First 40 Selected Trades\n\n"
        + _markdown_table(preview)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"json": str(OUT_JSON), "doc": str(OUT_MD), "summary": summary["summary"]}, indent=2))


if __name__ == "__main__":
    main()
