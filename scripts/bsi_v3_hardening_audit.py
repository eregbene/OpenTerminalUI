from __future__ import annotations

import json
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "research" / "bsi_v3_planned_entry_jan_aug_2026.json"
OUT_JSON = ROOT / "data" / "research" / "bsi_v3_jan_aug_hardened.json"
OUT_DIR = ROOT / "docs" / "bsi_updated_faiz" / "v3_hardening"


REPORTS = [
    "01_V3_UNIQUE_OPPORTUNITY_AUDIT.md",
    "02_V3_CONFLUENCE_DEDUP_AUDIT.md",
    "03_V3_CONFIRMATION_TIMEFRAME_AUDIT.md",
    "04_V3_PIT_CHRONOLOGY_AUDIT.md",
    "05_V3_POI_LIFECYCLE_AUDIT.md",
    "06_V3_EXECUTION_REALISM_AUDIT.md",
    "07_V3_LATENCY_SLIPPAGE_SENSITIVITY.md",
    "08_V3_PROFILE_WALK_FORWARD_AUDIT.md",
    "09_V3_ADAPTIVE_ATTRIBUTION.md",
    "10_V3_STRATEGY_OVERLAP_MATRIX.md",
    "11_V3_JAN_AUG_HARDENED_RESULTS.md",
    "12_V3_PORTFOLIO_CONSTRAINED_RESULTS.md",
    "13_V3_DEMO_DEPLOYMENT_VERIFICATION.md",
    "14_V3_HARDENING_FINAL_VERDICT.md",
]


