from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from backend.brokers.mt5.v3_opportunity_book import (
    canonicalize_opportunities,
    load_queue_rows,
    select_portfolio_opportunities,
    signal_explosion_summary,
)
from backend.shared.db import engine


DEFAULT_QUEUE = Path(os.getenv("BSI_V3_PLANNED_ENTRY_QUEUE_PATH", "data/research/bsi_v3_live_pending_entry_queue.json"))
DEFAULT_OUT = Path("data/research/bsi_v3_global_opportunity_book_current.json")
DEFAULT_MD = Path("docs/bsi_updated_faiz/v3_validation/BSI_V3_GLOBAL_OPPORTUNITY_BOOK_CURRENT.md")


def _queue_glob(base_path: Path) -> list[Path]:
    base_path = base_path.resolve()
    if base_path.exists():
        return [base_path]
    return sorted(base_path.parent.glob(f"{base_path.stem}_*{base_path.suffix}"))


def _recent_candidate_rows(hours: int) -> list[dict[str, Any]]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = text(
        """
        select account_id, symbol, broker_symbol, direction, timeframe, strategy, session,
               created_at, overall_confidence, raw_trend_score, initial_reward_risk,
               selected, eligible_for_execution, rejection_reasons, strategy_evidence
        from mt5_candidate_evaluations
        where created_at >= :since and overall_confidence >= 80
        order by created_at desc
        """
    )
    rows: list[dict[str, Any]] = []
    with engine.connect() as conn:
        for row in conn.execute(query, {"since": since}).mappings():
            payload = dict(row)
            evidence = payload.pop("strategy_evidence") or {}
            if isinstance(evidence, str):
                try:
                    evidence = json.loads(evidence)
                except json.JSONDecodeError:
                    evidence = {}
            if isinstance(evidence, dict):
                payload.update({key: value for key, value in evidence.items() if str(key).startswith(("bsi_v3_", "v3_"))})
            payload["current_plan_confidence"] = payload.get("raw_trend_score") or payload.get("overall_confidence")
            payload["execution_confidence"] = payload.get("overall_confidence")
            payload["rr"] = payload.get("initial_reward_risk")
            rows.append(payload)
    return rows


def _accepted_trade_count(hours: int) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = text("select count(*) from mt5_order_records where created_at >= :since and status = 'ACCEPTED'")
    with engine.connect() as conn:
        return int(conn.execute(query, {"since": since}).scalar() or 0)


def _build_report(hours: int, queue_path: Path) -> dict[str, Any]:
    queue_rows = load_queue_rows(_queue_glob(queue_path), base_stem=queue_path.stem)
    candidate_rows = _recent_candidate_rows(hours)
    source_rows = queue_rows + candidate_rows
    canonical = canonicalize_opportunities(source_rows)
    selected = select_portfolio_opportunities(canonical)
    summary = signal_explosion_summary(source_rows, selected, accepted_trades=_accepted_trade_count(hours))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lookback_hours": hours,
        "queue_files": [str(path) for path in _queue_glob(queue_path)],
        "summary": summary,
        "top_live_opportunities": selected[:20],
    }


def _write_markdown(report: dict[str, Any], path: Path) -> None:
    summary = report["summary"]
    lines = [
        "# BSI V3 Global Opportunity Book",
        "",
        f"Generated: `{report['generated_at']}`",
        f"Lookback hours: `{report['lookback_hours']}`",
        "",
        "## Signal Compression",
        "",
        f"- Raw >=80 observations: `{summary['raw_observations']}`",
        f"- Unique contexts: `{summary['unique_contexts']}`",
        f"- Unique theses: `{summary['unique_theses']}`",
        f"- Unique POI clusters: `{summary['unique_poi_clusters']}`",
        f"- Unique opportunities: `{summary['unique_opportunities']}`",
        f"- Confirmed opportunities: `{summary['unique_confirmed_opportunities']}`",
        f"- Portfolio-selected opportunities: `{summary['portfolio_selected_opportunities']}`",
        f"- Accepted broker trades: `{summary['accepted_broker_trades']}`",
        f"- Duplicate reduction: `{summary['duplicate_reduction_pct']}%`",
        "",
        "## Top Opportunities",
        "",
    ]
    for row in report["top_live_opportunities"][:10]:
        lines.append(
            f"{row.get('quality_rank')}. `{row.get('symbol')}` `{row.get('direction')}` "
            f"`{row.get('portfolio_decision')}` score=`{row.get('quality_score')}` "
            f"state=`{row.get('status')}` theme=`{row.get('exposure_theme')}` "
            f"strategies=`{','.join(row.get('strategies') or [])}`"
        )
    lines.extend(["", "## Exposure", "", f"```json\n{json.dumps(summary['portfolio_exposure'], indent=2)}\n```", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the live BSI V3 global opportunity book/audit.")
    parser.add_argument("--hours", type=int, default=6)
    parser.add_argument("--queue-path", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MD)
    args = parser.parse_args()

    report = _build_report(args.hours, args.queue_path)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    _write_markdown(report, args.markdown)
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
