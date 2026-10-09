from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "scripts" / "bsi_v3_august_validation.py"
RESEARCH_DIR = ROOT / "data" / "research"
DOC_DIR = ROOT / "docs" / "bsi_updated_faiz" / "v3_validation"
PROFILE_RESEARCH_DIR = Path(os.getenv("BSI_V3_PROFILE_OUTPUT_DIR", str(RESEARCH_DIR)))
PROFILE_CURRENT_PATH = Path(os.getenv("BSI_V3_PROFILE_CURRENT_PATH", str(PROFILE_RESEARCH_DIR / "bsi_v3_next_session_profile_current.json")))
DEFAULT_SYMBOLS = "EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD"
NY_TZ = ZoneInfo("America/New_York")
MIN_SIMILAR_PATTERN_TRADES = int(os.getenv("BSI_V3_MIN_SIMILAR_PATTERN_TRADES", "8"))
MIN_SIMILAR_PATTERN_EXPECTANCY = float(os.getenv("BSI_V3_MIN_SIMILAR_PATTERN_EXPECTANCY", "0.02"))
MIN_SIMILAR_PATTERN_PROFIT_FACTOR = float(os.getenv("BSI_V3_MIN_SIMILAR_PATTERN_PROFIT_FACTOR", "1.05"))
PREP_COMPACT_OUTPUT = os.getenv("BSI_V3_PREP_COMPACT_OUTPUT", "false").strip().lower() in {"1", "true", "yes", "on"}


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


def _previous_month_window(value: date) -> tuple[date, date]:
    current = _month_start(value)
    if current.month == 1:
        start = date(current.year - 1, 12, 1)
    else:
        start = date(current.year, current.month - 1, 1)
    return start, current


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
                "BSI_V3_COMPACT_OUTPUT": "true" if PREP_COMPACT_OUTPUT else "false",
            }
        )
        subprocess.run([sys.executable, str(VALIDATION)], cwd=ROOT, env=env, check=True)
    return json.loads(output.read_text(encoding="utf-8"))


def _allowed_map(report: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    rows = report.get("adaptive_manager_tuning", {}).get("allowed_buckets", [])
    return {(row["strategy_id"], row["symbol"]): row for row in rows}


def _outcomes(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in report.get("strategy_results", {}).values():
        rows.extend(item.get("outcomes", []))
    return rows


def _ny_session(value: datetime) -> str:
    local = value.astimezone(NY_TZ)
    hour = local.hour + local.minute / 60.0
    if 2.0 <= hour < 5.0:
        return "LONDON_KILLZONE"
    if 7.0 <= hour < 10.0:
        return "NY_AM_PRE_930"
    if 9.5 <= hour < 11.0:
        return "NY_930_1100"
    if 13.0 <= hour < 16.0:
        return "NY_PM"
    if hour >= 18.0 or hour < 2.0:
        return "ASIA"
    return "OTHER"


def _summarize_values(values: list[float]) -> dict[str, Any]:
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(values) * 100, 2) if values else None,
        "net_r": round(sum(values), 4),
        "expectancy": round(sum(values) / len(values), 4) if values else None,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
    }


def _similar_pattern_profile(report: dict[str, Any], next_day: date) -> dict[tuple[str, str], dict[str, Any]]:
    by_bucket: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    next_weekday = next_day.weekday()
    for row in _outcomes(report):
        entry_raw = row.get("entry_time")
        if not entry_raw:
            continue
        entry_time = datetime.fromisoformat(str(entry_raw).replace("Z", "+00:00"))
        strategy_id = str(row.get("strategy_id") or "")
        symbol = str(row.get("symbol") or "").upper()
        direction = str(row.get("direction") or "").upper()
        value = float(row.get("managed_r") or 0.0)
        key = (strategy_id, symbol)
        by_bucket[key]["all"].append(value)
        by_bucket[key][f"direction:{direction}"].append(value)
        by_bucket[key][f"weekday:{entry_time.weekday()}"].append(value)
        by_bucket[key][f"session:{_ny_session(entry_time)}"].append(value)
        if entry_time.weekday() == next_weekday:
            by_bucket[key]["next_weekday"].append(value)

    profiles: dict[tuple[str, str], dict[str, Any]] = {}
    for key, groups in by_bucket.items():
        best = []
        weak = []
        for label, values in groups.items():
            stats = _summarize_values(values)
            if stats["trades"] < MIN_SIMILAR_PATTERN_TRADES:
                continue
            if (
                (stats["expectancy"] is not None and stats["expectancy"] >= MIN_SIMILAR_PATTERN_EXPECTANCY)
                and (stats["profit_factor"] is not None and stats["profit_factor"] >= MIN_SIMILAR_PATTERN_PROFIT_FACTOR)
            ):
                best.append({"pattern": label, **stats})
            elif stats["net_r"] < 0 or (stats["profit_factor"] is not None and stats["profit_factor"] < 1.0):
                weak.append({"pattern": label, **stats})
        profiles[key] = {
            "positive_patterns": sorted(best, key=lambda item: (item["expectancy"] or 0.0, item["net_r"]), reverse=True)[:5],
            "weak_patterns": sorted(weak, key=lambda item: (item["expectancy"] or 0.0, item["net_r"]))[:5],
        }
    return profiles