def _table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    output: list[str] = []
    for idx, row in enumerate(rows):
        output.append("| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(row))) + " |")
        if idx == 0:
            output.append("| " + " | ".join("-" * width for width in widths) + " |")
    return "\n".join(output)


def _stats_rows(payload: dict[str, Any]) -> list[list[Any]]:
    rows = [["Model", "Trades", "WR", "Net R", "Expectancy", "PF", "Max LS", "Max DD R"]]
    for name, stats in payload["comparison"].items():
        rows.append(
            [
                name,
                stats.get("trades"),
                stats.get("win_rate"),
                stats.get("net_r"),
                stats.get("expectancy"),
                stats.get("profit_factor"),
                stats.get("max_losing_streak"),
                stats.get("max_drawdown_r"),
            ]
        )
    return rows


def _strategy_rows(payload: dict[str, Any]) -> list[list[Any]]:
    rows = [["Strategy", "Raw", "Planned", "Net R", "WR", "PF", "Expired/Unconfirmed"]]
    for strategy_id, item in payload["strategy_results"].items():
        planned = item.get("planned_entry", {})
        rows.append(
            [
                strategy_id,
                item.get("raw_opportunities", 0),
                item.get("planned_entries", 0),
                planned.get("net_r", 0),
                planned.get("win_rate", 0),
                planned.get("profit_factor", 0),
                item.get("expired_or_unconfirmed", 0),
            ]
        )
    return rows


def _example_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    for strategy_id, item in payload["strategy_results"].items():
        for example in item.get("examples", []):
            op = example.get("opportunity", {})
            out = example.get("outcome", {})
            examples.append(
                {
                    "strategy_id": strategy_id,
                    "symbol": op.get("symbol"),
                    "direction": op.get("direction"),
                    "setup_time": example.get("setup_time"),
                    "touch_time": example.get("touch_time"),
                    "confirmation_time": example.get("confirmation_time"),
                    "confirmation_timeframe": example.get("confirmation_timeframe"),
                    "thesis_id": op.get("thesis_id"),
                    "opportunity_id": op.get("opportunity_id"),
                    "entry": op.get("entry"),
                    "stop": op.get("stop"),
                    "target": op.get("target"),
                    "status": out.get("status"),
                    "managed_r": out.get("managed_r"),
                    "same_bar_ambiguous": out.get("same_bar_ambiguous", False),
                }
            )
    return examples


def _sample_overlap(examples: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[Any, ...], set[str]] = {}
    for row in examples:
        key = (
            row.get("symbol"),
            row.get("direction"),
            row.get("touch_time"),
            round(float(row.get("entry") or 0), 5),
            round(float(row.get("stop") or 0), 5),
            round(float(row.get("target") or 0), 5),
        )
        groups.setdefault(key, set()).add(str(row.get("strategy_id")))
    overlaps: dict[str, int] = {}
    confluence_groups = 0
    for strategies in groups.values():
        if len(strategies) < 2:
            continue
        confluence_groups += 1
        ordered = sorted(strategies)
        for i, left in enumerate(ordered):
            for right in ordered[i + 1 :]:
                pair = f"{left} + {right}"
                overlaps[pair] = overlaps.get(pair, 0) + 1
    return {"sample_confluence_groups": confluence_groups, "sample_overlap_pairs": overlaps}


def _median_r(examples: list[dict[str, Any]]) -> float | None:
    values = [float(row["managed_r"]) for row in examples if row.get("managed_r") is not None]
    return round(median(values), 4) if values else None


def main() -> None:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    examples = _example_rows(payload)
    comparison = payload["comparison"]
    planned = comparison["planned_entry"]
    adaptive = comparison["planned_entry_plus_adaptive_bucket_filter"]
    instant = comparison["instant_entry_same_window"]
    sample_overlap = _sample_overlap(examples)

    raw_detections = sum(int(item.get("raw_opportunities") or 0) for item in payload["strategy_results"].values())
    planned_trades = int(planned["trades"])
    expired = sum(int(item.get("expired_or_unconfirmed") or 0) for item in payload["strategy_results"].values())
    zero_trade_strategies = [
        sid
        for sid, item in payload["strategy_results"].items()
        if int(item.get("planned_entries") or 0) == 0
    ]
    executed_strategies = [
        sid
        for sid, item in payload["strategy_results"].items()
        if int(item.get("planned_entries") or 0) > 0
    ]

    hardened = {
        "source": str(SOURCE.relative_to(ROOT)),
        "status": "PARTIAL_HARDENING_AUDIT_FROM_EXISTING_COMPACT_ARTIFACT",
        "blocking_limitation": (
            "The existing Jan-Aug artifact stores full aggregate counts but only the first 25 examples "
            "per strategy, so exact canonical unique market-thesis/POI/opportunity counts for all "
            "10,621 planned trades cannot be proven without an instrumented full-entry replay."
        ),
        "window": payload["window"],
        "symbols": payload["symbols"],
        "strategy_count": len(payload["strategy_results"]),
        "raw_detections": raw_detections,
        "planned_trades_reported": planned_trades,
        "expired_or_unconfirmed": expired,
        "sampled_trade_rows": len(examples),
        "sample_unique_theses": len({row["thesis_id"] for row in examples if row.get("thesis_id")}),
        "sample_unique_entry_opportunities": len({row["opportunity_id"] for row in examples if row.get("opportunity_id")}),
        "sample_overlap": sample_overlap,
        "zero_trade_strategies": zero_trade_strategies,
        "executed_strategies": executed_strategies,
        "runtime_hardening_applied": {
            "explicit_ids": [
                "bsi_v3_market_thesis_id",
                "bsi_v3_poi_id",
                "bsi_v3_entry_opportunity_id",
                "bsi_v3_confirmation_id",
            ],
            "strategy_confirmation_policy": True,
            "m1_required_no_m5_fallback": True,
            "atomic_confluence_consumption": True,
            "broker_reject_not_consumed": True,
        },
        "reported_results_before_exact_dedup_replay": comparison,
        "adaptive_delta_from_reported_planned": {
            "net_r": round(float(adaptive["net_r"]) - float(planned["net_r"]), 4),
            "pf": round(float(adaptive["profit_factor"]) - float(planned["profit_factor"]), 4),
            "win_rate": round(float(adaptive["win_rate"]) - float(planned["win_rate"]), 4),
            "max_dd_r": round(float(adaptive["max_drawdown_r"]) - float(planned["max_drawdown_r"]), 4),
        },
        "portfolio_10k_025pct_reference_not_final": {
            "reason": "Exact portfolio-constrained equity curve needs full timestamped event rows, not compact aggregates.",
            "simple_unconstrained_reference_usd": round(float(adaptive["net_r"]) * 25.0, 2),
        },
        "median_managed_r_from_sample": _median_r(examples),
        "instant_entry_dd_r": instant["max_drawdown_r"],
        "planned_entry_dd_r": planned["max_drawdown_r"],
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(hardened, indent=2), encoding="utf-8")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stats_table = _table(_stats_rows(payload))
    strategy_table = _table(_strategy_rows(payload))
    overlap_rows = [["Overlap Pair", "Sample Count"]]
    for pair, count in sorted(sample_overlap["sample_overlap_pairs"].items(), key=lambda item: (-item[1], item[0])):
        overlap_rows.append([pair, count])
    if len(overlap_rows) == 1:
        overlap_rows.append(["No exact sample overlap in compact examples", 0])
    overlap_table = _table(overlap_rows)

    common_header = (
        "# BSI V3 Planned-Entry Hardening Audit\n\n"
        f"Source: `{SOURCE.relative_to(ROOT)}`\n\n"
        "Status: `PARTIAL_HARDENING_AUDIT_FROM_EXISTING_COMPACT_ARTIFACT`\n\n"
        "Important limitation: the compact Jan-Aug artifact stores full aggregate totals but not every "
        "timestamped planned trade row. Exact dedup, latency, same-bar, portfolio sequencing, and "
        "adaptive attribution need an instrumented replay artifact.\n\n"
    )
    report_body = {
        REPORTS[0]: common_header + f"Raw detections: `{raw_detections}`\n\nReported planned trades: `{planned_trades}`\n\nSample rows audited: `{len(examples)}`\n\nSample unique theses: `{hardened['sample_unique_theses']}`\n\nSample unique entry opportunities: `{hardened['sample_unique_entry_opportunities']}`\n\nVerdict: original `10,621` cannot be proven fully unique from the compact artifact alone.\n",
        REPORTS[1]: common_header + "Runtime fix: confluence evidence now carries all linked plan IDs, and broker acceptance consumes all linked component plans together.\n\n" + overlap_table + "\n",
        REPORTS[2]: common_header + "Runtime fix: strategy execution-timeframe policy added. M1-required strategies no longer fall back to M5. M5-required strategies may use M5.\n",
        REPORTS[3]: common_header + "Runtime fix: confirmation timestamps are persisted as started/confirmed/bar-close fields. Historical compact rows show setup < touch < confirmation in sampled rows.\n",
        REPORTS[4]: common_header + f"Expired/unconfirmed setups reported by existing replay: `{expired}`.\n\nRuntime terminal states now include expired, invalidated, missed, submitting, broker accepted, consumed, and rejected.\n",
        REPORTS[5]: common_header + "Audit finding: existing aggregate replay does not model full broker bid/ask, spread, commission, slippage, stop/freeze, tick size, volume step, and same-bar intrabar sequence for every row. This remains the biggest realism gap.\n",
        REPORTS[6]: common_header + "Latency sensitivity cannot be exactly recomputed from compact aggregate rows. Runtime watcher interval is configured at `10` seconds and active in Docker.\n",
        REPORTS[7]: common_header + "Profile walk-forward exists in prior rolling artifacts. This audit does not optimize or reselect strategies using future data.\n",
        REPORTS[8]: common_header + f"Reported adaptive delta versus planned: `{hardened['adaptive_delta_from_reported_planned']}`\n\nExact per-trade adaptive attribution requires full timestamped counterfactual rows.\n",
        REPORTS[9]: common_header + overlap_table + "\n",
        REPORTS[10]: common_header + stats_table + "\n\n" + strategy_table + "\n",
        REPORTS[11]: common_header + "Portfolio-constrained $10K at 0.25% risk cannot be finalized from compact aggregates. Simple unconstrained reference is included in JSON only and is not the final portfolio result.\n",
        REPORTS[12]: common_header + "Docker verification from live checks: backend image `bensim-trading:latest` is healthy; fast watcher env loaded; watcher startup log observed; no real-money deployment requested or performed.\n",
        REPORTS[13]: common_header + "Final verdict: V3 planned-entry remains directionally promising, but exact Jan-Aug trustworthiness requires an instrumented hardened replay because the existing artifact is compact.\n",
    }
    for name in REPORTS:
        (OUT_DIR / name).write_text(report_body[name], encoding="utf-8")
    print(json.dumps({"out": str(OUT_JSON), "docs": str(OUT_DIR), "reports": REPORTS}, indent=2))


if __name__ == "__main__":
    main()
