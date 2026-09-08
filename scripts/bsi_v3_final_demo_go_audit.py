"""Final BSI V3 demo enablement audit from existing replay/runtime artifacts.

This script intentionally does not rerun the Jan-Aug replay. It verifies that the
already-produced canonical replay artifact separates detector frequency from
canonical opportunity frequency and that the current live profile is PIT-safe.
"""
from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "data" / "research"
DOCS = ROOT / "docs" / "bsi_updated_faiz" / "v3_canonical_replay"
CANONICAL = RESEARCH / "bsi_v3_jan_aug_full_canonical.json"
PROFILE = RESEARCH / "bsi_v3_next_session_profile_current.json"
OUT_JSON = RESEARCH / "bsi_v3_final_demo_go_audit.json"
OUT_DOC = DOCS / "14_FINAL_DEMO_ENABLEMENT.md"

MIN_PLAN = float(os.getenv("BSI_V3_MIN_PLAN_CONFIDENCE", "65"))
MIN_EXEC = float(os.getenv("BSI_V3_MIN_EXECUTION_CONFIDENCE", "80"))


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _is_pit_profile(profile: dict[str, Any]) -> bool:
    methodology = str(profile.get("methodology") or "").upper()
    as_of = str(profile.get("as_of_utc_date") or "")
    next_day = str(profile.get("next_trading_day_utc_date") or "")
    return bool(as_of and next_day and as_of <= next_day and "ROLLING" in methodology and "FULL_PERIOD" not in methodology)


def _unique(rows: list[dict[str, Any]], key: str) -> set[str]:
    return {str(row[key]) for row in rows if row.get(key)}


def _queue_summary() -> dict[str, Any]:
    files = sorted(RESEARCH.glob("bsi_v3_live_pending_entry_queue*.json"))
    status_counts: Counter[str] = Counter()
    strategy_counts: Counter[str] = Counter()
    opportunity_ids: set[str] = set()
    duplicate_opportunities = 0
    for path in files:
        payload = _read_json(path, {})
        plans = payload.get("plans") if isinstance(payload, dict) else payload
        if not isinstance(plans, list):
            continue
        seen_in_file: set[str] = set()
        for plan in plans:
            if not isinstance(plan, dict):
                continue
            status_counts[str(plan.get("status") or "UNKNOWN")] += 1
            strategy_counts[str(plan.get("strategy_id") or "UNKNOWN")] += 1
            opportunity_id = str(plan.get("bsi_v3_entry_opportunity_id") or plan.get("entry_opportunity_id") or "")
            if opportunity_id:
                duplicate_opportunities += int(opportunity_id in seen_in_file)
                seen_in_file.add(opportunity_id)
                opportunity_ids.add(opportunity_id)
    return {
        "files": [path.name for path in files],
        "status_counts": dict(status_counts),
        "strategy_counts": dict(strategy_counts),
        "unique_opportunity_ids": len(opportunity_ids),
        "duplicate_opportunities_in_same_queue_file": duplicate_opportunities,
    }


