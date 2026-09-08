"""Runtime BSI V3 strategy-specific detectors.

The August validation script has the research detectors, but those are scoped to
offline replay windows. This module keeps the same V3 strategy identities and
rules, while evaluating only the latest live StrategyContext candles.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.models import ACTIVE_MT5, StrategySignal

NY_TZ = ZoneInfo("America/New_York")
METHODOLOGY = "BSI_BASELINE_V3_UPDATED_FAIZ"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeSpec:
    strategy_id: str
    name: str
    source_rule_ids: tuple[str, ...]
    source_videos: tuple[str, ...]
    required_timeframes: tuple[str, ...]
    eligible_symbols: tuple[str, ...]
    management_model: str
    stop_model: str
    target_model: str


@dataclass(frozen=True)
class RuntimeOpportunity:
    spec: RuntimeSpec
    direction: str
    entry_time: datetime
    entry: float
    stop: float
    target: float
    fvg_low: float
    fvg_high: float
    source_timeframe: str
    rejection_reasons: tuple[str, ...] = ()

    @property
    def risk_distance(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_distance(self) -> float:
        return abs(self.target - self.entry)

    @property
    def rr(self) -> float:
        return self.reward_distance / self.risk_distance if self.risk_distance else 0.0


CORE_SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "XAUUSD")
INDEX_SYMBOLS = ("NAS100", "USTEC", "NQ", "SPX500", "US500", "ES", "US30", "DJ30")
CRYPTO_SYMBOLS = ("BTCUSD", "ETHUSD", "BTC", "ETH")
ALL_ASSETS = CORE_SYMBOLS + INDEX_SYMBOLS + CRYPTO_SYMBOLS

RUNTIME_SPECS: tuple[RuntimeSpec, ...] = (
    RuntimeSpec("bsi_v3_order_flow", "Order Flow", ("FAIZ_V3_ORDERFLOW_001",), ("1. Order Flow Trading Strategy.mp4", "2. Order Flow Example For Market Structure Shift (1).mp4", "3. Order Flow Example For Market Structure Shift (2).mp4", "4. Order Flow Example For Market Structure Break (1).mp4", "5. Order Flow Example For Market Structure Break (2).mp4", "20. ORDERFLOW 101.mp4", "21. ORDERFLOW 101 EXAMPLE 1.mp4", "22. ORDERFLOW 101 EXAMPLE 2.mp4"), ("H4", "M15"), CORE_SYMBOLS, "MENTOR_BE_ON_STRUCTURE_BREAK", "STRUCTURE_OB", "LIQUIDITY_OR_FIXED_R"),
    RuntimeSpec("bsi_v3_smt_divergence", "SMT Divergence", ("FAIZ_V3_SMT_001",), ("1. SMT Divergence.mp4",), ("M15", "M1"), ("EURUSD", "GBPUSD", "AUDUSD", "NAS100", "US500", "US30", "BTCUSD", "ETHUSD"), "MENTOR_BE_ON_STRUCTURE_BREAK", "SMT_SWEEP_MSS_STRUCTURE", "OPPOSING_LIQUIDITY_OR_ABC_TARGET"),
    RuntimeSpec("bsi_v3_abc", "ABC", ("FAIZ_V3_ABC_001", "FAIZ_V3_ABC_003", "FAIZ_V3_ABC_004"), ("10. ABC Trading Strategy.mp4", "11. ABC Trading Strategy Example 1.mp4", "12. ABC Trading Strategy Example 2.mp4", "19. ABC 101.mp4", "10. Things To Avoid In ABC Strategy.mp4", "5. ABC POI Combination.mp4", "6. ABC POI Combination Example.mp4"), ("H4", "M15"), CORE_SYMBOLS, "MENTOR_PARTIAL_AT_POI_OR_2R", "ABC_STRUCTURE", "B_LEG_HIGH_LOW"),
    RuntimeSpec("bsi_v3_abcd", "ABCD", ("FAIZ_V3_ABCD_001",), ("16. ABCD 101.mp4", "17. ABCD 101 EXAMPLE 1.mp4", "18. ABCD 101 EXAMPLE 2.mp4", "19. ABCD Trading Strategy.mp4", "20. ABCD Trading Strategy Example.mp4"), ("H4", "M15"), CORE_SYMBOLS, "MENTOR_PARTIAL_AT_POI_OR_2R", "D_LEG_STRUCTURE", "FVG_SUPPLY_DEMAND_OB"),
    RuntimeSpec("bsi_v3_asian_v2", "Asian Session V2", ("FAIZ_V3_ASIAN_V2_001",), ("23. ASIAN SESSION STRATEGY V2.0.mp4", "24. ASIAN SESSION STRATEGY V2.0 EXAMPLE 1.mp4", "25. ASIAN SESSION STRATEGY V2.0 EXAMPLE 2.mp4"), ("M15", "M1"), CORE_SYMBOLS, "MENTOR_FINAL_TARGET_LIQUIDITY", "M1_SWEEP_STRUCTURE", "ASIAN_OPPOSITE_SIDE_OR_POI"),
    RuntimeSpec("bsi_v3_0930", "9:30 Updated", ("FAIZ_V3_0930_001",), ("13. 930AM Trading Strategy (Updated).mp4", "14. 930AM Trading Strategy Example.mp4", "15. 930AM Trading Strategy Example 2.mp4", "16. 930AM Trading Strategy Example 3.mp4"), ("M15", "M1"), INDEX_SYMBOLS, "MENTOR_FIXED_R_3_TO_5", "M1_SWEEP_STRUCTURE", "FVG_OR_3R_5R"),
    RuntimeSpec("bsi_v3_reactionary_block", "Reactionary Block", ("FAIZ_V3_REACTIONARY_001",), ("17. Reactionary Block Trading Strategy.mp4",), ("M15",), CORE_SYMBOLS, "MENTOR_FIXED_2R_5R", "REACTION_OR_ORIGINAL_ZONE", "NEXT_HIGH_LOW_OR_2R_5R"),
    RuntimeSpec("bsi_v3_ict_silver_bullet", "ICT Silver Bullet", ("FAIZ_V3_SB_001",), ("30. ICT Silver Bullet.mp4", "31. ICT Silver Bullet Example 1.mp4", "32. ICT Silver Bullet Example 2.mp4", "33. ICT Silver Bullet Example 3.mp4"), ("M1",), INDEX_SYMBOLS, "MENTOR_BE_AT_1R", "M1_SWEEP_STRUCTURE", "1R_OR_2R"),
    RuntimeSpec("bsi_v3_silver_bullet_with_bias", "Silver Bullet With Bias", ("FAIZ_V3_SB_BIAS_001",), ("24. Silver Bullet With Bias.mp4", "25. Silver Bullet With Bias Example 1.mp4", "26. Silver Bullet With Bias Example 2.mp4"), ("H1", "M15", "M5", "M1"), CORE_SYMBOLS + INDEX_SYMBOLS, "MENTOR_BE_AT_1R", "STRUCTURE", "1_TO_2_OR_INTERNAL_LIQUIDITY"),
    RuntimeSpec("bsi_v3_4h_order_block", "4 Hour OB", ("FAIZ_V3_4H_OB_001",), ("34. 4 Hour OB Trading Strategy.mp4", "35. 4 Hour OB Trading Strategy.mp4", "36. 4 Hour OB Trading Strategy Example 2.mp4", "37. 4 Hour OB Trading Strategy Example 3.mp4"), ("H4", "M15"), CORE_SYMBOLS, "MENTOR_FIXED_1R_BE_2R_TARGET", "H4_OB_M15_STRUCTURE", "2R"),
    RuntimeSpec("bsi_v3_mmxm", "MMXM", ("FAIZ_V3_MMXM_001",), ("8. THE MMXM.mp4", "9. THE MMXM 2.mp4", "10. THE MMXM EXAMPLE 1.mp4", "11. THE MMXM EXAMPLE 2.mp4", "12. THE MMXM EXAMPLE 3.mp4"), ("H4", "H1", "M15", "M5"), CORE_SYMBOLS + INDEX_SYMBOLS, "MENTOR_FINAL_TARGET_LIQUIDITY", "MMXM_STRUCTURE", "ORIGINAL_CONSOLIDATION_LIQUIDITY"),
    RuntimeSpec("bsi_v3_mmxm_second_distribution", "MMXM Second Distribution", ("FAIZ_V3_MMXM2_001",), ("13. MMXM 2ND DISTRIBUTION ENTRY.mp4",), ("H1", "M15"), CORE_SYMBOLS + INDEX_SYMBOLS, "MENTOR_FINAL_TARGET_LIQUIDITY", "FINAL_FRACTAL_BOS", "INTERNAL_LIQUIDITY_FVG"),
    RuntimeSpec("bsi_v3_holy_grail", "Holy Grail", ("FAIZ_V3_HOLY_GRAIL_001",), ("17. The Holy Grail.mp4", "18. The Holy Grail 2.mp4", "19. The Holy Grail Example 1.mp4", "20. The Holy Grail Example 2.mp4", "21. The Holy Grail Example 3.mp4", "22. The Holy Grail Example 4.mp4"), ("H1", "M5"), CORE_SYMBOLS + INDEX_SYMBOLS, "MENTOR_FINAL_TARGET_LIQUIDITY", "M5_MMXM_STRUCTURE", "DAILY_INTERNAL_LIQUIDITY"),
    RuntimeSpec("bsi_v3_juggernaut", "Juggernaut", ("FAIZ_V3_JUGGERNAUT_001",), ("21. The Juggernaut Model.mp4",), ("M1", "M5"), CORE_SYMBOLS + INDEX_SYMBOLS, "MENTOR_IFVG_CLOSEST_LIQUIDITY_BE", "IFVG_STRUCTURE", "CLOSEST_LIQUIDITY"),
    RuntimeSpec("bsi_v3_spectre", "Spectre", ("FAIZ_V3_SPECTRE_001",), ("25. The Spectre Model.mp4",), ("M15",), CORE_SYMBOLS, "MENTOR_FINAL_TARGET_LIQUIDITY", "INVERSE_OB", "NEARBY_LIQUIDITY_OR_FVG"),
    RuntimeSpec("bsi_v3_monday_range", "Monday Range", ("FAIZ_V3_MONDAY_RANGE_001",), ("37. Utilizing Monday Range.mp4",), ("M15",), ("EURUSD", "XAUUSD") + INDEX_SYMBOLS, "MENTOR_RANGE_TARGET", "M15_REVERSAL_STRUCTURE", "OPPOSITE_MONDAY_SIDE_OR_50_PERCENT"),
    RuntimeSpec("bsi_v3_weaver", "Weaver", ("FAIZ_V3_WEAVER_001",), ("38. The Weaver Model.mp4",), ("H1", "M15"), ALL_ASSETS, "MENTOR_FINAL_TARGET_LIQUIDITY", "M15_STRUCTURE", "H1_FVG_DRAW"),
    RuntimeSpec("bsi_v3_standard_deviation_po3", "Standard Deviations / PO3", ("FAIZ_V3_STDDEV_001",), ("29. Standard Deviations.mp4", "30. Standard Deviations 2.mp4"), ("M5",), CORE_SYMBOLS, "MENTOR_PARTIAL_AT_POI_OR_2R", "PO3_STRUCTURE", "STDDEV_NEGATIVE_2_TO_4"),
    RuntimeSpec("bsi_v3_ar50", "AR50", ("FAIZ_V3_AR50_001",), ("34. AR50 Trading Model.mp4",), ("M15",), CORE_SYMBOLS + ("NAS100", "USTEC", "NQ"), "MENTOR_FINAL_TARGET_LIQUIDITY", "ASIAN_RANGE_50", "DAILY_DRAW_LIQUIDITY"),
    RuntimeSpec("bsi_v3_ifvg_po3", "IFVG / PO3", ("FAIZ_V3_IFVG_PO3_001",), ("39. The IFVG Model.mp4",), ("M1",), ("EURUSD", "XAUUSD", "NAS100", "USTEC", "NQ"), "MENTOR_IFVG_50_PERCENT_AT_1R", "MANIPULATION_EXTREME", "CLOSEST_LIQUIDITY_THEN_HTF_DRAW"),
    RuntimeSpec("bsi_v3_turtle_soups_ranges", "Turtle Soups & Ranges", ("FAIZ_V3_TURTLE_RANGES_001",), ("40. Turtle Soups & Ranges Mastery.mp4",), ("M1",), INDEX_SYMBOLS + CRYPTO_SYMBOLS, "MENTOR_TURTLE_RANGE_PARTIAL_BE_AT_0_5", "RANGE_SWEEP_EXTREME", "OPPOSITE_RANGE_SIDE"),
    RuntimeSpec("bsi_v3_yin_yang", "Yin Yang", ("FAIZ_V3_YIN_YANG_001",), ("41. The Yin Yang Model.mp4",), ("M15",), ("XAUUSD",), "MENTOR_FIXED_1R_BE_2R_TARGET", "LONDON_FVG_BODY", "2R"),
    RuntimeSpec("bsi_v3_4h_candle_ranges", "4H Candle Ranges", ("FAIZ_V3_4H_CRD_001",), ("42. 4 Hour Candle Ranges.mp4",), ("H4", "M15"), ("EURUSD", "XAUUSD") + INDEX_SYMBOLS, "MENTOR_CANDLE_RANGE_PARTIAL_BE_TARGET_OPPOSITE_SIDE", "RAID_EXTREME", "OPPOSITE_CANDLE_SIDE"),
    RuntimeSpec("bsi_v3_smt_session_hl", "SMT Session Highs/Lows", ("FAIZ_V3_SMT_SESSION_001",), ("43. Utilizing SMT With Session Highs & Lows.mp4",), ("M15", "M1"), ("EURUSD", "GBPUSD", "AUDUSD", "NZDUSD") + INDEX_SYMBOLS, "MENTOR_SESSION_SMT_BE_1R_OR_STRONG_BIAS_1_5R", "SESSION_SMT_STRUCTURE", "OPPOSITE_SESSION_SIDE"),
    RuntimeSpec("bsi_v3_1h_candle_ranges", "1H Candle Ranges", ("FAIZ_V3_1H_CRD_001",), ("44. 1 Hour Candle Ranges.mp4",), ("H1", "M1"), CORE_SYMBOLS + INDEX_SYMBOLS, "MENTOR_1H_CRD_PARTIAL_AT_50_PERCENT_RANGE", "RAID_EXTREME", "50_PERCENT_THEN_OPPOSITE_SIDE"),
    RuntimeSpec("bsi_v3_enigma_range", "Enigma Range", ("FAIZ_V3_ENIGMA_001",), ("45. The Enigma.mp4",), ("H4", "H1", "M15"), ALL_ASSETS, "MENTOR_ENIGMA_PARTIAL_BE_0_5_TARGET_0_79_OR_1_0", "ENGINEERED_RANGE_EXTREME", "0_79_OR_OPPOSITE_RANGE"),
)

SPEC_BY_ID = {spec.strategy_id: spec for spec in RUNTIME_SPECS}

CONFIRMATION_TIMEFRAME_POLICY: dict[str, str] = {
    "bsi_v3_smt_divergence": "M1_REQUIRED",
    "bsi_v3_asian_v2": "M1_REQUIRED",
    "bsi_v3_0930": "M1_REQUIRED",
    "bsi_v3_ict_silver_bullet": "M1_REQUIRED",
    "bsi_v3_ifvg_po3": "M1_REQUIRED",
    "bsi_v3_turtle_soups_ranges": "M1_REQUIRED",
    "bsi_v3_smt_session_hl": "M1_REQUIRED",
    "bsi_v3_1h_candle_ranges": "M1_REQUIRED",
    "bsi_v3_silver_bullet_with_bias": "M1_OR_M5_ALLOWED",
    "bsi_v3_mmxm": "M5_REQUIRED",
    "bsi_v3_holy_grail": "M5_REQUIRED",
    "bsi_v3_juggernaut": "M1_OR_M5_ALLOWED",
}

TERMINAL_PLAN_STATUSES = {
    "EXPIRED",
    "INVALIDATED_BEFORE_CONFIRMATION",
    "CONFIRMED_RR_BELOW_MINIMUM",
    "MISSED_ENTRY",
    "RESERVED",
    "SUBMITTING",
    "BROKER_ACCEPTED",
    "CONSUMED",
    "BROKER_SUBMISSION_REJECTED",
    "IGNORED_BELOW_PLAN_CONFIDENCE_FLOOR",
}

ACTIVE_PLAN_STATUSES = {"PENDING_POI_TOUCH", "TOUCHED_WAITING_CONFIRMATION", "CONFIRMED_FOR_ENTRY"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _plan_confidence_band(value: float) -> str:
    if value >= 80:
        return "WATCH_PREMIUM"
    if value >= 75:
        return "WATCH_HIGH"
    if value >= 70:
        return "WATCH_MEDIUM"
    if value >= 65:
        return "WATCH_LOW"
    return "IGNORE_NOT_ACTIVE_PLAN"


def _plan_confidence(*, ctx: StrategyContext, spec: RuntimeSpec, direction: str, rr: float, timeframe: str, entry_time: datetime) -> float:
    score = 58.0
    score += min(14.0, max(0.0, rr - _env_float("MT5_BSI_V3_MIN_RISK_REWARD", 1.25)) * 5.0)
    if timeframe in {"H4", "H1"}:
        score += 5.0
    elif timeframe in {"M15", "M5"}:
        score += 3.0
    bias = _simple_bias(ctx.h4_rows) or _simple_bias(ctx.h1_rows) or ctx.htf_trend_h4
    if bias == direction:
        score += 7.0
    if _in_any_ny_window(entry_time, ((2.0, 6.0), (8.5, 12.0), (14.0, 15.0))):
        score += 4.0
    if spec.strategy_id in CONFIRMATION_TIMEFRAME_POLICY:
        score += 2.0
    return round(max(0.0, min(100.0, score)), 2)


def _row_time(row: dict[str, Any]) -> datetime:
    value = row.get("time") or row.get("timestamp")
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ny_hour(dt: datetime) -> float:
    local = dt.astimezone(NY_TZ)
    return local.hour + local.minute / 60.0


def _in_any_ny_window(dt: datetime, windows: tuple[tuple[float, float], ...]) -> bool:
    hour = _ny_hour(dt)
    return any(start <= hour <= end for start, end in windows)


def _simple_bias(rows: list[dict[str, Any]]) -> str | None:
    if len(rows) < 6:
        return None
    recent = rows[-6:]
    highs = [float(r["high"]) for r in recent]
    lows = [float(r["low"]) for r in recent]
    highs_up = highs[-1] > highs[-3] > highs[-5]
    lows_up = lows[-1] > lows[-3] > lows[-5]
    highs_down = highs[-1] < highs[-3] < highs[-5]
    lows_down = lows[-1] < lows[-3] < lows[-5]
    if highs_up or lows_up:
        return "LONG"
    if highs_down or lows_down:
        return "SHORT"
    return "LONG" if float(recent[-1]["close"]) >= float(recent[-1]["open"]) else "SHORT"


def _recent_fvgs(rows: list[dict[str, Any]], *, lookback_bars: int) -> list[dict[str, Any]]:
    if len(rows) < 10:
        return []
    start = max(2, len(rows) - lookback_bars - 2)
    fvgs: list[dict[str, Any]] = []
    for i in range(start, len(rows)):
        a, c = rows[i - 2], rows[i]
        confirmed_at = _row_time(c)
        if float(a["high"]) < float(c["low"]):
            fvgs.append({"direction": "LONG", "index": i, "low": float(a["high"]), "high": float(c["low"]), "confirmed_at": confirmed_at})
        if float(a["low"]) > float(c["high"]):
            fvgs.append({"direction": "SHORT", "index": i, "low": float(c["high"]), "high": float(a["low"]), "confirmed_at": confirmed_at})
    return fvgs


def _rows_for_timeframe(ctx: StrategyContext, timeframe: str) -> list[dict[str, Any]]:
    if timeframe == "M1":
        return ctx.m1_rows
    if timeframe == "M5":
        return ctx.m5_rows
    if timeframe == "M15":
        return ctx.m15_rows
    if timeframe == "H1":
        return ctx.h1_rows
    if timeframe == "H4":
        return ctx.h4_rows
    return []


def _current_entry(ctx: StrategyContext, direction: str) -> float:
    return float(ctx.ask if direction == "LONG" else ctx.bid)


def _zone_allows_entry(ctx: StrategyContext, fvg: dict[str, Any], direction: str) -> bool:
    entry = _current_entry(ctx, direction)
    zone_low = float(fvg["low"])
    zone_high = float(fvg["high"])
    atr = float(ctx.atr_m15 or 0)
    tolerance = max(atr * _env_float("BSI_V3_RUNTIME_ENTRY_ZONE_ATR_TOLERANCE", 0.35), float(ctx.spread or 0) * 2.0)
    return (zone_low - tolerance) <= entry <= (zone_high + tolerance)


def _make_opportunity(ctx: StrategyContext, spec: RuntimeSpec, fvg: dict[str, Any], rows: list[dict[str, Any]], *, fixed_r: float = 2.0) -> RuntimeOpportunity | None:
    direction = str(fvg["direction"])
    entry = _current_entry(ctx, direction)
    idx = int(fvg["index"])
    lookback = rows[max(0, idx - 10) : idx + 1]
    if not lookback:
        return None
    if direction == "LONG":
        stop = min(float(r["low"]) for r in lookback)
        min_stop = float(ctx.broker_min_stop_distance or 0)
        if stop >= entry:
            stop = entry - max(float(ctx.atr_m15 or 0), min_stop, float(ctx.spread or 0) * 3.0)
        target = entry + (entry - stop) * fixed_r
    else:
        stop = max(float(r["high"]) for r in lookback)
        min_stop = float(ctx.broker_min_stop_distance or 0)
        if stop <= entry:
            stop = entry + max(float(ctx.atr_m15 or 0), min_stop, float(ctx.spread or 0) * 3.0)
        target = entry - (stop - entry) * fixed_r
    if abs(entry - stop) <= 0:
        return None
    return RuntimeOpportunity(spec, direction, fvg["confirmed_at"], entry, stop, target, float(fvg["low"]), float(fvg["high"]), str(fvg["timeframe"]))


def _base_fvg_opportunities(ctx: StrategyContext, spec: RuntimeSpec, rejection_code: str) -> tuple[list[RuntimeOpportunity], list[str]]:
    if ctx.symbol not in spec.eligible_symbols:
        return [], [f"{spec.strategy_id}_symbol_not_eligible"]
    missing = []
    for tf in spec.required_timeframes:
        rows_for_tf = _rows_for_timeframe(ctx, tf)
        min_rows = 50 if tf in {"M1", "M5", "M15"} else 10
        if len(rows_for_tf) < min_rows:
            missing.append(tf)
    if missing:
        return [], [f"DATA_GAP_{'_'.join(sorted(set(missing)))}"]
    primary_tf = next((tf for tf in reversed(spec.required_timeframes) if tf in {"M1", "M5", "M15"}), "M15")
    primary_rows = _rows_for_timeframe(ctx, primary_tf)
    fvgs = _recent_fvgs(primary_rows, lookback_bars=_env_int("BSI_V3_RUNTIME_RECENT_LOOKBACK_BARS", 6))
    opportunities = []
    for fvg in fvgs:
        fvg["timeframe"] = primary_tf
        if not _zone_allows_entry(ctx, fvg, str(fvg["direction"])):
            continue
        op = _make_opportunity(ctx, spec, fvg, primary_rows)
        if op is not None:
            opportunities.append(op)
    return opportunities, [] if opportunities else [rejection_code]


def _filter_or_reject(opps: list[RuntimeOpportunity], predicate: Callable[[RuntimeOpportunity], bool], reason: str) -> tuple[list[RuntimeOpportunity], list[str]]:
    kept = [op for op in opps if predicate(op)]
    return kept, [] if kept else [reason]


def _with_fixed_r(op: RuntimeOpportunity, multiple: float) -> RuntimeOpportunity:
    sign = 1 if op.direction == "LONG" else -1
    return RuntimeOpportunity(op.spec, op.direction, op.entry_time, op.entry, op.stop, op.entry + sign * op.risk_distance * multiple, op.fvg_low, op.fvg_high, op.source_timeframe, op.rejection_reasons)


def _queue_path(ctx: StrategyContext) -> Path:
    return planned_entry_queue_path(ctx.account_id)


def planned_entry_queue_path(account_id: str | None) -> Path:
    raw = os.getenv("BSI_V3_PLANNED_ENTRY_QUEUE_PATH", "/data/research/bsi_v3_live_pending_entry_queue.json")
    path = Path(raw)
    account = str(account_id or "default").replace("/", "_").replace("\\", "_").replace(":", "_")
    return path.with_name(f"{path.stem}_{account}{path.suffix}")


def _load_queue(ctx: StrategyContext) -> list[dict[str, Any]]:
    path = _queue_path(ctx)
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return payload
    except Exception:
        return []
    return []


def _save_queue(ctx: StrategyContext, rows: list[dict[str, Any]]) -> None:
    path = _queue_path(ctx)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.tmp")
        tmp.write_text(json.dumps(rows[-_env_int("BSI_V3_PLANNED_ENTRY_MAX_QUEUE", 500):], indent=2, default=str), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        return


def _primary_live_timeframe(spec: RuntimeSpec) -> str:
    for tf in reversed(spec.required_timeframes):
        if tf in {"M1", "M5", "M15"}:
            return tf
    return "M15"


def _planned_target_multiple(spec: RuntimeSpec) -> float:
    if spec.strategy_id == "bsi_v3_0930":
        return 3.0
    if spec.management_model in {"MENTOR_FIXED_1R_BE_2R_TARGET", "MENTOR_PARTIAL_AT_POI_OR_2R"}:
        return 2.0
    return 2.0


def _make_plan_from_fvg(ctx: StrategyContext, spec: RuntimeSpec, fvg: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    idx = int(fvg["index"])
    lookback = rows[max(0, idx - 10) : idx + 1]
    if not lookback:
        return None
    direction = str(fvg["direction"])
    entry = (float(fvg["low"]) + float(fvg["high"])) / 2.0
    if direction == "LONG":
        stop = min(float(r["low"]) for r in lookback)
        if stop >= entry:
            return None
        target = entry + (entry - stop) * _planned_target_multiple(spec)
    else:
        stop = max(float(r["high"]) for r in lookback)
        if stop <= entry:
            return None
        target = entry - (stop - entry) * _planned_target_multiple(spec)
    created_at = fvg["confirmed_at"]
    risk = abs(entry - stop)
    rr = abs(target - entry) / risk if risk else 0.0
    plan_confidence = _plan_confidence(ctx=ctx, spec=spec, direction=direction, rr=rr, timeframe=str(fvg["timeframe"]), entry_time=created_at)
    structural_key = f"{ctx.symbol.upper()}:{fvg['timeframe']}:{direction}:{created_at.isoformat()}:{float(fvg['low']):.8f}:{float(fvg['high']):.8f}"
    context_id = f"context:{ctx.symbol.upper()}:{direction}:{fvg['timeframe']}:{created_at.date().isoformat()}"
    thesis_id = f"thesis:{ctx.symbol.upper()}:{direction}:{fvg['timeframe']}:{created_at.date().isoformat()}:{created_at.hour:02d}"
    poi_id = f"poi:{structural_key}"
    opportunity_id = f"entry:{structural_key}:{entry:.8f}:{stop:.8f}:{target:.8f}"
    plan_id = f"{spec.strategy_id}:{opportunity_id}"
    return {
        "plan_id": plan_id,
        "bsi_v3_market_context_id": context_id,
        "bsi_v3_market_thesis_id": thesis_id,
        "bsi_v3_poi_id": poi_id,
        "bsi_v3_entry_opportunity_id": opportunity_id,
        "canonical_plan_id": f"canonical:{opportunity_id}",
        "status": "PENDING_POI_TOUCH",
        "strategy_id": spec.strategy_id,
        "primary_strategy_id": spec.strategy_id,
        "confluence_strategy_ids": [spec.strategy_id],
        "confluence_source_videos": list(spec.source_videos),
        "symbol": ctx.symbol.upper(),
        "broker_symbol": ctx.broker_symbol,
        "direction": direction,
        "created_at": created_at.isoformat(),
        "last_updated_at": ctx.generated_at.astimezone(timezone.utc).isoformat(),
        "expires_at": (created_at + timedelta(hours=_env_float("BSI_V3_PLANNED_ENTRY_MAX_PENDING_HOURS", 24.0))).isoformat(),
        "initial_plan_confidence": plan_confidence,
        "current_plan_confidence": plan_confidence,
        "max_plan_confidence": plan_confidence,
        "plan_confidence_band": _plan_confidence_band(plan_confidence),
        "entry": entry,
        "stop": stop,
        "target": target,
        "fvg_low": float(fvg["low"]),
        "fvg_high": float(fvg["high"]),
        "source_timeframe": str(fvg["timeframe"]),
        "source_rule_ids": list(spec.source_rule_ids),
        "source_videos": list(spec.source_videos),
        "management_model": spec.management_model,
        "stop_model": spec.stop_model,
        "target_model": spec.target_model,
        "confirmation_policy": CONFIRMATION_TIMEFRAME_POLICY.get(spec.strategy_id, "M1_OR_M5_ALLOWED"),
    }


def _upsert_new_plans(ctx: StrategyContext, allowed_specs: list[RuntimeSpec], queue: list[dict[str, Any]]) -> None:
    min_plan_confidence = _env_float("BSI_V3_MIN_PLAN_CONFIDENCE", 65.0)
    active_rows = [row for row in queue if row.get("status") in ACTIVE_PLAN_STATUSES]
    existing = {str(row.get("plan_id")) for row in active_rows}
    lookback = _env_int("BSI_V3_PLANNED_SETUP_LOOKBACK_BARS", 96)
    for spec in allowed_specs:
        if ctx.symbol.upper() not in spec.eligible_symbols:
            continue
        tf = _primary_live_timeframe(spec)
        rows = _rows_for_timeframe(ctx, tf)
        if len(rows) < 50:
            continue
        for fvg in _recent_fvgs(rows, lookback_bars=lookback):
            fvg["timeframe"] = tf
            plan = _make_plan_from_fvg(ctx, spec, fvg, rows)
            if not plan:
                continue
            if float(plan.get("current_plan_confidence") or 0.0) < min_plan_confidence:
                logger.info(
                    "BSI V3 planned-entry plan ignored below floor: symbol=%s strategy=%s direction=%s confidence=%s floor=%s",
                    plan["symbol"],
                    plan["strategy_id"],
                    plan["direction"],
                    plan.get("current_plan_confidence"),
                    min_plan_confidence,
                )
                continue
            same = next((row for row in active_rows if _same_poi_group(row, plan)), None)
            if same is not None:
                current = float(plan.get("current_plan_confidence") or 0.0)
                previous_current = float(same.get("current_plan_confidence") or same.get("initial_plan_confidence") or 0.0)
                previous_max = float(same.get("max_plan_confidence") or previous_current)
                same["last_updated_at"] = ctx.generated_at.astimezone(timezone.utc).isoformat()
                same["current_plan_confidence"] = max(previous_current, current)
                same["max_plan_confidence"] = max(previous_max, current)
                same["plan_confidence_band"] = _plan_confidence_band(float(same["current_plan_confidence"]))
                strategies = sorted(set((same.get("confluence_strategy_ids") or [same.get("strategy_id")]) + [spec.strategy_id]))
                same["confluence_strategy_ids"] = strategies
                videos = sorted(set((same.get("confluence_source_videos") or same.get("source_videos") or []) + list(spec.source_videos)))
                same["confluence_source_videos"] = videos
                if current > previous_current:
                    same["primary_strategy_id"] = spec.strategy_id
                    same["strategy_id"] = spec.strategy_id
                    same["source_rule_ids"] = list(spec.source_rule_ids)
                    same["source_videos"] = list(spec.source_videos)
                    same["management_model"] = spec.management_model
                    same["stop_model"] = spec.stop_model
                    same["target_model"] = spec.target_model
                continue
            if plan["plan_id"] not in existing:
                queue.append(plan)
                active_rows.append(plan)
                existing.add(plan["plan_id"])
                logger.info(
                    "BSI V3 planned-entry plan created: plan_id=%s symbol=%s strategy=%s direction=%s timeframe=%s poi=[%s,%s] expires_at=%s",
                    plan["plan_id"],
                    plan["symbol"],
                    plan["strategy_id"],
                    plan["direction"],
                    plan["source_timeframe"],
                    plan["fvg_low"],
                    plan["fvg_high"],
                    plan["expires_at"],
                )


def _current_price_touches_plan(ctx: StrategyContext, plan: dict[str, Any]) -> bool:
    price = float(ctx.ask if plan.get("direction") == "LONG" else ctx.bid)
    atr = float(ctx.atr_m15 or 0.0)
    tolerance = max(atr * _env_float("BSI_V3_PLANNED_TOUCH_ATR_TOLERANCE", 0.2), float(ctx.spread or 0.0) * 2.0)
    return float(plan["fvg_low"]) - tolerance <= price <= float(plan["fvg_high"]) + tolerance


def _plan_invalidated(ctx: StrategyContext, plan: dict[str, Any]) -> bool:
    price = float(ctx.bid if plan.get("direction") == "LONG" else ctx.ask)
    stop = float(plan["stop"])
    return price <= stop if plan.get("direction") == "LONG" else price >= stop


def _lower_tf_confirms(ctx: StrategyContext, spec: RuntimeSpec, direction: str) -> tuple[bool, str]:
    policy = CONFIRMATION_TIMEFRAME_POLICY.get(spec.strategy_id, "M1_OR_M5_ALLOWED")
    if policy == "M1_REQUIRED":
        candidates = (("M1", ctx.m1_rows),)
    elif policy == "M5_REQUIRED":
        candidates = (("M5", ctx.m5_rows),)
    else:
        candidates = (("M1", ctx.m1_rows), ("M5", ctx.m5_rows))
    for tf, rows in candidates:
        if len(rows) < 10:
            continue
        fvgs = _recent_fvgs(rows, lookback_bars=_env_int("BSI_V3_PLANNED_CONFIRM_LOOKBACK_BARS", 8))
        wanted = "LONG" if direction == "LONG" else "SHORT"
        directional = [row for row in fvgs if row["direction"] == wanted]
        if directional:
            return True, tf
    return False, "NONE"


def _opportunity_from_plan(ctx: StrategyContext, spec: RuntimeSpec, plan: dict[str, Any], confirmation_tf: str) -> RuntimeOpportunity | None:
    direction = str(plan["direction"])
    entry = _current_entry(ctx, direction)
    stop = float(plan["stop"])
    if direction == "LONG" and stop >= entry:
        return None
    if direction == "SHORT" and stop <= entry:
        return None
    risk = abs(entry - stop)
    target = float(plan["target"])
    min_rr = _env_float("MT5_BSI_V3_MIN_RISK_REWARD", 1.25)
    if abs(target - entry) / risk < min_rr:
        sign = 1 if direction == "LONG" else -1
        target = entry + sign * risk * max(min_rr, _planned_target_multiple(spec))
    return RuntimeOpportunity(
        spec,
        direction,
        ctx.generated_at,
        entry,
        stop,
        target,
        float(plan["fvg_low"]),
        float(plan["fvg_high"]),
        confirmation_tf,
    )


def _same_poi_group(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("bsi_v3_entry_opportunity_id") and right.get("bsi_v3_entry_opportunity_id"):
        return left.get("bsi_v3_entry_opportunity_id") == right.get("bsi_v3_entry_opportunity_id")
    if str(left.get("symbol") or "").upper() != str(right.get("symbol") or "").upper():
        return False
    if str(left.get("direction") or "") != str(right.get("direction") or ""):
        return False
    if str(left.get("source_timeframe") or "") != str(right.get("source_timeframe") or ""):
        return False
    keys = ("entry", "stop", "target", "fvg_low", "fvg_high")
    return all(abs(float(left.get(key) or 0.0) - float(right.get(key) or 0.0)) <= 1e-9 for key in keys)


def _confluent_plans(queue: list[dict[str, Any]], anchor: dict[str, Any]) -> list[dict[str, Any]]:
    return [plan for plan in queue if plan is anchor or _same_poi_group(plan, anchor)]


def _live_cycle_key(ctx: StrategyContext) -> str:
    rows = ctx.m5_rows or ctx.m15_rows
    if rows:
        try:
            return _row_time(rows[-1]).isoformat()
        except Exception:
            pass
    return ctx.generated_at.replace(second=0, microsecond=0).astimezone(timezone.utc).isoformat()


def _detect_order_flow(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "order_flow_no_mss_fvg_ob_sequence")
    bias = _simple_bias(ctx.h4_rows) or ctx.htf_trend_daily
    if not opps:
        return [], reasons
    if bias in {"LONG", "SHORT"}:
        return _filter_or_reject(opps, lambda op: op.direction == bias, "order_flow_htf_bias_mismatch")
    return [], ["order_flow_no_h4_daily_bias"]


def _detect_smt_divergence(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "smt_no_sweep_mss_fvg")
    if not opps:
        return [], reasons
    return _filter_or_reject(opps, lambda op: _in_any_ny_window(op.entry_time, ((3.0, 6.0), (8.5, 11.5))) and op.rr >= 1.25, "smt_no_session_or_rr_room")


def _detect_abc(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "abc_no_reclaim_mss_fvg")
    if not opps:
        return [], reasons
    filtered, reject = _filter_or_reject(opps, lambda op: _in_any_ny_window(op.entry_time, ((3.0, 6.0), (8.5, 11.25))), "abc_no_killzone_or_rr_room")
    return [_with_fixed_r(op, 2.0) for op in filtered], reject


def _detect_abcd(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "abcd_no_d_leg_m15_mss")
    bias = _simple_bias(ctx.h4_rows)
    if not opps:
        return [], reasons
    return _filter_or_reject(opps, lambda op: (bias is None or op.direction == bias) and _in_any_ny_window(op.entry_time, ((3.0, 6.0), (8.5, 11.25))), "abcd_no_h4_bias_or_killzone")


def _detect_asian_v2(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "asian_v2_no_m1_sweep_mss_fvg")
    if not opps:
        return [], reasons
    return _filter_or_reject(opps, lambda op: _in_any_ny_window(op.entry_time, ((2.0, 6.0), (8.0, 11.5))), "asian_v2_no_asian_sweep_poi_retest")


def _detect_0930(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "0930_no_liquidity_sweep_mss_fvg")
    if not opps:
        return [], reasons
    filtered, reject = _filter_or_reject(opps, lambda op: _in_any_ny_window(op.entry_time, ((9.5, 11.99),)), "0930_not_in_930_1159_ny")
    return [_with_fixed_r(op, 3.0) for op in filtered], reject


def _detect_reactionary_block(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    return _base_fvg_opportunities(ctx, spec, "reactionary_no_original_ob_fvg_reaction")


def _detect_ict_silver_bullet(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "silver_bullet_no_m1_sweep_displacement_fvg")
    if not opps:
        return [], reasons
    filtered, reject = _filter_or_reject(opps, lambda op: _in_any_ny_window(op.entry_time, ((10.0, 11.0), (14.0, 15.0))), "silver_bullet_not_in_killzone")
    return [_with_fixed_r(op, 2.0) for op in filtered], reject


def _detect_silver_bullet_with_bias(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "silver_bullet_bias_no_structure_fvg")
    if not opps:
        return [], reasons
    bias = _simple_bias(ctx.h1_rows) or _simple_bias(ctx.h4_rows)
    filtered, reject = _filter_or_reject(opps, lambda op: (bias is None or op.direction == bias) and _in_any_ny_window(op.entry_time, ((10.0, 11.0), (14.0, 15.0))), "silver_bullet_bias_mismatch_or_outside_killzone")
    return [_with_fixed_r(op, 2.0) for op in filtered], reject


def _detect_4h_order_block(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "4h_ob_no_m15_mss_from_h4_ob")
    bias = _simple_bias(ctx.h4_rows)
    if not opps:
        return [], reasons
    filtered, reject = _filter_or_reject(opps, lambda op: (bias is None or op.direction == bias) and _in_any_ny_window(op.entry_time, ((3.0, 6.0), (8.5, 11.25))), "4h_ob_no_h4_orderflow_or_killzone")
    return [_with_fixed_r(op, 2.0) for op in filtered], reject


def _detect_mmxm(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "mmxm_no_accumulation_poi_mss_fvg")
    if not opps:
        return [], reasons
    bias = _simple_bias(ctx.h4_rows) or _simple_bias(ctx.h1_rows)
    return _filter_or_reject(opps, lambda op: (bias is None or op.direction == bias) and op.rr >= 1.5, "mmxm_no_htf_bias_or_liquidity_room")


def _detect_mmxm_second_distribution(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "mmxm2_no_final_fractal_bos_fvg")
    if not opps:
        return [], reasons
    return _filter_or_reject(opps, lambda op: op.entry_time.weekday() < 5 and op.rr >= 1.2, "mmxm2_no_internal_liquidity_room")


def _detect_holy_grail(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "holy_grail_no_daily_h1_m5_sequence")
    if not opps:
        return [], reasons
    h1_bias = _simple_bias(ctx.h1_rows)
    return _filter_or_reject(opps, lambda op: h1_bias is None or op.direction == h1_bias, "holy_grail_h1_confirmation_mismatch")


def _detect_juggernaut(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "juggernaut_no_ifvg_structure")
    if not opps:
        return [], reasons
    return _filter_or_reject(opps, lambda op: 1.0 <= op.rr <= 3.0 and _in_any_ny_window(op.entry_time, ((3.0, 6.0), (8.5, 12.0))), "juggernaut_no_session_or_close_liquidity")


def _detect_spectre(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "spectre_no_inverse_ob_reclaim")
    if not opps:
        return [], reasons
    return _filter_or_reject(opps, lambda op: 1.0 <= op.rr <= 5.0, "spectre_no_nearby_liquidity_target")


def _detect_monday_range(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "monday_range_no_tuesday_mss")
    if not opps:
        return [], reasons
    return _filter_or_reject(opps, lambda op: op.entry_time.astimezone(NY_TZ).weekday() == 1, "monday_range_no_tuesday_sweep")


def _detect_weaver(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "weaver_no_previous_day_sweep_m15_mss")
    if not opps:
        return [], reasons
    by_day: dict[Any, list[dict[str, Any]]] = {}
    for row in ctx.m15_rows:
        by_day.setdefault(_row_time(row).date(), []).append(row)

    def swept_previous_day(op: RuntimeOpportunity) -> bool:
        all_days = sorted(day for day in by_day if day < op.entry_time.date())
        if not all_days:
            return False
        prior = by_day[all_days[-1]]
        day_rows = [r for r in by_day.get(op.entry_time.date(), []) if _row_time(r) <= op.entry_time]
        if not day_rows:
            return False
        prev_high, prev_low = max(float(r["high"]) for r in prior), min(float(r["low"]) for r in prior)
        return max(float(r["high"]) for r in day_rows) > prev_high or min(float(r["low"]) for r in day_rows) < prev_low

    return _filter_or_reject(opps, swept_previous_day, "weaver_no_previous_day_high_low_sweep")


def _detect_standard_deviation_po3(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "stddev_po3_no_m5_manipulation_mss")
    if not opps:
        return [], reasons
    return _filter_or_reject(opps, lambda op: op.rr >= 2.0, "stddev_po3_no_negative_2_room")


def _detect_ar50(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "ar50_no_asian_50_pullback_mss")
    if not opps:
        return [], reasons

    def near_asian_mid(op: RuntimeOpportunity) -> bool:
        scoped = [r for r in ctx.m15_rows if _row_time(r).date() == op.entry_time.date() and 18.0 <= _ny_hour(_row_time(r)) < 24.0]
        if not scoped:
            return False
        high, low = max(float(r["high"]) for r in scoped), min(float(r["low"]) for r in scoped)
        mid = (high + low) / 2.0
        return abs(op.entry - mid) <= max((high - low) * 0.35, op.risk_distance)

    return _filter_or_reject(opps, near_asian_mid, "ar50_not_near_asian_range_50")


def _detect_ifvg_po3(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "ifvg_po3_no_manipulation_extreme_reclaim")
    if not opps:
        return [], reasons
    return _filter_or_reject(opps, lambda op: op.rr >= 1.0 and _in_any_ny_window(op.entry_time, ((3.0, 6.0), (8.5, 12.0))), "ifvg_po3_no_killzone_or_target_room")


def _detect_turtle_soups_ranges(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "turtle_no_range_sweep_mss")
    if not opps:
        return [], reasons
    return _filter_or_reject(opps, lambda op: _in_any_ny_window(op.entry_time, ((3.0, 6.0), (8.5, 12.0))) and op.rr >= 1.0, "turtle_no_london_ny_range_reentry")


def _detect_yin_yang(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "yin_yang_no_london_ifvg")
    if not opps:
        return [], reasons
    filtered, reject = _filter_or_reject(opps, lambda op: _in_any_ny_window(op.entry_time, ((3.0, 7.0),)), "yin_yang_not_london_session")
    return [_with_fixed_r(op, 2.0) for op in filtered], reject


def _detect_4h_candle_ranges(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "4h_crd_no_range_raid_mss")
    if not opps:
        return [], reasons
    return _filter_or_reject(opps, lambda op: _in_any_ny_window(op.entry_time, ((5.0, 13.0),)), "4h_crd_no_1am_or_5am_range_raid")


def _detect_smt_session_hl(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "smt_session_no_session_high_low_mss")
    if not opps:
        return [], reasons
    return _filter_or_reject(opps, lambda op: op.rr >= 1.25 and _in_any_ny_window(op.entry_time, ((3.0, 6.0), (8.5, 11.5))), "smt_session_no_session_sweep_or_rr_room")


def _detect_1h_candle_ranges(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    opps, reasons = _base_fvg_opportunities(ctx, spec, "1h_crd_no_hourly_raid_mss")
    if not opps:
        return [], reasons
    return _filter_or_reject(opps, lambda op: _in_any_ny_window(op.entry_time, ((3.0, 6.0), (8.5, 12.0))), "1h_crd_no_london_or_ny_raid")


def _detect_enigma_range(ctx: StrategyContext, spec: RuntimeSpec) -> tuple[list[RuntimeOpportunity], list[str]]:
    return _base_fvg_opportunities(ctx, spec, "enigma_no_engineered_range_reentry")


RUNTIME_DETECTOR_REGISTRY: dict[str, Callable[[StrategyContext, RuntimeSpec], tuple[list[RuntimeOpportunity], list[str]]]] = {
    "bsi_v3_order_flow": _detect_order_flow,
    "bsi_v3_smt_divergence": _detect_smt_divergence,
    "bsi_v3_abc": _detect_abc,
    "bsi_v3_abcd": _detect_abcd,
    "bsi_v3_asian_v2": _detect_asian_v2,
    "bsi_v3_0930": _detect_0930,
    "bsi_v3_reactionary_block": _detect_reactionary_block,
    "bsi_v3_ict_silver_bullet": _detect_ict_silver_bullet,
    "bsi_v3_silver_bullet_with_bias": _detect_silver_bullet_with_bias,
    "bsi_v3_4h_order_block": _detect_4h_order_block,
    "bsi_v3_mmxm": _detect_mmxm,
    "bsi_v3_mmxm_second_distribution": _detect_mmxm_second_distribution,
    "bsi_v3_holy_grail": _detect_holy_grail,
    "bsi_v3_juggernaut": _detect_juggernaut,
    "bsi_v3_spectre": _detect_spectre,
    "bsi_v3_monday_range": _detect_monday_range,
    "bsi_v3_weaver": _detect_weaver,
    "bsi_v3_standard_deviation_po3": _detect_standard_deviation_po3,
    "bsi_v3_ar50": _detect_ar50,
    "bsi_v3_ifvg_po3": _detect_ifvg_po3,
    "bsi_v3_turtle_soups_ranges": _detect_turtle_soups_ranges,
    "bsi_v3_yin_yang": _detect_yin_yang,
    "bsi_v3_4h_candle_ranges": _detect_4h_candle_ranges,
    "bsi_v3_smt_session_hl": _detect_smt_session_hl,
    "bsi_v3_1h_candle_ranges": _detect_1h_candle_ranges,
    "bsi_v3_enigma_range": _detect_enigma_range,
}


def evaluate_bsi_v3_planned_runtime_detectors(ctx: StrategyContext, allowed_strategy_ids: set[str]) -> StrategySignal | None:
    return _evaluate_bsi_v3_planned_runtime_detectors(ctx, allowed_strategy_ids, create_new_plans=True)


def evaluate_bsi_v3_existing_planned_queue(ctx: StrategyContext, allowed_strategy_ids: set[str]) -> StrategySignal | None:
    """Fast watcher path: consume/update existing queued plans without creating new HTF plans."""
    return _evaluate_bsi_v3_planned_runtime_detectors(ctx, allowed_strategy_ids, create_new_plans=False)


def _evaluate_bsi_v3_planned_runtime_detectors(ctx: StrategyContext, allowed_strategy_ids: set[str], *, create_new_plans: bool) -> StrategySignal | None:
    min_rr = _env_float("MT5_BSI_V3_MIN_RISK_REWARD", 1.25)
    allowed_specs = [spec for spec in RUNTIME_SPECS if (not allowed_strategy_ids or spec.strategy_id in allowed_strategy_ids)]
    queue = _load_queue(ctx)
    now = ctx.generated_at.astimezone(timezone.utc)
    cycle_key = _live_cycle_key(ctx)
    min_plan_confidence = _env_float("BSI_V3_MIN_PLAN_CONFIDENCE", 65.0)
    active: list[dict[str, Any]] = []
    best: tuple[RuntimeOpportunity, dict[str, Any], str] | None = None
    spec_by_id = {spec.strategy_id: spec for spec in allowed_specs}

    if create_new_plans:
        _upsert_new_plans(ctx, allowed_specs, queue)

    for plan in queue:
        if str(plan.get("symbol") or "").upper() != ctx.symbol.upper():
            active.append(plan)
            continue
        status = str(plan.get("status") or "PENDING_POI_TOUCH")
        if status in TERMINAL_PLAN_STATUSES:
            continue
        spec = spec_by_id.get(str(plan.get("strategy_id") or ""))
        if spec is None:
            active.append(plan)
            continue
        if float(plan.get("current_plan_confidence") or plan.get("initial_plan_confidence") or 100.0) < min_plan_confidence:
            plan["status"] = "IGNORED_BELOW_PLAN_CONFIDENCE_FLOOR"
            plan["last_updated_at"] = now.isoformat()
            continue
        try:
            expires_at = datetime.fromisoformat(str(plan["expires_at"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            continue
        if expires_at < now:
            plan["status"] = "EXPIRED"
            continue
        if _plan_invalidated(ctx, plan):
            plan["status"] = "INVALIDATED_BEFORE_CONFIRMATION"
            continue
        if not _current_price_touches_plan(ctx, plan):
            active.append(plan)
            continue
        if not plan.get("alerted_touch_at"):
            plan["alerted_touch_at"] = now.isoformat()
            logger.warning(
                "BSI V3 planned-entry POI touched: plan_id=%s symbol=%s strategy=%s direction=%s price_bid=%s price_ask=%s poi=[%s,%s]",
                plan.get("plan_id"),
                plan.get("symbol"),
                plan.get("strategy_id"),
                plan.get("direction"),
                ctx.bid,
                ctx.ask,
                plan.get("fvg_low"),
                plan.get("fvg_high"),
            )
        confirmed, confirmation_tf = _lower_tf_confirms(ctx, spec, str(plan["direction"]))
        if not confirmed:
            plan["status"] = "TOUCHED_WAITING_CONFIRMATION"
            plan["last_touch_at"] = now.isoformat()
            active.append(plan)
            continue
        op = _opportunity_from_plan(ctx, spec, plan, confirmation_tf)
        if op is None or op.rr < min_rr:
            plan["status"] = "CONFIRMED_RR_BELOW_MINIMUM"
            continue
        plan["status"] = "CONFIRMED_FOR_ENTRY"
        plan["confirmed_at"] = now.isoformat()
        plan["confirmation_started_at"] = plan.get("last_touch_at") or plan.get("alerted_touch_at") or now.isoformat()
        plan["confirmation_confirmed_at"] = now.isoformat()
        plan["confirmation_bar_close_at"] = now.isoformat()
        plan["bsi_v3_confirmation_id"] = f"confirm:{plan.get('bsi_v3_entry_opportunity_id') or plan.get('plan_id')}:{confirmation_tf}:{cycle_key}"
        plan["confirmed_cycle_key"] = cycle_key
        if not plan.get("alerted_confirmed_at"):
            plan["alerted_confirmed_at"] = now.isoformat()
            logger.warning(
                "BSI V3 planned-entry confirmed for entry: plan_id=%s symbol=%s strategy=%s direction=%s confirmation_tf=%s entry=%s stop=%s target=%s rr=%.2f",
                plan.get("plan_id"),
                plan.get("symbol"),
                plan.get("strategy_id"),
                plan.get("direction"),
                confirmation_tf,
                op.entry,
                op.stop,
                op.target,
                op.rr,
            )
        if best is None or (op.rr, op.entry_time) > (best[0].rr, best[0].entry_time):
            best = (op, plan, confirmation_tf)
        active.append(plan)

    _save_queue(ctx, active)
    if best is None:
        return None

    op, plan, confirmation_tf = best
    spec = op.spec
    confluent = _confluent_plans(active, plan)
    confluent_plan_ids = sorted({str(row.get("plan_id") or "") for row in confluent if row.get("plan_id")})
    confluent_strategy_ids = sorted(
        set(
            str(strategy)
            for row in confluent
            for strategy in (row.get("confluence_strategy_ids") or [row.get("strategy_id")])
            if strategy
        )
    )
    confluent_source_videos = sorted({video for row in confluent for video in (row.get("confluence_source_videos") or row.get("source_videos") or [])})
    strength = min(100.0, 74.0 + min(20.0, max(0.0, op.rr - min_rr) * 6.0))
    if len(confluent_strategy_ids) > 1:
        strength = min(100.0, strength + min(8.0, (len(confluent_strategy_ids) - 1) * 4.0))
    evidence = {
        "bsi_version": METHODOLOGY,
        "active_methodology": METHODOLOGY,
        "setup_subtype": spec.strategy_id,
        "subtype_activation_status": ACTIVE_MT5,
        "v3_strategy_id": spec.strategy_id,
        "v3_detector_validation": "STRATEGY_SPECIFIC_RUNTIME_PLANNED_ENTRY",
        "v3_bridge_source": "v3_planned_entry_runtime_queue",
        "v3_detector_registry_size": len(RUNTIME_DETECTOR_REGISTRY),
        "v3_detector_available_strategy_count": len(RUNTIME_DETECTOR_REGISTRY),
        "v3_plan_id": plan.get("plan_id"),
        "v3_canonical_plan_id": plan.get("canonical_plan_id"),
        "v3_confluence_plan_ids": confluent_plan_ids,
        "bsi_v3_market_context_id": plan.get("bsi_v3_market_context_id"),
        "bsi_v3_market_thesis_id": plan.get("bsi_v3_market_thesis_id"),
        "bsi_v3_poi_id": plan.get("bsi_v3_poi_id"),
        "bsi_v3_entry_opportunity_id": plan.get("bsi_v3_entry_opportunity_id"),
        "bsi_v3_confirmation_id": plan.get("bsi_v3_confirmation_id"),
        "v3_plan_created_at": plan.get("created_at"),
        "v3_poi_touch_status": plan.get("status"),
        "v3_confirmation_timeframe": confirmation_tf,
        "v3_confirmation_policy": plan.get("confirmation_policy") or CONFIRMATION_TIMEFRAME_POLICY.get(spec.strategy_id, "M1_OR_M5_ALLOWED"),
        "confirmation_started_at": plan.get("confirmation_started_at"),
        "confirmation_confirmed_at": plan.get("confirmation_confirmed_at"),
        "confirmation_bar_close_at": plan.get("confirmation_bar_close_at"),
        "v3_confluence_strategy_ids": confluent_strategy_ids,
        "v3_confluence_count": len(confluent_strategy_ids),
        "plan_confidence": plan.get("current_plan_confidence"),
        "initial_plan_confidence": plan.get("initial_plan_confidence"),
        "max_plan_confidence": plan.get("max_plan_confidence"),
        "plan_confidence_band": plan.get("plan_confidence_band"),
        "plan_watch_floor": min_plan_confidence,
        "source_rule_ids": list(spec.source_rule_ids),
        "source_videos": confluent_source_videos or list(spec.source_videos),
        "management_model": spec.management_model,
        "stop_model": spec.stop_model,
        "target_model": spec.target_model,
        "fvg_entry_zone_low": op.fvg_low,
        "fvg_entry_zone_high": op.fvg_high,
        "execution_entry_source": "planned_poi_touch_then_current_bid_ask",
        "min_required_rr": min_rr,
    }
    metadata = {
        "candle_time": op.entry_time.isoformat(),
        "atr": float(ctx.atr_m15) if ctx.atr_m15 is not None else None,
        "spread": float(ctx.spread) if ctx.spread is not None else None,
        "broker_min_stop_distance": float(ctx.broker_min_stop_distance) if ctx.broker_min_stop_distance is not None else None,
        "final_stop_distance": op.risk_distance,
        "active_methodology": METHODOLOGY,
    }
    return StrategySignal(
        strategy_id="bsi",
        strategy_family="bsi",
        symbol=ctx.symbol,
        broker_symbol=ctx.broker_symbol,
        direction=op.direction,
        timeframe=f"{op.source_timeframe}(bsi_v3_planned)",
        generated_at=ctx.generated_at,
        valid=True,
        raw_signal_strength=strength,
        proposed_entry=op.entry,
        stop_loss=op.stop,
        take_profit=op.target,
        reward_risk=op.rr,
        regime=ctx.regime,
        evidence=evidence,
        metadata=metadata,
    )


def evaluate_bsi_v3_runtime_detectors(ctx: StrategyContext, allowed_strategy_ids: set[str]) -> StrategySignal | None:
    """Return the best current V3 detector signal, or None when no V3 setup exists."""
    min_rr = _env_float("MT5_BSI_V3_MIN_RISK_REWARD", 1.25)
    rejections: dict[str, list[str]] = {}
    candidates: list[RuntimeOpportunity] = []
    for spec in RUNTIME_SPECS:
        if allowed_strategy_ids and spec.strategy_id not in allowed_strategy_ids:
            continue
        detector = RUNTIME_DETECTOR_REGISTRY.get(spec.strategy_id)
        if detector is None:
            continue
        opps, reasons = detector(ctx, spec)
        if reasons:
            rejections[spec.strategy_id] = reasons
        for op in opps:
            if op.rr >= min_rr:
                candidates.append(op)
            else:
                rejections[spec.strategy_id] = ["FINAL_RR_BELOW_V3_MINIMUM"]
    if not candidates:
        return None
    best = max(candidates, key=lambda op: (op.rr, op.entry_time))
    spec = best.spec
    strength = min(100.0, 72.0 + min(18.0, max(0.0, best.rr - min_rr) * 6.0))
    evidence = {
        "bsi_version": METHODOLOGY,
        "active_methodology": METHODOLOGY,
        "setup_subtype": spec.strategy_id,
        "subtype_activation_status": ACTIVE_MT5,
        "v3_strategy_id": spec.strategy_id,
        "v3_detector_validation": "STRATEGY_SPECIFIC_RUNTIME",
        "v3_bridge_source": "v3_runtime_detector_registry",
        "v3_detector_registry_size": len(RUNTIME_DETECTOR_REGISTRY),
        "v3_detector_available_strategy_count": len(RUNTIME_DETECTOR_REGISTRY),
        "v3_runtime_rejections": rejections,
        "source_rule_ids": list(spec.source_rule_ids),
        "source_videos": list(spec.source_videos),
        "management_model": spec.management_model,
        "stop_model": spec.stop_model,
        "target_model": spec.target_model,
        "fvg_entry_zone_low": best.fvg_low,
        "fvg_entry_zone_high": best.fvg_high,
        "execution_entry_source": "current_bid_ask",
        "min_required_rr": min_rr,
    }
    metadata = {
        "candle_time": best.entry_time.isoformat(),
        "atr": float(ctx.atr_m15) if ctx.atr_m15 is not None else None,
        "spread": float(ctx.spread) if ctx.spread is not None else None,
        "broker_min_stop_distance": float(ctx.broker_min_stop_distance) if ctx.broker_min_stop_distance is not None else None,
        "final_stop_distance": best.risk_distance,
        "active_methodology": METHODOLOGY,
    }
    return StrategySignal(
        strategy_id="bsi",
        strategy_family="bsi",
        symbol=ctx.symbol,
        broker_symbol=ctx.broker_symbol,
        direction=best.direction,
        timeframe="M15(bsi_v3_runtime)",
        generated_at=ctx.generated_at,
        valid=True,
        raw_signal_strength=strength,
        proposed_entry=best.entry,
        stop_loss=best.stop,
        take_profit=best.target,
        reward_risk=best.rr,
        regime=ctx.regime,
        evidence=evidence,
        metadata=metadata,
    )