def _compose_profile(as_of: date, next_day: date, windows: dict[str, dict[str, Any]], symbols: str) -> dict[str, Any]:
    weights = {
        "year_to_date": 1.0,
        "previous_month": 2.5,
        "month_to_date": 2.0,
        "week_to_date": 3.0,
        "previous_trading_day": 4.0,
    }
    previous_month_stats = windows.get("previous_month", {}).get("portfolio", {}).get("adaptive_filtered_management", {})
    previous_month_shock = (
        float(previous_month_stats.get("net_r") or 0.0) <= -25.0
        or float(previous_month_stats.get("max_drawdown_r") or 0.0) >= 25.0
        or (
            previous_month_stats.get("profit_factor") is not None
            and float(previous_month_stats.get("profit_factor") or 0.0) < 1.0
        )
    )
    similar_patterns = _similar_pattern_profile(windows["year_to_date"], next_day)
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
                    "similar_patterns": {},
                },
            )
            bucket["score"] += weights[window_name]
            bucket["windows"].append(window_name)
            bucket["train_trades"] += int(row.get("trades") or 0)
            bucket["train_net_r"] = round(bucket["train_net_r"] + float(row.get("net_r") or 0.0), 4)
            bucket["similar_patterns"] = similar_patterns.get(key, {"positive_patterns": [], "weak_patterns": []})

    allowed = []
    watchlist = []
    blocked = []
    blocked_previous_month_shock = []
    for row in sorted(bucket_scores.values(), key=lambda item: (-item["score"], item["strategy_id"], item["symbol"])):
        has_long_memory = "year_to_date" in row["windows"]
        has_previous_month_confirmation = "previous_month" in row["windows"]
        has_current_recent_confirmation = any(name in row["windows"] for name in ("month_to_date", "week_to_date", "previous_trading_day"))
        has_recent_confirmation = has_current_recent_confirmation or (previous_month_shock and has_previous_month_confirmation)
        if previous_month_shock and has_long_memory and not has_previous_month_confirmation:
            row["decision"] = "BLOCK_PREVIOUS_MONTH_SHOCK_NO_CONFIRMATION"
            blocked_previous_month_shock.append(row)
            continue
        if not row.get("similar_patterns", {}).get("positive_patterns"):
            row["decision"] = "WATCHLIST_NO_SIMILAR_PATTERN_EDGE"
            watchlist.append(row)
            continue
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
    elif previous_month_shock:
        risk_mode = "HIGH_QUALITY_ONLY_PREVIOUS_MONTH_SHOCK"

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
        "blocked_previous_month_shock_buckets": blocked_previous_month_shock,
        "risk_mode": risk_mode,
        "risk_rules": {
            "pause_new_entries_if_current_month_net_r_lte": -25.0,
            "reduce_or_pause_if_current_month_drawdown_r_gte": 25.0,
            "previous_month_shock_requires_previous_month_bucket_confirmation": True,
            "previous_month_shock_net_r_lte": -25.0,
            "previous_month_shock_drawdown_r_gte": 25.0,
            "previous_month_shock_profit_factor_lt": 1.0,
            "similar_pattern_fields": ["strategy_id", "symbol", "direction", "weekday", "new_york_session"],
            "similar_pattern_history_scope": "current_year_only",
            "min_similar_pattern_trades": MIN_SIMILAR_PATTERN_TRADES,
            "min_similar_pattern_expectancy": MIN_SIMILAR_PATTERN_EXPECTANCY,
            "min_similar_pattern_profit_factor": MIN_SIMILAR_PATTERN_PROFIT_FACTOR,
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
    previous_month_start, previous_month_end = _previous_month_window(as_of)
    windows = {
        "year_to_date": _run_validation(f"{as_of}_ytd", date(as_of.year, 1, 1), as_of + timedelta(days=1), args.symbols, args.refresh),
        "previous_month": _run_validation(f"{as_of}_previous_month", previous_month_start, previous_month_end, args.symbols, args.refresh),
        "month_to_date": _run_validation(f"{as_of}_mtd", _month_start(as_of), as_of + timedelta(days=1), args.symbols, args.refresh),
        "week_to_date": _run_validation(f"{as_of}_wtd", _week_start(as_of), as_of + timedelta(days=1), args.symbols, args.refresh),
        "previous_trading_day": _run_validation(f"{as_of}_prev_day", previous_day, previous_day + timedelta(days=1), args.symbols, args.refresh),
    }
    profile = _compose_profile(as_of, next_day, windows, args.symbols)

    PROFILE_RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    out = PROFILE_RESEARCH_DIR / f"bsi_v3_next_session_profile_{next_day.isoformat()}.json"
    doc = DOC_DIR / f"BSI_V3_NEXT_SESSION_PROFILE_{next_day.isoformat()}.md"
    out.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    PROFILE_CURRENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out, PROFILE_CURRENT_PATH)

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
        f"Blocked previous-month-shock buckets: `{len(profile['blocked_previous_month_shock_buckets'])}`\n\n"
        "The profile uses accumulated year-to-date evidence plus month-to-date, week-to-date, and previous-trading-day confirmation.\n\n"
        + _table(rows)
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"out": str(out), "current": str(PROFILE_CURRENT_PATH), "doc": str(doc), "allowed": len(profile["allowed_buckets"]), "risk_mode": profile["risk_mode"]}, indent=2))


if __name__ == "__main__":
    main()
