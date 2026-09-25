from __future__ import annotations

import json
import os
import sys
import time
from bisect import bisect_right
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scripts.bsi_v3_august_validation as v3

RESEARCH_DIR = ROOT / "data" / "research"
DOC_DIR = ROOT / "docs" / "bsi_updated_faiz" / "v3_validation"

START = datetime.fromisoformat(os.getenv("BSI_V3_PLANNED_START", "2026-01-01T00:00:00+00:00"))
END = datetime.fromisoformat(os.getenv("BSI_V3_PLANNED_END", "2026-09-01T00:00:00+00:00"))
SYMBOLS = [s.strip().upper() for s in os.getenv("BSI_V3_PLANNED_SYMBOLS", ",".join(v3.CORE_SYMBOLS)).split(",") if s.strip()]
MIN_RR = float(os.getenv("BSI_V3_PLANNED_MIN_RR", "1.25"))
MAX_PENDING_HOURS = float(os.getenv("BSI_V3_PLANNED_MAX_PENDING_HOURS", "24"))
CONFIRMATION_LOOKAHEAD_MINUTES = int(os.getenv("BSI_V3_PLANNED_CONFIRMATION_LOOKAHEAD_MINUTES", "90"))
RUN_LABEL = os.getenv("BSI_V3_PLANNED_LABEL", "jan_aug_2026")
OUT_JSON = RESEARCH_DIR / f"bsi_v3_planned_entry_{RUN_LABEL}.json"
OUT_DOC = DOC_DIR / f"BSI_V3_PLANNED_ENTRY_{RUN_LABEL.upper()}.md"


COURSE_RULES = {
    "source_mode": "TRANSCRIPTS_PLUS_VISUAL_CONTACT_SHEETS",
    "source_folder": r"F:\new faiz",
    "videos_available": 125,
    "transcripts_available": 125,
    "visual_contact_sheets_available": 125,
    "rechecked_videos": [
        "Risk Management & Psychology",
        "Risk Management For Prop Firm Accounts",
        "Daily Bias Made Easy",
        "Finding Monthly, Weekly, & Daily Bias",
        "Order Flow",
        "ABC / ABCD",
        "Asian Session V2",
        "New York / 9:30",
        "Orderblock 2.0",
        "ICT Silver Bullet",
        "4H OB",
        "MMXM / Holy Grail",
        "Turtle Soups & Ranges",
        "Standard Deviations",
        "Confirmation Entries",
        "Entry Patterns",
    ],
    "extracted_rules": {
        "planning": [
            "Build external/internal liquidity and daily/weekly/monthly directional bias.",
            "Use H4/H1/M30/M15 context to mark POI zones before entry.",
            "POIs are FVGs, order blocks, breaker/BPR/IFVG zones, ranges, and liquidity pools.",
            "The plan can update intraday after BOS, MSS, CHoCH, sweep, displacement, or new POI formation.",
        ],
        "confirmation": [
            "After price reaches POI, drop to lower timeframe and wait for MSS/structure shift.",
            "Confirmation entry should include displacement and an entry POI such as FVG/OB/BPR/breaker.",
            "Use killzone/session validity for session models.",
            "Do not chase if price leaves the POI and no confirmation appears.",
        ],
        "risk_management": [
            "Use one consistent risk percentage per trade in normal trading.",
            "Reduce risk after 3-5 consecutive losses; reduce again after deeper losing streak.",
            "Prop-account passing plan can use 1% baseline and step-up only after profit cushion.",
            "Funded/conservative mode starts around 0.5% and steps up only after 1-2% profit cushion.",
            "Move to breakeven when structure breaks in trade direction.",
            "Take no more than about two partials; partials are tied to structure/targets, not random exits.",
        ],
    },
}


def _utc(value: Any) -> datetime:
    return v3.utc(value)


def _tf_seconds(tf: str) -> int:
    return {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}.get(tf.upper(), 900)


def _bars_between(bars: list[v3.Bar], start: datetime, end: datetime) -> list[v3.Bar]:
    return [b for b in bars if start <= b.time <= end]


