from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "scripts" / "bsi_v3_august_validation.py"
RESEARCH_DIR = ROOT / "data" / "research"
DOC_DIR = ROOT / "docs" / "bsi_updated_faiz" / "v3_validation"
DEFAULT_SYMBOLS = "EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD"


def _parse_yyyy_mm_dd(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _dt(value: date) -> str:
    return datetime.combine(value, time.min, tzinfo=timezone.utc).isoformat()


def _next_trading_day(value: date) -> date:
    candidate = value + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def _previous_trading_day(value: date) -> date:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _label(text: str) -> str:
    return text.lower().replace("-", "_").replace(" ", "_")


def _run_validation(label: str, start: date, end: date, symbols: str, refresh: bool) -> dict[str, Any]:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    output = RESEARCH_DIR / f"bsi_v3_prep_{_label(label)}_full.json"
    golden = RESEARCH_DIR / f"bsi_v3_prep_{_label(label)}_golden.json"
    if refresh or not output.exists():
        env = os.environ.copy()
        env.update(
            {
                "BSI_V3_START": _dt(start),
                "BSI_V3_END": _dt(end),
                "BSI_V3_SYMBOLS": symbols,
                "BSI_V3_GOLDEN_SYMBOLS": symbols,
                "BSI_V3_OUTPUT_JSON": str(output.relative_to(ROOT)),
                "BSI_V3_GOLDEN_OUTPUT_JSON": str(golden.relative_to(ROOT)),
                "BSI_V3_GOLDEN_DOC": f"BSI_V3_PREP_{_label(label).upper()}_GOLDEN_RECONSTRUCTIONS.md",
                "BSI_V3_COMPACT_OUTPUT": "true",
            }
        )
        subprocess.run([sys.executable, str(VALIDATION)], cwd=ROOT, env=env, check=True)
    return json.loads(output.read_text(encoding="utf-8"))


def _allowed_map(report: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    rows = report.get("adaptive_manager_tuning", {}).get("allowed_buckets", [])
    return {(row["strategy_id"], row["symbol"]): row for row in rows}


def _compose_profile(as_of: date, next_day: date, windows: dict[str, dict[str, Any]], symbols: str) -> dict[str, Any]:
    weights = {
        "year_to_date": 1.0,
        "month_to_date": 2.0,
        "week_to_date": 3.0,
        "previous_trading_day": 4.0,
    }
    bucket_scores: dict[tuple[str, str], dict[str, Any]] = {}
    for window_name, report in windows.items():
        for key, row in _allowed_map(report).items():
            bucket = bucket_scores.setdefault(
                key,
                {
                    "strategy_id": key[0],
                    "symbol": key[1],
                    "score": 0.0,
                    "windows": [],
                    "train_trades": 0,
                    "train_net_r": 0.0,
                },
            )
            bucket["score"] += weights[window_name]
            bucket["windows"].append(window_name)
            bucket["train_trades"] += int(row.get("trades") or 0)
            bucket["train_net_r"] = round(bucket["train_net_r"] + float(row.get("net_r") or 0.0), 4)

    allowed = []
    watchlist = []
    blocked = []
    for row in sorted(bucket_scores.values(), key=lambda item: (-item["score"], item["strategy_id"], item["symbol"])):
        has_long_memory = "year_to_date" in row["windows"]
        has_recent_confirmation = any(name in row["windows"] for name in ("month_to_date", "week_to_date", "previous_trading_day"))
        if has_long_memory and has_recent_confirmation:
            row["decision"] = "ALLOW_NEXT_TRADING_DAY"
            allowed.append(row)
        elif has_long_memory:
            row["decision"] = "WATCHLIST_LONG_MEMORY_ONLY"
            watchlist.append(row)
        else:
            row["decision"] = "BLOCK_NO_YEAR_TO_DATE_CONFIRMATION"
            blocked.append(row)

    month_stats = windows["month_to_date"]["portfolio"]["adaptive_filtered_management"]
    risk_mode = "NORMAL"
    if float(month_stats.get("net_r") or 0.0) <= -25.0:
        risk_mode = "PAUSE_NEW_V3_ENTRIES"
    elif float(month_stats.get("max_drawdown_r") or 0.0) >= 25.0:
        risk_mode = "REDUCE_RISK_OR_NEW_ENTRIES"

    return {
        "profile_id": f"BSI_V3_NEXT_SESSION_PROFILE_{next_day.isoformat()}",
        "methodology": "BSI_BASELINE_V3_UPDATED_FAIZ_ROLLING_INTELLIGENCE",
        "as_of_utc_date": as_of.isoformat(),
        "next_trading_day_utc_date": next_day.isoformat(),
        "symbols": [item.strip() for item in symbols.split(",") if item.strip()],
        "windows": {
            name: {
                "start": report["window"]["start"],
                "end": report["window"]["end"],
                "allowed_bucket_count": report["adaptive_manager_tuning"]["allowed_bucket_count"],
                "adaptive_filtered": report["portfolio"]["adaptive_filtered_management"],
            }
            for name, report in windows.items()
        },
        "allowed_buckets": allowed,
        "watchlist_buckets": watchlist,
        "blocked_recent_only_buckets": blocked,
        "risk_mode": risk_mode,
        "risk_rules": {
            "pause_new_entries_if_current_month_net_r_lte": -25.0,
            "reduce_or_pause_if_current_month_drawdown_r_gte": 25.0,
            "use_broker_safe_sl_and_partial_volume_helpers": True,
            "route_only_strategy_specific_detectors": True,
            "generic_detector_routing_count": 0,
        },
        "routing_note": "This prepares next-session V3 demo routing decisions. It does not place trades by itself.",
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
    parser = argparse.ArgumentParser(description="Build a BSI V3 next-session adaptive prep profile.")
    parser.add_argument("--as-of", default=os.getenv("BSI_V3_PREP_AS_OF", date.today().isoformat()))
    parser.add_argument("--symbols", default=os.getenv("BSI_V3_PREP_SYMBOLS", DEFAULT_SYMBOLS))
    parser.add_argument("--refresh", action="store_true", default=os.getenv("BSI_V3_PREP_REFRESH", "false").lower() == "true")
    args = parser.parse_args()

    as_of = _parse_yyyy_mm_dd(args.as_of)
    next_day = _next_trading_day(as_of)
    previous_day = _previous_trading_day(as_of)
    windows = {
        "year_to_date": _run_validation(f"{as_of}_ytd", date(as_of.year, 1, 1), as_of + timedelta(days=1), args.symbols, args.refresh),
        "month_to_date": _run_validation(f"{as_of}_mtd", _month_start(as_of), as_of + timedelta(days=1), args.symbols, args.refresh),
        "week_to_date": _run_validation(f"{as_of}_wtd", _week_start(as_of), as_of + timedelta(days=1), args.symbols, args.refresh),
        "previous_trading_day": _run_validation(f"{as_of}_prev_day", previous_day, previous_day + timedelta(days=1), args.symbols, args.refresh),
    }
    profile = _compose_profile(as_of, next_day, windows, args.symbols)

    out = RESEARCH_DIR / f"bsi_v3_next_session_profile_{next_day.isoformat()}.json"
    doc = DOC_DIR / f"BSI_V3_NEXT_SESSION_PROFILE_{next_day.isoformat()}.md"
    out.write_text(json.dumps(profile, indent=2), encoding="utf-8")

    rows = [["Bucket", "Score", "Windows", "Train Trades", "Train Net R"]]
    for row in profile["allowed_buckets"][:50]:
        rows.append(
            [
                f"{row['strategy_id']} / {row['symbol']}",
                row["score"],
                ",".join(row["windows"]),
                row["train_trades"],
                row["train_net_r"],
            ]
        )
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    doc.write_text(
        "# BSI V3 Next Session Adaptive Prep Profile\n\n"
        f"As-of UTC date: `{profile['as_of_utc_date']}`\n\n"
        f"Next trading day UTC date: `{profile['next_trading_day_utc_date']}`\n\n"
        f"Risk mode: `{profile['risk_mode']}`\n\n"
        f"Allowed next-day buckets: `{len(profile['allowed_buckets'])}`\n\n"
        f"Watchlist long-memory buckets: `{len(profile['watchlist_buckets'])}`\n\n"
        f"Blocked recent-only buckets: `{len(profile['blocked_recent_only_buckets'])}`\n\n"
        "The profile uses accumulated year-to-date evidence plus month-to-date, week-to-date, and previous-trading-day confirmation.\n\n"
        + _table(rows)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"out": str(out), "doc": str(doc), "allowed": len(profile["allowed_buckets"]), "risk_mode": profile["risk_mode"]}, indent=2))


if __name__ == "__main__":
    main()
