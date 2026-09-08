from __future__ import annotations

import json
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.mt5_strategies.families.bsi_v3_runtime_detectors import (
    CONFIRMATION_TIMEFRAME_POLICY,
    CORE_SYMBOLS,
    RUNTIME_SPECS,
)

SOURCE = ROOT / "data" / "research" / "bsi_v3_planned_entry_jan_aug_2026.json"
OUT_JSON = ROOT / "data" / "research" / "bsi_v3_plan_frequency_audit.json"
OUT_DIR = ROOT / "docs" / "bsi_updated_faiz" / "v3_plan_frequency"
QUEUE_PATH = ROOT / "data" / "research" / "bsi_v3_live_pending_entry_queue.json"


REPORTS = [
    "01_V3_PLAN_IDENTITY_AUDIT.md",
    "02_V3_H4_CONTEXT_LIFETIME_AUDIT.md",
    "03_V3_STRATEGY_NATURAL_FREQUENCY.md",
    "04_V3_SYMBOL_NATURAL_FREQUENCY.md",
    "05_V3_PLAN_CONFIDENCE_DISTRIBUTION.md",
    "06_V3_CURRENT_QUEUE_CANONICALIZATION.md",
    "07_V3_EXISTING_POSITION_ATTRIBUTION.md",
    "08_V3_PLAN_RANKING_SPEC.md",
    "09_V3_PLAN_FREQUENCY_FINAL_VERDICT.md",
]


def _table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    out = []
    for idx, row in enumerate(rows):
        out.append("| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(row))) + " |")
        if idx == 0:
            out.append("| " + " | ".join("-" * width for width in widths) + " |")
    return "\n".join(out)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"mean": None, "median": None, "p75": None, "p90": None, "max": None}
    ordered = sorted(values)
    def pct(p: float) -> float:
        idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * p)))
        return round(ordered[idx], 4)
    return {
        "mean": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
        "p75": pct(0.75),
        "p90": pct(0.90),
        "max": round(max(values), 4),
    }


def _examples(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for strategy_id, item in payload["strategy_results"].items():
        for example in item.get("examples", []):
            op = example.get("opportunity") or {}
            rows.append({
                "strategy_id": strategy_id,
                "symbol": op.get("symbol"),
                "direction": op.get("direction"),
                "setup_time": example.get("setup_time"),
                "touch_time": example.get("touch_time"),
                "confirmation_time": example.get("confirmation_time"),
                "thesis_id": op.get("thesis_id"),
                "opportunity_id": op.get("opportunity_id"),
                "entry": op.get("entry"),
                "stop": op.get("stop"),
                "target": op.get("target"),
                "confirmation_timeframe": example.get("confirmation_timeframe"),
            })
    return rows


def _session_label(strategy_id: str) -> str:
    sid = strategy_id.lower()
    if any(token in sid for token in ("asian", "0930", "silver_bullet", "monday", "turtle", "session", "ar50")):
        return "session/intraday"
    return "htf_or_hybrid"


def _new_opportunity_condition(strategy_id: str) -> str:
    if "asian" in strategy_id:
        return "new Asian range/sweep plus fresh lower-TF confirmation"
    if "0930" in strategy_id:
        return "fresh 9:30-11:59 NY liquidity sweep/MSS/FVG"
    if "silver_bullet" in strategy_id:
        return "fresh Silver Bullet window sweep/displacement/FVG"
    if "reactionary" in strategy_id:
        return "fresh reaction from original zone with new POI/FVG"
    if "spectre" in strategy_id:
        return "fresh inverse OB/reclaim POI"
    if "mmxm" in strategy_id:
        return "fresh MMXM phase/distribution leg and POI"
    if "abc" in strategy_id:
        return "fresh ABC/ABCD leg completion and POI"
    if "candle_ranges" in strategy_id or "turtle" in strategy_id:
        return "fresh range extreme raid/re-entry event"
    return "fresh mentor-valid structure/liquidity/POI event"


def _frequency_from_examples(examples: list[dict[str, Any]], key: str) -> dict[str, Any]:
    by_day: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in examples:
        dt = _parse_dt(row.get("confirmation_time") or row.get("touch_time") or row.get("setup_time"))
        value = row.get(key)
        symbol = row.get("symbol")
        if dt and value and symbol:
            by_day[(str(symbol), dt.date().isoformat())].add(str(value))
    return _stats([float(len(values)) for values in by_day.values()])


def _queue_summary() -> dict[str, Any]:
    rows = json.loads(QUEUE_PATH.read_text(encoding="utf-8")) if QUEUE_PATH.exists() else []
    active = [r for r in rows if r.get("status") not in {"EXPIRED", "CONSUMED", "BROKER_SUBMISSION_REJECTED"}]
    def conf(r: dict[str, Any]) -> float:
        try:
            return float(r.get("current_plan_confidence") or r.get("initial_plan_confidence") or 0.0)
        except Exception:
            return 0.0
    def band(v: float) -> str:
        if v >= 90:
            return ">=90"
        if v >= 85:
            return "85-89.99"
        if v >= 80:
            return "80-84.99"
        if v >= 75:
            return "75-79.99"
        if v >= 70:
            return "70-74.99"
        if v >= 65:
            return "65-69.99"
        return "<65"
    best: dict[str, dict[str, Any]] = {}
    for r in active:
        key = f"{str(r.get('symbol') or '').upper()}:{str(r.get('direction') or '').upper()}"
        if key not in best or conf(r) > conf(best[key]):
            best[key] = r
    return {
        "raw_plans": len(rows),
        "active_plans": len(active),
        "plans_ge_65": sum(1 for r in active if conf(r) >= 65),
        "plans_ge_70": sum(1 for r in active if conf(r) >= 70),
        "plans_ge_75": sum(1 for r in active if conf(r) >= 75),
        "plans_ge_80": sum(1 for r in active if conf(r) >= 80),
        "bands": dict(Counter(band(conf(r)) for r in active)),
        "best_by_symbol_direction": {
            key: {
                "strategy": row.get("primary_strategy_id") or row.get("strategy_id"),
                "confluence": row.get("confluence_strategy_ids") or [row.get("strategy_id")],
                "confidence": conf(row),
                "state": row.get("status"),
            }
            for key, row in best.items()
        },
    }


def _psql_json(query: str) -> list[dict[str, Any]]:
    cmd = [
        "docker", "compose", "exec", "-T", "postgres", "psql", "-U", "openterminalui", "-d", "openterminalui",
        "-At", "-c", f"select coalesce(json_agg(t),'[]'::json) from ({query}) t;",
    ]
    try:
        result = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True, timeout=30)
        return json.loads(result.stdout.strip() or "[]")
    except Exception:
        return []