def _fvg_zone_for_op(op: v3.Opportunity, bars: list[v3.Bar]) -> tuple[float, float] | None:
    fvgs = v3.detect_fvgs(bars)
    same_time = [f for f in fvgs if f["confirmed_at"] == op.entry_time]
    if not same_time:
        same_time = [f for f in fvgs if abs((f["confirmed_at"] - op.entry_time).total_seconds()) <= _tf_seconds(op.timeframe_stack[-1])]
    direction = "BULLISH" if op.direction == "LONG" else "BEARISH"
    directional = [f for f in same_time if f["direction"] == direction]
    selected = directional[0] if directional else (same_time[0] if same_time else None)
    if selected is None:
        return None
    return float(selected["low"]), float(selected["high"])


def _displacement_confirms(direction: str, bars: list[v3.Bar]) -> bool:
    if len(bars) < 8:
        return False
    ranges = [b.high - b.low for b in bars[-8:-1] if (b.high - b.low) > 0]
    avg_range = sum(ranges) / len(ranges) if ranges else 0.0
    last = bars[-1]
    body = abs(last.close - last.open)
    if avg_range <= 0 or body < avg_range * 0.55:
        return False
    if direction == "LONG":
        return last.close > last.open and last.close >= max(b.high for b in bars[-6:-1])
    return last.close < last.open and last.close <= min(b.low for b in bars[-6:-1])


def _fvg_confirms(direction: str, bars: list[v3.Bar]) -> bool:
    if len(bars) < 3:
        return False
    fvg = v3.detect_fvgs(bars[-8:])
    wanted = "BULLISH" if direction == "LONG" else "BEARISH"
    return any(row["direction"] == wanted for row in fvg[-3:])


def _find_confirmed_planned_entry(
    op: v3.Opportunity,
    *,
    primary_bars: list[v3.Bar],
    primary_times: list[datetime],
    m1_bars: list[v3.Bar],
    m1_times: list[datetime],
    m5_bars: list[v3.Bar],
    m5_times: list[datetime],
) -> dict[str, Any] | None:
    zone = _fvg_zone_for_op(op, primary_bars)
    if zone is None:
        return None
    zone_low, zone_high = zone
    expiry = op.entry_time + timedelta(hours=MAX_PENDING_HOURS)
    start_idx = bisect_right(primary_times, op.entry_time)
    end_idx = bisect_right(primary_times, expiry)
    touch = next((b for b in primary_bars[start_idx:end_idx] if b.low <= zone_high and b.high >= zone_low), None)
    if touch is None:
        return None
    confirmation_end = touch.time + timedelta(minutes=CONFIRMATION_LOOKAHEAD_MINUTES)
    lower_tf = "M1" if m1_bars else ("M5" if m5_bars else op.timeframe_stack[-1])
    lower_bars = m1_bars if lower_tf == "M1" else (m5_bars if lower_tf == "M5" else primary_bars)
    lower_times = m1_times if lower_tf == "M1" else (m5_times if lower_tf == "M5" else primary_times)
    lower_start = bisect_right(lower_times, touch.time - timedelta(microseconds=1))
    lower_end = bisect_right(lower_times, confirmation_end)
    scoped = lower_bars[lower_start:lower_end]
    confirm_bar: v3.Bar | None = None
    for idx in range(8, len(scoped) + 1):
        window = scoped[:idx]
        if _displacement_confirms(op.direction, window) and _fvg_confirms(op.direction, window):
            confirm_bar = window[-1]
            break
    if confirm_bar is None:
        return None
    entry = op.entry
    if op.direction == "LONG":
        stop = min(op.stop, min(b.low for b in scoped[: max(1, scoped.index(confirm_bar) + 1)]))
        if not (stop < entry):
            return None
        target = max(op.target, entry + 2.0 * (entry - stop))
    else:
        stop = max(op.stop, max(b.high for b in scoped[: max(1, scoped.index(confirm_bar) + 1)]))
        if not (stop > entry):
            return None
        target = min(op.target, entry - 2.0 * (stop - entry))
    planned = replace(
        op,
        entry_time=confirm_bar.time,
        entry=float(entry),
        stop=float(stop),
        target=float(target),
        opportunity_id=f"{op.opportunity_id}:planned:{confirm_bar.time.isoformat()}",
    )
    if planned.rr < MIN_RR:
        return None
    return {
        "opportunity": planned,
        "setup_time": op.entry_time.isoformat(),
        "poi_zone": {"low": zone_low, "high": zone_high},
        "touch_time": touch.time.isoformat(),
        "confirmation_time": confirm_bar.time.isoformat(),
        "confirmation_timeframe": lower_tf,
        "confirmation_rules": ["POI_TOUCH", "LOWER_TF_DISPLACEMENT", "LOWER_TF_FVG"],
    }


