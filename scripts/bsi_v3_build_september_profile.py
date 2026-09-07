from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bsi_v3_august_validation import DETECTOR_REGISTRY, strategy_registry


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "research" / "bsi_v3_forward_adaptive_jan_july_train_august_test.json"
ROLLING_SOURCE = ROOT / "data" / "research" / "bsi_v3_rolling_walk_forward_jan_august_2026.json"
OUT = ROOT / "data" / "research" / "bsi_v3_adaptive_profile_september_2026_demo.json"
DOC = ROOT / "docs" / "bsi_updated_faiz" / "v3_validation" / "BSI_V3_SEPTEMBER_2026_DEMO_ADAPTIVE_PROFILE.md"


def _table(rows: list[list[Any]]) -> str:
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    lines = []
    for idx, row in enumerate(rows):
        lines.append("| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(row))) + " |")
        if idx == 0:
            lines.append("| " + " | ".join("-" * width for width in widths) + " |")
    return "\n".join(lines)


def main() -> None:
    forward = json.loads(SOURCE.read_text(encoding="utf-8"))
    rolling = json.loads(ROLLING_SOURCE.read_text(encoding="utf-8")) if ROLLING_SOURCE.exists() else None
    registry = {spec.strategy_id: spec for spec in strategy_registry()}
    detector_ids = set(DETECTOR_REGISTRY)
    allowed = []
    excluded = []
    for row in forward["allowed_buckets"]:
        strategy_id = row["strategy_id"]
        symbol = row["symbol"]
        if strategy_id in detector_ids:
            allowed.append(
                {
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    "detector_validation": "STRATEGY_SPECIFIC_DETECTOR_REGISTERED",
                    "entry_routing": "DEMO_ALLOWED_FROM_2026-09-08_IF_GLOBAL_V3_FLAGS_ENABLED",
                }
            )
        else:
            excluded.append(
                {
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    "reason": "NO_STRATEGY_SPECIFIC_DETECTOR_REGISTERED",
                }
            )
    for strategy_id, spec in sorted(registry.items()):
        if strategy_id not in detector_ids:
            excluded.append(
                {
                    "strategy_id": strategy_id,
                    "symbol": "*",
                    "reason": "STRATEGY_EXCLUDED_FROM_DEMO_SCOPE_UNTIL_DETECTOR_REGISTERED",
                    "source_videos": spec.source_videos,
                }
            )

    profile = {
        "profile_id": "BSI_V3_ADAPTIVE_PROFILE_SEPTEMBER_2026_DEMO",
        "methodology": "BSI_BASELINE_V3_UPDATED_FAIZ",
        "effective_from_utc": "2026-09-08T00:00:00+00:00",
        "train_window": forward["train_window"],
        "validation_window": forward["test_window"],
        "validation_result": forward["test_forward_adaptive"],
        "source_forward_artifact": str(SOURCE.relative_to(ROOT)),
        "symbols": forward["symbols"],
        "allowed_buckets": allowed,
        "excluded_buckets": excluded,
        "generic_detector_routing_count": 0,
        "unregistered_detector_routing_count": len([item for item in excluded if item["reason"].startswith("NO_STRATEGY")]),
        "demo_routing_contract": {
            "route_only_allowed_buckets": True,
            "route_only_strategy_specific_detectors": True,
            "block_unconverted_strategies": True,
            "sl_moves_must_improve_risk": True,
            "partial_volumes_floor_to_broker_step": True,
            "never_round_partial_volume_up_to_minimum": True,
        },
        "risk_circuit_breakers": {
            "monthly_loss_guard_required": True,
            "basis": "Rolling walk-forward showed July 2026 was negative even after adaptive filtering; profile must pause/tighten after a severe current-month drawdown.",
            "pause_v3_new_entries_after_month_net_r": -25.0,
            "resume_requires_next_month_or_manual_review": True,
        },
        "rolling_walk_forward_summary": rolling["combined_forward_result"] if rolling else None,
        "status": "READY_AS_PROFILE_NOT_YET_CONNECTED_TO_LIVE_ENGINE",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    rows = [["Strategy", "Symbol", "Detector", "Routing"]]
    for row in allowed:
        rows.append([row["strategy_id"], row["symbol"], row["detector_validation"], row["entry_routing"]])
    DOC.write_text(
        "# BSI V3 September 2026 Demo Adaptive Profile\n\n"
        "Effective from UTC: `2026-09-08T00:00:00+00:00`.\n\n"
        f"Train window: `{forward['train_window']['start']}` to `{forward['train_window']['end']}`.\n\n"
        f"Validation window: `{forward['test_window']['start']}` to `{forward['test_window']['end']}`.\n\n"
        f"Validation net R: `{forward['test_forward_adaptive']['net_r']}`. "
        f"Win rate: `{forward['test_forward_adaptive']['win_rate']}`. "
        f"Profit factor: `{forward['test_forward_adaptive']['profit_factor']}`.\n\n"
        f"Allowed demo buckets: `{len(allowed)}`.\n\n"
        "Generic detector routing count: `0`.\n\n"
        "Unconverted strategies are excluded from demo scope until strategy-specific detectors are registered.\n\n"
        "Risk guard: pause V3 new entries if current-month V3 net R reaches `-25R`; July 2026 was the stress case.\n\n"
        + _table(rows)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"out": str(OUT), "doc": str(DOC), "allowed": len(allowed), "excluded": len(excluded)}, indent=2))


if __name__ == "__main__":
    main()