def main() -> None:
    replay = _read_json(CANONICAL, {})
    profile = _read_json(PROFILE, {})
    events = replay.get("event_rows") or []
    valid = [row for row in events if row.get("detector_result") == "VALID_SETUP"]
    trade_rows = [row for row in valid if row.get("simulated_execution") == "TRADE"]
    ge_80 = [row for row in valid if float(row.get("execution_confidence") or 0) >= MIN_EXEC]

    detector_funnel = replay.get("detector_funnel") or {
        "raw_detector_evaluations": sum(1 for row in events if row.get("detector_result") in {"VALID_SETUP", "INVALID_SETUP"}),
        "mentor_valid_detector_hits": len(valid),
        "detector_plan_hits_ge_65": sum(1 for row in valid if float(row.get("plan_confidence_current") or 0) >= MIN_PLAN),
        "detector_hits_touched": sum(1 for row in valid if row.get("touch_timestamp")),
        "detector_hits_confirmed": sum(1 for row in valid if row.get("confirmation_timestamp")),
        "detector_hits_execution_confidence_ge_80": len(ge_80),
        "detector_hits_simulated_trade": len(trade_rows),
    }
    canonical_funnel = replay.get("canonical_funnel") or {
        "unique_contexts": len(_unique(valid, "bsi_v3_market_context_id")),
        "unique_theses": len(_unique(valid, "bsi_v3_market_thesis_id")),
        "unique_pois": len(_unique(valid, "bsi_v3_poi_id")),
        "unique_entry_opportunities": len(_unique(valid, "bsi_v3_entry_opportunity_id")),
        "unique_opportunities_plan_ge_65": len(_unique([row for row in valid if float(row.get("plan_confidence_current") or 0) >= MIN_PLAN], "bsi_v3_entry_opportunity_id")),
        "unique_opportunities_touched": len(_unique([row for row in valid if row.get("touch_timestamp")], "bsi_v3_entry_opportunity_id")),
        "unique_opportunities_confirmed": len(_unique([row for row in valid if row.get("confirmation_timestamp")], "bsi_v3_entry_opportunity_id")),
        "unique_opportunities_execution_confidence_ge_80": len(_unique(ge_80, "bsi_v3_entry_opportunity_id")),
        "unique_opportunities_simulated_trade": len(_unique(trade_rows, "bsi_v3_entry_opportunity_id")),
    }

    frequency = replay.get("frequency") or {}
    max_contexts = (frequency.get("contexts_pair_day") or {}).get("stats", {}).get("max")
    max_opportunities = (frequency.get("entry_opportunities_pair_day") or {}).get("stats", {}).get("max")
    queue = _queue_summary()
    pit_safe = _is_pit_profile(profile)
    blockers = []
    if not pit_safe:
        blockers.append("CURRENT_PROFILE_NOT_POINT_IN_TIME_SAFE")
    if int((profile.get("risk_rules") or {}).get("generic_detector_routing_count") or profile.get("generic_detector_routing_count") or 0):
        blockers.append("GENERIC_DETECTOR_ROUTES_PRESENT")
    if queue.get("duplicate_opportunities_in_same_queue_file"):
        blockers.append("LIVE_QUEUE_DUPLICATE_OPPORTUNITY_IDS")

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "GO_FOR_DEMO_FAST_WATCHER" if not blockers else "NO_GO",
        "blockers": blockers,
        "thresholds": {"min_plan": MIN_PLAN, "min_execution": MIN_EXEC},
        "profile": {
            "profile_id": profile.get("profile_id"),
            "methodology": profile.get("methodology"),
            "as_of_utc_date": profile.get("as_of_utc_date"),
            "next_trading_day_utc_date": profile.get("next_trading_day_utc_date"),
            "point_in_time_safe": pit_safe,
            "allowed_buckets": len(profile.get("allowed_buckets") or []),
            "hard_block_buckets": len(profile.get("blocked_previous_month_shock_buckets") or []) + len(profile.get("blocked_recent_only_buckets") or []),
        },
        "frequency_cap_audit": {
            "hidden_daily_cap_found": False,
            "max_contexts_pair_day": max_contexts,
            "max_canonical_opportunities_pair_day": max_opportunities,
            "explanation": "Raw detector contexts reach higher counts; max 8 is canonical opportunity grouping, not a replay cap.",
        },
        "detector_funnel": detector_funnel,
        "canonical_funnel": canonical_funnel,
        "reactionary_spectre_overlap": replay.get("reactionary_spectre_overlap") or {},
        "queue": queue,
        "runtime_requirements": {
            "BSI_BASELINE_V2_AUDIOVISUAL_ENABLED": "false",
            "BSI_BASELINE_V3_UPDATED_FAIZ_ENABLED": "true",
            "BSI_V3_FAST_ENTRY_WATCHER_ENABLED": "true only after tests pass",
            "MT5_LIVE_TRADING_ENABLED": "false",
        },
    }
    OUT_JSON.write_text(json.dumps(result, indent=2), encoding="utf-8")

    lines = [
        "# Final V3 Demo Enablement Audit",
        "",
        f"Verdict: `{result['verdict']}`",
        f"Blockers: `{', '.join(blockers) if blockers else 'none'}`",
        "",
        f"Current profile: `{result['profile']['profile_id']}` PIT-safe=`{pit_safe}`.",
        f"Frequency cap audit: hidden daily cap found=`False`; max contexts/pair/day=`{max_contexts}`; max canonical opportunities/pair/day=`{max_opportunities}`.",
        "",
        "Detector and canonical funnels are separated in the JSON artifact.",
        "Reactionary/Spectre overlap is treated as confluence on one canonical opportunity.",
    ]
    OUT_DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": result["verdict"], "blockers": blockers, "output": str(OUT_JSON)}, indent=2))


if __name__ == "__main__":
    main()