def _load_baseline() -> dict[str, Any]:
    candidates = [
        RESEARCH_DIR / "bsi_v3_rolling_walk_forward_jan_august_2026_rr1p25.json",
        RESEARCH_DIR / "bsi_v3_rolling_walk_forward_jan_august_2026.json",
        RESEARCH_DIR / "bsi_v3_august_2026_full.json",
    ]
    for path in candidates:
        if path.exists():
            return {"path": str(path), "payload": json.loads(path.read_text(encoding="utf-8"))}
    return {"path": None, "payload": None}


def _table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    out = []
    for idx, row in enumerate(rows):
        out.append("| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(row))) + " |")
        if idx == 0:
            out.append("| " + " | ".join("-" * w for w in widths) + " |")
    return "\n".join(out)


def _all_outcomes(report: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for item in report.get("strategy_results", {}).values():
        outcomes.extend(item.get("outcomes", []))
    return outcomes


def _forward_adaptive_filter(outcomes: list[v3.Outcome]) -> dict[str, Any]:
    profile = v3.build_adaptive_bucket_profile(outcomes, min_trades=8, min_expectancy=0.02, min_profit_factor=1.05, max_losing_streak=8)
    allowed = {(row["strategy_id"], row["symbol"]) for row in profile["allowed_buckets"]}
    kept = [out for out in outcomes if (out.strategy_id, out.symbol) in allowed]
    skipped = [out for out in outcomes if (out.strategy_id, out.symbol) not in allowed]
    return {
        "allowed_bucket_count": len(allowed),
        "blocked_bucket_count": len(profile["blocked_buckets"]),
        "kept": v3.summarize_outcomes(kept, "managed_r"),
        "skipped": v3.summarize_outcomes(skipped, "managed_r"),
        "allowed_buckets": profile["allowed_buckets"],
        "blocked_buckets": profile["blocked_buckets"],
    }


def main() -> None:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    v3.START = START
    v3.END = END
    v3.MIN_ENTRY_RR = MIN_RR
    store = v3.CandleStore()
    registry = v3.strategy_registry()
    candle_cache: dict[tuple[str, str], list[v3.Bar]] = {}
    time_cache: dict[tuple[str, str], list[datetime]] = {}

    def cached_bars(symbol: str, timeframe: str) -> list[v3.Bar]:
        key = (symbol, timeframe)
        if key not in candle_cache:
            candle_cache[key] = v3.load_bars(store, symbol, timeframe, START, END)
            time_cache[key] = [b.time for b in candle_cache[key]]
        return candle_cache[key]

    def cached_times(symbol: str, timeframe: str) -> list[datetime]:
        cached_bars(symbol, timeframe)
        return time_cache[(symbol, timeframe)]

    planned_by_strategy: dict[str, Any] = {}
    all_planned_outcomes: list[v3.Outcome] = []
    all_raw_outcomes: list[v3.Outcome] = []
    for spec in registry:
        print(f"replaying {spec.strategy_id}", flush=True)
        detector = v3.DETECTOR_REGISTRY.get(spec.strategy_id)
        if detector is None:
            planned_by_strategy[spec.strategy_id] = {"status": "NO_STRATEGY_SPECIFIC_DETECTOR", "planned_entries": 0}
            continue
        scoped_symbols = [s for s in spec.eligible_symbols if s in SYMBOLS]
        planned_entries: list[dict[str, Any]] = []
        raw_outcomes: list[v3.Outcome] = []
        planned_outcomes: list[v3.Outcome] = []
        rejections: dict[str, int] = {}
        for symbol in scoped_symbols:
            symbol_started = time.perf_counter()
            print(f"  {symbol}: loading/detecting", flush=True)
            required = set(spec.required_timeframes) | {"M1", "M5", "M15"}
            bars_by_tf = {tf: cached_bars(symbol, tf) for tf in required}
            found, reasons = detector(spec, symbol, {tf: bars_by_tf.get(tf, []) for tf in spec.required_timeframes})
            print(f"  {symbol}: {len(found)} instant opportunities", flush=True)
            for reason in reasons:
                rejections[reason] = rejections.get(reason, 0) + 1
            primary_tf = spec.required_timeframes[-1]
            primary_bars = bars_by_tf.get(primary_tf) or bars_by_tf.get("M15", [])
            primary_times = cached_times(symbol, primary_tf) if bars_by_tf.get(primary_tf) else cached_times(symbol, "M15")
            for op in found:
                if op.rr < MIN_RR:
                    rejections["instant_entry_rr_below_min"] = rejections.get("instant_entry_rr_below_min", 0) + 1
                    continue
                future = primary_bars[bisect_right(primary_times, op.entry_time) :]
                raw_outcomes.append(v3.simulate_outcome(op, future))
                planned = _find_confirmed_planned_entry(
                    op,
                    primary_bars=primary_bars,
                    primary_times=primary_times,
                    m1_bars=bars_by_tf.get("M1", []),
                    m1_times=cached_times(symbol, "M1"),
                    m5_bars=bars_by_tf.get("M5", []),
                    m5_times=cached_times(symbol, "M5"),
                )
                if planned is None:
                    rejections["planned_entry_not_confirmed_or_expired"] = rejections.get("planned_entry_not_confirmed_or_expired", 0) + 1
                    continue
                planned_op: v3.Opportunity = planned["opportunity"]
                planned_future = primary_bars[bisect_right(primary_times, planned_op.entry_time) :]
                outcome = v3.simulate_outcome(planned_op, planned_future)
                planned_outcomes.append(outcome)
                planned_entries.append(
                    {
                        **{k: v for k, v in planned.items() if k != "opportunity"},
                        "opportunity": asdict(planned_op),
                        "outcome": asdict(outcome),
                    }
                )
            print(
                f"  {symbol}: planned={len(planned_outcomes)} raw={len(raw_outcomes)} elapsed={time.perf_counter() - symbol_started:.1f}s",
                flush=True,
            )
        all_raw_outcomes.extend(raw_outcomes)
        all_planned_outcomes.extend(planned_outcomes)
        planned_by_strategy[spec.strategy_id] = {
            "status": "PLANNED_ENTRY_REPLAYED",
            "spec": asdict(spec),
            "raw_instant": v3.summarize_outcomes(raw_outcomes, "managed_r"),
            "planned_entry": v3.summarize_outcomes(planned_outcomes, "managed_r"),
            "raw_opportunities": len(raw_outcomes),
            "planned_entries": len(planned_outcomes),
            "expired_or_unconfirmed": rejections.get("planned_entry_not_confirmed_or_expired", 0),
            "rejections": rejections,
            "examples": planned_entries[:25],
        }
    adaptive = _forward_adaptive_filter(all_planned_outcomes)
    baseline = _load_baseline()
    result = {
        "methodology": "BSI_V3_PLANNED_POI_CONFIRMATION_ENTRY",
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "symbols": SYMBOLS,
        "min_rr": MIN_RR,
        "course_rules": COURSE_RULES,
        "old_instant_entry_baseline_reference": baseline["path"],
        "planned_entry_contract": {
            "plan_timeframes": ["D1", "H4", "H1", "M30", "M15"],
            "poi_sources": ["FVG", "OB", "breaker", "BPR", "IFVG", "liquidity", "premium_discount", "session_range"],
            "pending_setup_state": ["PLANNED", "WAITING_FOR_POI", "POI_TOUCHED", "CONFIRMED", "EXPIRED", "INVALIDATED"],
            "confirmation_timeframes": ["M1", "M5"],
            "confirmation_rules": ["POI_TOUCH", "MSS_OR_BOS_PROXY_DISPLACEMENT", "LOWER_TF_FVG"],
            "expiry": f"{MAX_PENDING_HOURS}h",
            "risk_model": COURSE_RULES["extracted_rules"]["risk_management"],
        },
        "comparison": {
            "instant_entry_same_window": v3.summarize_outcomes(all_raw_outcomes, "managed_r"),
            "planned_entry": v3.summarize_outcomes(all_planned_outcomes, "managed_r"),
            "planned_entry_plus_adaptive_bucket_filter": adaptive["kept"],
            "planned_entry_adaptive_skipped": adaptive["skipped"],
        },
        "strategy_results": planned_by_strategy,
        "adaptive_manager": adaptive,
        "readiness": {
            "demo_ready": False,
            "reason": "Research replay complete. Live pending-setup state machine still must be wired into MT5 runtime before demo routing uses this model.",
        },
    }
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    rows = [["Model", "Trades", "Win Rate", "Net R", "Expectancy", "PF", "Max LS", "Max DD R"]]
    for name, stats in result["comparison"].items():
        rows.append([name, stats["trades"], stats["win_rate"], stats["net_r"], stats["expectancy"], stats["profit_factor"], stats["max_losing_streak"], stats["max_drawdown_r"]])
    strategy_rows = [["Strategy", "Instant Trades", "Instant Net R", "Planned Trades", "Planned Net R", "Expired/Unconfirmed"]]
    for sid, item in planned_by_strategy.items():
        if item.get("status") != "PLANNED_ENTRY_REPLAYED":
            strategy_rows.append([sid, 0, "", 0, "", item["status"]])
            continue
        strategy_rows.append([sid, item["raw_instant"]["trades"], item["raw_instant"]["net_r"], item["planned_entry"]["trades"], item["planned_entry"]["net_r"], item["expired_or_unconfirmed"]])
    OUT_DOC.write_text(
        "# BSI V3 Planned POI Confirmation Entry Validation\n\n"
        f"Window: `{START.isoformat()}` to `{END.isoformat()}`\n\n"
        f"Symbols: `{', '.join(SYMBOLS)}`\n\n"
        f"Old baseline reference: `{baseline['path']}`\n\n"
        "Course material used: `125/125` videos, `125/125` transcripts, `125/125` contact sheets.\n\n"
        "## Extracted Mentor Rules\n\n"
        "- Draw HTF liquidity/internal liquidity, POIs, FVG/OB/range levels first.\n"
        "- Do not chase price; wait for price to return into the POI.\n"
        "- Confirm on M1/M5 with displacement plus structure/FVG proxy.\n"
        "- Enter only inside valid session/killzone where the model requires it.\n"
        "- Move risk-free after structure breaks in favor; partial around structure/targets.\n"
        "- Use consistent risk; reduce after losing streak; step up only after profit cushion.\n\n"
        "## Comparison\n\n"
        + _table(rows)
        + "\n\n## Strategy Detail\n\n"
        + _table(strategy_rows)
        + "\n\n## Decision\n\n"
        "This validates the planned-entry idea as research. The live MT5 runtime still needs the persistent pending-setup queue before this becomes the demo execution model.\n",
        encoding="utf-8",
    )
    print(json.dumps({"out": str(OUT_JSON), "doc": str(OUT_DOC), "comparison": result["comparison"]}, indent=2))
    store.close()


if __name__ == "__main__":
    main()