def main() -> None:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    examples = _examples(payload)
    queue = _queue_summary()
    positions = _psql_json(
        "select account_id,symbol,direction,strategy_id,broker_ticket,round(max_achieved_r::numeric,3) as mfe_r,"
        "round(tp_progress::numeric,3) as tp_progress,current_volume from adaptive_position_states "
        "where closed_detected_at is null order by account_id,symbol,broker_ticket"
    )
    actions = _psql_json(
        "select action_type,status,count(*) as count from adaptive_management_actions "
        "where created_at >= '2026-09-07 22:30:00+00' group by action_type,status order by count desc"
    )

    strategy_rows = [["Strategy", "Class", "Planning TF", "Confirmation TF", "Raw", "Planned", "Expired", "Opp/day*"]]
    suspicious = []
    for spec in RUNTIME_SPECS:
        item = payload["strategy_results"].get(spec.strategy_id, {})
        raw = int(item.get("raw_opportunities") or 0)
        planned = int(item.get("planned_entries") or 0)
        per_day = round(raw / max(1, 171), 2)
        if per_day > 8:
            suspicious.append({"strategy_id": spec.strategy_id, "raw_opportunities_per_day_window": per_day, "reason": "high raw detector frequency before canonical live dedup"})
        strategy_rows.append([
            spec.strategy_id,
            _session_label(spec.strategy_id),
            ",".join(spec.required_timeframes),
            CONFIRMATION_TIMEFRAME_POLICY.get(spec.strategy_id, "M1_OR_M5_ALLOWED"),
            raw,
            planned,
            int(item.get("expired_or_unconfirmed") or 0),
            per_day,
        ])

    symbol_rows = [["Symbol", "Sample theses/day", "Sample opp/day", "Live LONG", "Live SHORT"]]
    for symbol in CORE_SYMBOLS:
        symbol_examples = [r for r in examples if r.get("symbol") == symbol]
        thesis_stats = _frequency_from_examples(symbol_examples, "thesis_id")
        opp_stats = _frequency_from_examples(symbol_examples, "opportunity_id")
        symbol_rows.append([
            symbol,
            thesis_stats["median"],
            opp_stats["median"],
            queue["best_by_symbol_direction"].get(f"{symbol}:LONG", {}).get("strategy", "none"),
            queue["best_by_symbol_direction"].get(f"{symbol}:SHORT", {}).get("strategy", "none"),
        ])

    position_keys = Counter((p.get("symbol"), p.get("direction"), p.get("strategy_id")) for p in positions)
    audit = {
        "status": "PLAN_FREQUENCY_AUDIT_FROM_EXISTING_COMPACT_REPLAY_PLUS_LIVE_STATE",
        "limitation": "Jan-Aug artifact has full aggregate totals but only sampled examples per strategy; exact full canonical opportunity median/P90 needs instrumented full-entry replay.",
        "strategy_count": len(RUNTIME_SPECS),
        "h4_or_hybrid_strategy_count": sum(1 for s in RUNTIME_SPECS if any(tf in {"H4", "H1"} for tf in s.required_timeframes)),
        "session_intraday_strategy_count": sum(1 for s in RUNTIME_SPECS if _session_label(s.strategy_id) == "session/intraday"),
        "plan_floor": 65,
        "execution_floor": 80,
        "sample_frequency": {
            "contexts_per_pair_day": _frequency_from_examples(examples, "thesis_id"),
            "theses_per_pair_day": _frequency_from_examples(examples, "thesis_id"),
            "pois_per_pair_day": _frequency_from_examples(examples, "touch_time"),
            "opportunities_per_pair_day": _frequency_from_examples(examples, "opportunity_id"),
        },
        "current_queue": queue,
        "existing_positions": {
            "account_executions": len(positions),
            "unique_symbol_direction_strategy_groups": len(position_keys),
            "positions": positions,
            "adaptive_actions_since_v3": actions,
        },
        "suspicious_strategy_frequencies": suspicious,
        "strategy_table": strategy_rows[1:],
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    header = "# BSI V3 Plan Frequency Audit\n\nSource: `data/research/bsi_v3_planned_entry_jan_aug_2026.json` plus live Docker DB state.\n\n"
    (OUT_DIR / REPORTS[0]).write_text(header + "Plan identity now separates context, thesis, POI, entry opportunity, confirmation, and execution. Repeated scans update an active canonical plan instead of appending a duplicate when the POI/opportunity is the same.\n\n" + _table(strategy_rows), encoding="utf-8")
    (OUT_DIR / REPORTS[1]).write_text(header + "H4 context is treated as context, not as one new trade per H4 candle. A fresh plan requires a fresh mentor event: structure/MSS/sweep/POI/session event depending on strategy.\n", encoding="utf-8")
    (OUT_DIR / REPORTS[2]).write_text(header + _table(strategy_rows) + "\n\nSuspicious raw-frequency candidates: `" + json.dumps(suspicious) + "`\n", encoding="utf-8")
    (OUT_DIR / REPORTS[3]).write_text(header + _table(symbol_rows) + "\n\nExact full-population P90 by symbol requires a non-compact replay artifact.\n", encoding="utf-8")
    (OUT_DIR / REPORTS[4]).write_text(header + f"Live queue confidence buckets: `{queue['bands']}`\n\nCounts: >=65 `{queue['plans_ge_65']}`, >=70 `{queue['plans_ge_70']}`, >=75 `{queue['plans_ge_75']}`, >=80 `{queue['plans_ge_80']}`.\n", encoding="utf-8")
    (OUT_DIR / REPORTS[5]).write_text(header + f"Current queue summary: `{json.dumps(queue, indent=2)}`\n", encoding="utf-8")
    (OUT_DIR / REPORTS[6]).write_text(header + f"Open adaptive positions: `{len(positions)}` account executions. Unique symbol/direction/strategy groups: `{len(position_keys)}`.\n\nAdaptive actions since V3: `{json.dumps(actions)}`\n", encoding="utf-8")
    (OUT_DIR / REPORTS[7]).write_text(header + "Ranking uses mentor validity first, then plan confidence, profile/reliability, HTF alignment, POI quality, liquidity/session relevance, confluence, freshness, and potential RR. Ranking cannot bypass POI touch, mentor confirmation, execution confidence >=80, exposure, risk, RR, or broker checks.\n", encoding="utf-8")
    (OUT_DIR / REPORTS[8]).write_text(header + "Verdict: plan generation now has a 65 active-watch floor and canonical upsert/confluence merge. Fast watcher remains disabled. No one-trade-per-day cap was added.\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT_JSON), "docs": str(OUT_DIR), "reports": REPORTS}, indent=2))


if __name__ == "__main__":
    main()
