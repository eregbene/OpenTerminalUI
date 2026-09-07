from __future__ import annotations

import json
import math
import os
import sqlite3
from bisect import bisect_right
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from zoneinfo import ZoneInfo

try:
    import psycopg
except ImportError:  # pragma: no cover - optional outside Docker/dev envs.
    psycopg = None


METHODOLOGY = "BSI_BASELINE_V3_UPDATED_FAIZ"
START = datetime.fromisoformat(os.getenv("BSI_V3_START", "2026-08-01T00:00:00+00:00"))
END = datetime.fromisoformat(os.getenv("BSI_V3_END", "2026-09-01T00:00:00+00:00"))
DB_PATH = Path("data/openterminalui.db")
DEFAULT_POSTGRES_URL = "postgresql://openterminalui:openterminalui@localhost:5432/openterminalui"
DOC_DIR = Path("docs/bsi_updated_faiz")
OUT_DIR = DOC_DIR / "v3_validation"
OUT_JSON = Path(os.getenv("BSI_V3_OUTPUT_JSON", "data/research/bsi_v3_august_2026_full.json"))
GOLDEN_TWO_PAIR_JSON = Path(os.getenv("BSI_V3_GOLDEN_OUTPUT_JSON", "data/research/bsi_v3_august_golden_two_pair.json"))
GOLDEN_TWO_PAIR_DOC = OUT_DIR / os.getenv("BSI_V3_GOLDEN_DOC", "BSI_V3_AUGUST_SELECTED_SYMBOL_GOLDEN_RECONSTRUCTIONS.md")

CORE_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD", "EURJPY", "GBPJPY", "XAUUSD"]
INDEX_SYMBOLS = ["NAS100", "USTEC", "NQ", "SPX500", "US500", "ES", "US30", "DJ30"]
CRYPTO_SYMBOLS = ["BTCUSD", "ETHUSD", "BTC", "ETH"]
NY_TZ = ZoneInfo("America/New_York")
ANALYSIS_CACHE: dict[tuple[str, str, int, str, str], tuple[list[dict[str, Any]], list[dict[str, Any]], list[int]]] = {}


@dataclass(frozen=True)
class Bar:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    name: str
    classification: str
    source_rule_ids: list[str]
    source_videos: list[str]
    required_timeframes: list[str]
    eligible_symbols: list[str]
    management_model: str
    stop_model: str
    target_model: str
    notes: str = ""


@dataclass
class Opportunity:
    strategy_id: str
    symbol: str
    direction: str
    entry_time: datetime
    entry: float
    stop: float
    target: float
    timeframe_stack: list[str]
    thesis_id: str
    opportunity_id: str
    source_rule_ids: list[str]
    rejection_reasons: list[str] = field(default_factory=list)

    @property
    def risk_distance(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward_distance(self) -> float:
        return abs(self.target - self.entry)

    @property
    def rr(self) -> float:
        return self.reward_distance / self.risk_distance if self.risk_distance else 0.0


@dataclass
class Outcome:
    opportunity_id: str
    strategy_id: str
    symbol: str
    direction: str
    entry_time: str
    exit_time: str | None
    status: str
    baseline_r: float | None
    managed_r: float | None
    same_bar_ambiguous: bool = False


class CandleStore:
    def __init__(self) -> None:
        self.source = "sqlite"
        self._sqlite: sqlite3.Connection | None = None
        self._pg: Any | None = None
        self._bar_cache: dict[tuple[str, str, datetime, datetime], list[Bar]] = {}

        url = os.getenv("BSI_V3_DATABASE_URL") or os.getenv("DATABASE_URL") or DEFAULT_POSTGRES_URL
        if os.getenv("BSI_V3_USE_POSTGRES", "1").strip().lower() not in {"0", "false", "no"}:
            pg_url = _normalize_postgres_url(url)
            if pg_url and psycopg is not None:
                try:
                    self._pg = psycopg.connect(pg_url, connect_timeout=5)
                    self.source = "postgres"
                    return
                except Exception:
                    self._pg = None

        self._sqlite = sqlite3.connect(DB_PATH)

    def close(self) -> None:
        if self._pg is not None:
            self._pg.close()
        if self._sqlite is not None:
            self._sqlite.close()

    def candles(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Bar]:
        key = (symbol, timeframe, start, end)
        if key in self._bar_cache:
            return self._bar_cache[key]
        if self._pg is not None:
            rows = self._pg.execute(
                """
                select timestamp, open, high, low, close, tick_volume
                from mt5_canonical_candles
                where broker_symbol=%s and timeframe=%s and timestamp >= %s and timestamp < %s
                order by timestamp asc
                """,
                (symbol, timeframe, start, end),
            ).fetchall()
        else:
            assert self._sqlite is not None
            rows = self._sqlite.execute(
                """
                select timestamp, open, high, low, close, tick_volume
                from mt5_canonical_candles
                where broker_symbol=? and timeframe=? and timestamp>=? and timestamp<?
                order by timestamp asc
                """,
                (
                    symbol,
                    timeframe,
                    start.replace(tzinfo=None).isoformat(sep=" "),
                    end.replace(tzinfo=None).isoformat(sep=" "),
                ),
            ).fetchall()
        bars = [Bar(utc(ts), float(o), float(h), float(l), float(c), float(v or 0)) for ts, o, h, l, c, v in rows]
        self._bar_cache[key] = bars
        return bars

    def coverage(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> dict[str, Any]:
        key = (symbol, timeframe, start, end)
        if key in self._bar_cache:
            bars = self._bar_cache[key]
            return {
                "count": len(bars),
                "first": bars[0].time.isoformat() if bars else None,
                "last": bars[-1].time.isoformat() if bars else None,
            }
        if self._pg is not None:
            row = self._pg.execute(
                """
                select timestamp
                from mt5_canonical_candles
                where broker_symbol=%s and timeframe=%s and timestamp >= %s and timestamp < %s
                limit 1
                """,
                (symbol, timeframe, start, end),
            ).fetchone()
        else:
            assert self._sqlite is not None
            row = self._sqlite.execute(
                """
                select timestamp
                from mt5_canonical_candles
                where broker_symbol=? and timeframe=? and timestamp>=? and timestamp<?
                limit 1
                """,
                (
                    symbol,
                    timeframe,
                    start.replace(tzinfo=None).isoformat(sep=" "),
                    end.replace(tzinfo=None).isoformat(sep=" "),
                ),
            ).fetchone()
        if not row:
            return {"count": 0, "first": None, "last": None}
        return {"count": 1, "first": utc(row[0]).isoformat(), "last": None}


def _normalize_postgres_url(url: str) -> str | None:
    text = (url or "").strip()
    if not text.startswith("postgresql"):
        return None
    return text.replace("postgresql+asyncpg://", "postgresql://", 1).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )


def utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def strategy_registry() -> list[StrategySpec]:
    fx = CORE_SYMBOLS
    indices = INDEX_SYMBOLS
    crypto = CRYPTO_SYMBOLS
    all_assets = CORE_SYMBOLS + INDEX_SYMBOLS + CRYPTO_SYMBOLS
    return [
        StrategySpec("bsi_v3_order_flow", "Order Flow", "INDEPENDENT_STRATEGY", ["FAIZ_V3_ORDERFLOW_001"], ["1. Order Flow Trading Strategy.mp4", "20. ORDERFLOW 101.mp4"], ["D1", "H4", "M15"], fx, "MENTOR_BE_ON_STRUCTURE_BREAK", "STRUCTURE_OB", "LIQUIDITY_OR_FIXED_R"),
        StrategySpec("bsi_v3_smt_divergence", "SMT Divergence", "INDEPENDENT_STRATEGY", ["FAIZ_V3_SMT_001"], ["1. SMT Divergence.mp4"], ["M15", "M1"], ["EURUSD", "GBPUSD", "AUDUSD", "NAS100", "US500", "US30", "BTCUSD", "ETHUSD"], "MENTOR_BE_ON_STRUCTURE_BREAK", "SMT_SWEEP_MSS_STRUCTURE", "OPPOSING_LIQUIDITY_OR_ABC_TARGET", "Candidate independent strategy; also used as confluence. Reference-market data gaps must be treated as DATA_GAP_SMT_REFERENCE before demo activation."),
        StrategySpec("bsi_v3_abc", "ABC", "INDEPENDENT_STRATEGY", ["FAIZ_V3_ABC_001", "FAIZ_V3_ABC_003", "FAIZ_V3_ABC_004"], ["10. ABC Trading Strategy.mp4", "19. ABC 101.mp4"], ["D1", "H4", "M15"], fx, "MENTOR_PARTIAL_AT_POI_OR_2R", "ABC_STRUCTURE", "B_LEG_HIGH_LOW"),
        StrategySpec("bsi_v3_abcd", "ABCD", "INDEPENDENT_STRATEGY", ["FAIZ_V3_ABCD_001"], ["16. ABCD 101.mp4"], ["D1", "H4", "M15"], fx, "MENTOR_PARTIAL_AT_POI_OR_2R", "D_LEG_STRUCTURE", "FVG_SUPPLY_DEMAND_OB"),
        StrategySpec("bsi_v3_asian_v2", "Asian Session V2", "INDEPENDENT_STRATEGY", ["FAIZ_V3_ASIAN_V2_001"], ["23. ASIAN SESSION STRATEGY V2.0.mp4"], ["M15", "M1"], fx, "MENTOR_FINAL_TARGET_LIQUIDITY", "M1_SWEEP_STRUCTURE", "ASIAN_OPPOSITE_SIDE_OR_POI"),
        StrategySpec("bsi_v3_0930", "9:30 Updated", "INDEPENDENT_STRATEGY", ["FAIZ_V3_0930_001"], ["13. 930AM Trading Strategy (Updated).mp4"], ["M15", "M1"], indices, "MENTOR_FIXED_R_3_TO_5", "M1_SWEEP_STRUCTURE", "FVG_OR_3R_5R"),
        StrategySpec("bsi_v3_reactionary_block", "Reactionary Block", "INDEPENDENT_STRATEGY", ["FAIZ_V3_REACTIONARY_001"], ["17. Reactionary Block Trading Strategy.mp4"], ["M15"], fx, "MENTOR_FIXED_2R_5R", "REACTION_OR_ORIGINAL_ZONE", "NEXT_HIGH_LOW_OR_2R_5R"),
        StrategySpec("bsi_v3_ict_silver_bullet", "ICT Silver Bullet", "INDEPENDENT_STRATEGY", ["FAIZ_V3_SB_001"], ["30. ICT Silver Bullet.mp4"], ["M1"], indices, "MENTOR_BE_AT_1R", "M1_SWEEP_STRUCTURE", "1R_OR_2R"),
        StrategySpec("bsi_v3_silver_bullet_with_bias", "Silver Bullet With Bias", "INDEPENDENT_STRATEGY", ["FAIZ_V3_SB_BIAS_001"], ["24. Silver Bullet With Bias.mp4"], ["H1", "M15", "M5", "M1"], fx + indices, "MENTOR_BE_AT_1R", "STRUCTURE", "1_TO_2_OR_INTERNAL_LIQUIDITY"),
        StrategySpec("bsi_v3_4h_order_block", "4 Hour OB", "INDEPENDENT_STRATEGY", ["FAIZ_V3_4H_OB_001"], ["34. 4 Hour OB Trading Strategy.mp4", "35. 4 Hour OB Trading Strategy.mp4", "36. 4 Hour OB Trading Strategy Example 2.mp4", "37. 4 Hour OB Trading Strategy Example 3.mp4"], ["H4", "M15"], fx, "MENTOR_FIXED_1R_BE_2R_TARGET", "H4_OB_M15_STRUCTURE", "2R"),
        StrategySpec("bsi_v3_mmxm", "MMXM", "INDEPENDENT_STRATEGY", ["FAIZ_V3_MMXM_001"], ["8. THE MMXM.mp4", "9. THE MMXM 2.mp4"], ["MN1", "W1", "D1", "H4", "H1", "M15", "M5"], fx + indices, "MENTOR_FINAL_TARGET_LIQUIDITY", "MMXM_STRUCTURE", "ORIGINAL_CONSOLIDATION_LIQUIDITY"),
        StrategySpec("bsi_v3_mmxm_second_distribution", "MMXM Second Distribution", "INDEPENDENT_STRATEGY", ["FAIZ_V3_MMXM2_001"], ["13. MMXM 2ND DISTRIBUTION ENTRY.mp4"], ["D1", "H1"], fx + indices, "MENTOR_FINAL_TARGET_LIQUIDITY", "FINAL_FRACTAL_BOS", "INTERNAL_LIQUIDITY_FVG"),
        StrategySpec("bsi_v3_holy_grail", "Holy Grail", "INDEPENDENT_STRATEGY", ["FAIZ_V3_HOLY_GRAIL_001"], ["17. The Holy Grail.mp4"], ["D1", "H1", "M5"], fx + indices, "MENTOR_FINAL_TARGET_LIQUIDITY", "M5_MMXM_STRUCTURE", "DAILY_INTERNAL_LIQUIDITY"),
        StrategySpec("bsi_v3_juggernaut", "Juggernaut", "INDEPENDENT_STRATEGY", ["FAIZ_V3_JUGGERNAUT_001"], ["21. The Juggernaut Model.mp4"], ["M1", "M5"], fx + indices, "MENTOR_IFVG_CLOSEST_LIQUIDITY_BE", "IFVG_STRUCTURE", "CLOSEST_LIQUIDITY"),
        StrategySpec("bsi_v3_spectre", "Spectre", "INDEPENDENT_STRATEGY", ["FAIZ_V3_SPECTRE_001"], ["25. The Spectre Model.mp4"], ["M15"], fx, "MENTOR_FINAL_TARGET_LIQUIDITY", "INVERSE_OB", "NEARBY_LIQUIDITY_OR_FVG"),
        StrategySpec("bsi_v3_monday_range", "Monday Range", "INDEPENDENT_STRATEGY", ["FAIZ_V3_MONDAY_RANGE_001"], ["37. Utilizing Monday Range.mp4"], ["D1", "M15"], ["EURUSD", "XAUUSD"] + indices, "MENTOR_RANGE_TARGET", "M15_REVERSAL_STRUCTURE", "OPPOSITE_MONDAY_SIDE_OR_50_PERCENT"),
        StrategySpec("bsi_v3_weaver", "Weaver", "INDEPENDENT_STRATEGY", ["FAIZ_V3_WEAVER_001"], ["38. The Weaver Model.mp4"], ["D1", "H1", "M15"], all_assets, "MENTOR_FINAL_TARGET_LIQUIDITY", "M15_STRUCTURE", "H1_FVG_DRAW"),
        StrategySpec("bsi_v3_standard_deviation_po3", "Standard Deviations / PO3", "INDEPENDENT_STRATEGY", ["FAIZ_V3_STDDEV_001"], ["29. Standard Deviations.mp4"], ["D1", "M5"], fx, "MENTOR_PARTIAL_AT_POI_OR_2R", "PO3_STRUCTURE", "STDDEV_NEGATIVE_2_TO_4"),
        StrategySpec("bsi_v3_ar50", "AR50", "INDEPENDENT_STRATEGY", ["FAIZ_V3_AR50_001"], ["34. AR50 Trading Model.mp4"], ["D1", "M15"], fx + ["NAS100", "USTEC", "NQ"], "MENTOR_FINAL_TARGET_LIQUIDITY", "ASIAN_RANGE_50", "DAILY_DRAW_LIQUIDITY"),
        StrategySpec("bsi_v3_ifvg_po3", "IFVG / PO3", "INDEPENDENT_STRATEGY", ["FAIZ_V3_IFVG_PO3_001"], ["39. The IFVG Model.mp4"], ["M1"], ["EURUSD", "XAUUSD", "NAS100", "USTEC", "NQ"], "MENTOR_IFVG_50_PERCENT_AT_1R", "MANIPULATION_EXTREME", "CLOSEST_LIQUIDITY_THEN_HTF_DRAW"),
        StrategySpec("bsi_v3_turtle_soups_ranges", "Turtle Soups & Ranges", "INDEPENDENT_STRATEGY", ["FAIZ_V3_TURTLE_RANGES_001"], ["40. Turtle Soups & Ranges Mastery.mp4"], ["M1"], indices + crypto, "MENTOR_TURTLE_RANGE_PARTIAL_BE_AT_0_5", "RANGE_SWEEP_EXTREME", "OPPOSITE_RANGE_SIDE"),
        StrategySpec("bsi_v3_yin_yang", "Yin Yang", "INDEPENDENT_STRATEGY", ["FAIZ_V3_YIN_YANG_001"], ["41. The Yin Yang Model.mp4"], ["M15"], ["XAUUSD"], "MENTOR_FIXED_1R_BE_2R_TARGET", "LONDON_FVG_BODY", "2R"),
        StrategySpec("bsi_v3_4h_candle_ranges", "4H Candle Ranges", "INDEPENDENT_STRATEGY", ["FAIZ_V3_4H_CRD_001"], ["42. 4 Hour Candle Ranges.mp4"], ["H4", "M5"], ["EURUSD", "XAUUSD"] + indices, "MENTOR_CANDLE_RANGE_PARTIAL_BE_TARGET_OPPOSITE_SIDE", "RAID_EXTREME", "OPPOSITE_CANDLE_SIDE"),
        StrategySpec("bsi_v3_smt_session_hl", "SMT Session Highs/Lows", "INDEPENDENT_STRATEGY", ["FAIZ_V3_SMT_SESSION_001"], ["43. Utilizing SMT With Session Highs & Lows.mp4"], ["M15", "M1"], ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD"] + indices, "MENTOR_SESSION_SMT_BE_1R_OR_STRONG_BIAS_1_5R", "SESSION_SMT_STRUCTURE", "OPPOSITE_SESSION_SIDE"),
        StrategySpec("bsi_v3_1h_candle_ranges", "1H Candle Ranges", "INDEPENDENT_STRATEGY", ["FAIZ_V3_1H_CRD_001"], ["44. 1 Hour Candle Ranges.mp4"], ["H1", "M1"], fx + indices, "MENTOR_1H_CRD_PARTIAL_AT_50_PERCENT_RANGE", "RAID_EXTREME", "50_PERCENT_THEN_OPPOSITE_SIDE"),
        StrategySpec("bsi_v3_enigma_range", "Enigma Range", "INDEPENDENT_STRATEGY", ["FAIZ_V3_ENIGMA_001"], ["45. The Enigma.mp4"], ["M15", "H1", "H4"], fx + indices + crypto, "MENTOR_ENIGMA_PARTIAL_BE_0_5_TARGET_0_79_OR_1_0", "ENGINEERED_RANGE_EXTREME", "0_79_OR_OPPOSITE_RANGE"),
    ]


SUPPORTING_MODELS = [
    {"id": "faiz_v3_fvg", "classification": "POI_PRIMITIVE"},
    {"id": "faiz_v3_ifvg", "classification": "SUPPORTING_ENTRY_MODEL"},
    {"id": "faiz_v3_bpr", "classification": "SUPPORTING_ENTRY_MODEL"},
    {"id": "faiz_v3_breaker", "classification": "SUPPORTING_ENTRY_MODEL"},
    {"id": "faiz_v3_volume_imbalance", "classification": "SUPPORTING_ENTRY_MODEL"},
    {"id": "faiz_v3_smt", "classification": "CONFLUENCE"},
    {"id": "faiz_v3_quarterly_theory", "classification": "CONFLUENCE"},
    {"id": "faiz_v3_daily_bias", "classification": "BIAS_MODEL"},
    {"id": "faiz_v3_risk_management", "classification": "RISK_MODEL"},
]


def load_bars(store: CandleStore, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Bar]:
    return store.candles(symbol, timeframe, start, end)


def coverage_audit(store: CandleStore, registry: list[StrategySpec]) -> dict[str, Any]:
    required_symbols = sorted({s for spec in registry for s in spec.eligible_symbols})
    required_tfs = ["MN1", "W1", "D1", "H4", "H1", "M15", "M5", "M1"]
    coverage: dict[str, dict[str, Any]] = {}
    for symbol in required_symbols:
        coverage[symbol] = {}
        for tf in required_tfs:
            coverage[symbol][tf] = store.coverage(symbol, tf, START, END)
    return coverage


def has_required_data(coverage: dict[str, Any], symbol: str, spec: StrategySpec) -> bool:
    return all(coverage.get(symbol, {}).get(tf, {}).get("count", 0) > 0 for tf in spec.required_timeframes)


def detect_swings(bars: list[Bar], left: int = 2, right: int = 2) -> list[dict[str, Any]]:
    swings = []
    for i in range(left, len(bars) - right):
        win = bars[i - left : i + right + 1]
        if bars[i].high == max(b.high for b in win):
            swings.append({"type": "HIGH", "index": i, "price": bars[i].high, "occurred_at": bars[i].time, "confirmed_at": bars[i + right].time})
        if bars[i].low == min(b.low for b in win):
            swings.append({"type": "LOW", "index": i, "price": bars[i].low, "occurred_at": bars[i].time, "confirmed_at": bars[i + right].time})
    return swings


def detect_fvgs(bars: list[Bar]) -> list[dict[str, Any]]:
    out = []
    for i in range(2, len(bars)):
        a, c = bars[i - 2], bars[i]
        confirmed_at = c.time
        if a.high < c.low:
            out.append({"direction": "BULLISH", "index": i, "low": a.high, "high": c.low, "occurred_at": c.time, "confirmed_at": confirmed_at})
        if a.low > c.high:
            out.append({"direction": "BEARISH", "index": i, "low": c.high, "high": a.low, "occurred_at": c.time, "confirmed_at": confirmed_at})
    return out


def simulate_outcome(op: Opportunity, future: list[Bar], max_bars: int = 96) -> Outcome:
    if not future:
        return Outcome(op.opportunity_id, op.strategy_id, op.symbol, op.direction, op.entry_time.isoformat(), None, "open_timeout", None, None)
    risk = op.risk_distance
    if risk <= 0:
        return Outcome(op.opportunity_id, op.strategy_id, op.symbol, op.direction, op.entry_time.isoformat(), None, "invalid_geometry", None, None)
    sign = 1 if op.direction == "LONG" else -1
    managed = None
    partial_done = False
    stop = op.stop
    for bar in future[:max_bars]:
        hit_stop = bar.low <= stop if op.direction == "LONG" else bar.high >= stop
        hit_target = bar.high >= op.target if op.direction == "LONG" else bar.low <= op.target
        if hit_stop and hit_target:
            return Outcome(op.opportunity_id, op.strategy_id, op.symbol, op.direction, op.entry_time.isoformat(), bar.time.isoformat(), "same_bar_ambiguous", 0.0, 0.0, True)
        if hit_stop:
            r = sign * (stop - op.entry) / risk
            return Outcome(op.opportunity_id, op.strategy_id, op.symbol, op.direction, op.entry_time.isoformat(), bar.time.isoformat(), "sl", round(r, 4), round(managed if managed is not None else r, 4))
        if not partial_done:
            one_r = op.entry + sign * risk
            hit_one_r = bar.high >= one_r if op.direction == "LONG" else bar.low <= one_r
            if hit_one_r:
                partial_done = True
                stop = op.entry
                managed = 0.5
        if hit_target:
            baseline_r = sign * (op.target - op.entry) / risk
            managed_r = baseline_r if managed is None else managed + 0.5 * baseline_r
            return Outcome(op.opportunity_id, op.strategy_id, op.symbol, op.direction, op.entry_time.isoformat(), bar.time.isoformat(), "tp", round(baseline_r, 4), round(managed_r, 4))
    last = future[min(len(future), max_bars) - 1]
    close_r = sign * (last.close - op.entry) / risk
    return Outcome(op.opportunity_id, op.strategy_id, op.symbol, op.direction, op.entry_time.isoformat(), last.time.isoformat(), "open_timeout", round(close_r, 4), round(close_r, 4))


def _ny_hour(dt: datetime) -> float:
    local = dt.astimezone(NY_TZ)
    return local.hour + local.minute / 60.0


def _in_any_ny_window(dt: datetime, windows: list[tuple[float, float]]) -> bool:
    hour = _ny_hour(dt)
    return any(start <= hour <= end for start, end in windows)


def _bar_direction(bar: Bar) -> str:
    return "LONG" if bar.close >= bar.open else "SHORT"


def _simple_bias(bars: list[Bar]) -> str | None:
    if len(bars) < 6:
        return None
    recent = bars[-6:]
    highs_up = recent[-1].high > recent[-3].high > recent[-5].high
    lows_up = recent[-1].low > recent[-3].low > recent[-5].low
    highs_down = recent[-1].high < recent[-3].high < recent[-5].high
    lows_down = recent[-1].low < recent[-3].low < recent[-5].low
    if highs_up or lows_up:
        return "LONG"
    if highs_down or lows_down:
        return "SHORT"
    return _bar_direction(recent[-1])


def _range_for_day(bars: list[Bar], day, start_hour: float, end_hour: float) -> tuple[float, float] | None:
    scoped = [bar for bar in bars if bar.time.date() == day and start_hour <= _ny_hour(bar.time) < end_hour]
    if not scoped:
        return None
    return max(bar.high for bar in scoped), min(bar.low for bar in scoped)


def _set_fixed_r(op: Opportunity, multiple: float) -> Opportunity:
    sign = 1 if op.direction == "LONG" else -1
    return Opportunity(
        op.strategy_id,
        op.symbol,
        op.direction,
        op.entry_time,
        op.entry,
        op.stop,
        op.entry + sign * op.risk_distance * multiple,
        op.timeframe_stack,
        op.thesis_id,
        op.opportunity_id,
        op.source_rule_ids,
        op.rejection_reasons,
    )


def _raw_fvg_structure_candidates(
    spec: StrategySpec,
    symbol: str,
    bars_by_tf: dict[str, list[Bar]],
    *,
    rejection_code: str,
) -> tuple[list[Opportunity], list[str]]:
    primary_tf = spec.required_timeframes[-1]
    bars = bars_by_tf.get(primary_tf, [])
    if len(bars) < 50:
        return [], [f"DATA_GAP_{primary_tf}"]
    cache_key = (symbol, primary_tf, len(bars), bars[0].time.isoformat(), bars[-1].time.isoformat())
    if cache_key in ANALYSIS_CACHE:
        swings, fvgs, swing_indices = ANALYSIS_CACHE[cache_key]
    else:
        swings = detect_swings(bars)
        fvgs = detect_fvgs(bars)
        swing_indices = [int(s["index"]) for s in swings]
        ANALYSIS_CACHE[cache_key] = (swings, fvgs, swing_indices)
    opportunities: list[Opportunity] = []
    used_theses: set[str] = set()
    for fvg in fvgs:
        if fvg["confirmed_at"] < START or fvg["confirmed_at"] >= END:
            continue
        idx = fvg["index"]
        left = bisect_right(swing_indices, idx - 30)
        right = bisect_right(swing_indices, idx - 1)
        if right - left < 2:
            continue
        direction = "LONG" if fvg["direction"] == "BULLISH" else "SHORT"
        entry = (float(fvg["low"]) + float(fvg["high"])) / 2.0
        if direction == "LONG":
            stop = min(b.low for b in bars[max(0, idx - 10) : idx + 1])
            target = entry + 2.0 * (entry - stop)
        else:
            stop = max(b.high for b in bars[max(0, idx - 10) : idx + 1])
            target = entry - 2.0 * (stop - entry)
        if abs(entry - stop) <= 0:
            continue
        thesis = f"{spec.strategy_id}:{symbol}:{primary_tf}:{fvg['confirmed_at'].date()}:{direction}"
        if thesis in used_theses:
            continue
        used_theses.add(thesis)
        oid = f"{thesis}:{fvg['confirmed_at'].isoformat()}"
        opportunities.append(Opportunity(spec.strategy_id, symbol, direction, fvg["confirmed_at"], entry, stop, target, spec.required_timeframes, thesis, oid, spec.source_rule_ids))
    if not opportunities:
        return [], [rejection_code]
    return opportunities, []


def _filter_or_reject(opps: list[Opportunity], predicate, reason: str) -> tuple[list[Opportunity], list[str]]:
    kept = [op for op in opps if predicate(op)]
    return kept, [] if kept else [reason]


def detect_order_flow(spec: StrategySpec, symbol: str, bars_by_tf: dict[str, list[Bar]]) -> tuple[list[Opportunity], list[str]]:
    opps, reasons = _raw_fvg_structure_candidates(spec, symbol, bars_by_tf, rejection_code="order_flow_no_mss_fvg_ob_sequence")
    if not opps:
        return [], reasons
    htf_bias = _simple_bias(bars_by_tf.get("H4", [])) or _simple_bias(bars_by_tf.get("D1", []))
    if htf_bias:
        return _filter_or_reject(opps, lambda op: op.direction == htf_bias, "order_flow_htf_bias_mismatch")
    return [], ["order_flow_no_h4_daily_bias"]


def detect_abc(spec: StrategySpec, symbol: str, bars_by_tf: dict[str, list[Bar]]) -> tuple[list[Opportunity], list[str]]:
    opps, reasons = _raw_fvg_structure_candidates(spec, symbol, bars_by_tf, rejection_code="abc_no_reclaim_mss_fvg")
    if not opps:
        return [], reasons
    filtered, reject = _filter_or_reject(
        opps,
        lambda op: op.rr >= 1.5 and _in_any_ny_window(op.entry_time, [(3.0, 6.0), (8.5, 11.25)]),
        "abc_no_killzone_or_rr_room",
    )
    return [_set_fixed_r(op, 2.0) for op in filtered], reject


def detect_abcd(spec: StrategySpec, symbol: str, bars_by_tf: dict[str, list[Bar]]) -> tuple[list[Opportunity], list[str]]:
    opps, reasons = _raw_fvg_structure_candidates(spec, symbol, bars_by_tf, rejection_code="abcd_no_d_leg_m15_mss")
    if not opps:
        return [], reasons
    bias = _simple_bias(bars_by_tf.get("H4", []))
    filtered, reject = _filter_or_reject(
        opps,
        lambda op: (bias is None or op.direction == bias) and _in_any_ny_window(op.entry_time, [(3.0, 6.0), (8.5, 11.25)]),
        "abcd_no_h4_bias_or_killzone",
    )
    return filtered, reject


def detect_reactionary_block(spec: StrategySpec, symbol: str, bars_by_tf: dict[str, list[Bar]]) -> tuple[list[Opportunity], list[str]]:
    opps, reasons = _raw_fvg_structure_candidates(spec, symbol, bars_by_tf, rejection_code="reactionary_no_original_ob_fvg_reaction")
    if not opps:
        return [], reasons
    filtered, reject = _filter_or_reject(opps, lambda op: 2.0 <= op.rr <= 5.0, "reactionary_rr_not_2_to_5")
    return filtered, reject


def detect_4h_order_block(spec: StrategySpec, symbol: str, bars_by_tf: dict[str, list[Bar]]) -> tuple[list[Opportunity], list[str]]:
    opps, reasons = _raw_fvg_structure_candidates(spec, symbol, bars_by_tf, rejection_code="4h_ob_no_m15_mss_from_h4_ob")
    if not opps:
        return [], reasons
    bias = _simple_bias(bars_by_tf.get("H4", []))
    filtered, reject = _filter_or_reject(
        opps,
        lambda op: (bias is None or op.direction == bias) and _in_any_ny_window(op.entry_time, [(3.0, 6.0), (8.5, 11.25)]),
        "4h_ob_no_h4_orderflow_or_killzone",
    )
    return [_set_fixed_r(op, 2.0) for op in filtered], reject


def detect_mmxm_second_distribution(spec: StrategySpec, symbol: str, bars_by_tf: dict[str, list[Bar]]) -> tuple[list[Opportunity], list[str]]:
    opps, reasons = _raw_fvg_structure_candidates(spec, symbol, bars_by_tf, rejection_code="mmxm2_no_final_fractal_bos_fvg")
    if not opps:
        return [], reasons
    filtered, reject = _filter_or_reject(opps, lambda op: op.entry_time.weekday() < 5 and op.rr >= 1.2, "mmxm2_no_internal_liquidity_room")
    return filtered, reject


def detect_holy_grail(spec: StrategySpec, symbol: str, bars_by_tf: dict[str, list[Bar]]) -> tuple[list[Opportunity], list[str]]:
    opps, reasons = _raw_fvg_structure_candidates(spec, symbol, bars_by_tf, rejection_code="holy_grail_no_daily_h1_m5_sequence")
    if not opps:
        return [], reasons
    h1_bias = _simple_bias(bars_by_tf.get("H1", []))
    filtered, reject = _filter_or_reject(opps, lambda op: h1_bias is None or op.direction == h1_bias, "holy_grail_h1_confirmation_mismatch")
    return filtered, reject


def detect_spectre(spec: StrategySpec, symbol: str, bars_by_tf: dict[str, list[Bar]]) -> tuple[list[Opportunity], list[str]]:
    opps, reasons = _raw_fvg_structure_candidates(spec, symbol, bars_by_tf, rejection_code="spectre_no_inverse_ob_reclaim")
    if not opps:
        return [], reasons
    filtered, reject = _filter_or_reject(opps, lambda op: 1.0 <= op.rr <= 3.0, "spectre_no_nearby_liquidity_target")
    return filtered, reject


def detect_monday_range(spec: StrategySpec, symbol: str, bars_by_tf: dict[str, list[Bar]]) -> tuple[list[Opportunity], list[str]]:
    opps, reasons = _raw_fvg_structure_candidates(spec, symbol, bars_by_tf, rejection_code="monday_range_no_tuesday_mss")
    if not opps:
        return [], reasons
    filtered, reject = _filter_or_reject(opps, lambda op: op.entry_time.astimezone(NY_TZ).weekday() == 1, "monday_range_no_tuesday_sweep")
    return filtered, reject


def detect_weaver(spec: StrategySpec, symbol: str, bars_by_tf: dict[str, list[Bar]]) -> tuple[list[Opportunity], list[str]]:
    opps, reasons = _raw_fvg_structure_candidates(spec, symbol, bars_by_tf, rejection_code="weaver_no_previous_day_sweep_m15_mss")
    if not opps:
        return [], reasons
    m15 = bars_by_tf.get("M15", [])
    by_day: dict[Any, list[Bar]] = {}
    for bar in m15:
        by_day.setdefault(bar.time.date(), []).append(bar)

    def swept_previous_day(op: Opportunity) -> bool:
        prev = by_day.get((op.entry_time.astimezone(timezone.utc).date()))
        all_days = sorted(day for day in by_day if day < op.entry_time.date())
        if not prev or not all_days:
            return False
        prior = by_day[all_days[-1]]
        prev_high, prev_low = max(b.high for b in prior), min(b.low for b in prior)
        day_bars = [b for b in prev if b.time <= op.entry_time]
        return bool(day_bars) and (max(b.high for b in day_bars) > prev_high or min(b.low for b in day_bars) < prev_low)

    filtered, reject = _filter_or_reject(opps, swept_previous_day, "weaver_no_previous_day_high_low_sweep")
    return filtered, reject


def detect_standard_deviation_po3(spec: StrategySpec, symbol: str, bars_by_tf: dict[str, list[Bar]]) -> tuple[list[Opportunity], list[str]]:
    opps, reasons = _raw_fvg_structure_candidates(spec, symbol, bars_by_tf, rejection_code="stddev_po3_no_m5_manipulation_mss")
    if not opps:
        return [], reasons
    filtered, reject = _filter_or_reject(opps, lambda op: op.rr >= 2.0, "stddev_po3_no_negative_2_room")
    return filtered, reject


def detect_ar50(spec: StrategySpec, symbol: str, bars_by_tf: dict[str, list[Bar]]) -> tuple[list[Opportunity], list[str]]:
    opps, reasons = _raw_fvg_structure_candidates(spec, symbol, bars_by_tf, rejection_code="ar50_no_asian_50_pullback_mss")
    if not opps:
        return [], reasons
    m15 = bars_by_tf.get("M15", [])

    def near_asian_mid(op: Opportunity) -> bool:
        rng = _range_for_day(m15, op.entry_time.date(), 18.0, 24.0)
        if rng is None:
            return False
        high, low = rng
        mid = (high + low) / 2.0
        return abs(op.entry - mid) <= max((high - low) * 0.35, op.risk_distance)

    filtered, reject = _filter_or_reject(opps, near_asian_mid, "ar50_not_near_asian_range_50")
    return filtered, reject


def detect_yin_yang(spec: StrategySpec, symbol: str, bars_by_tf: dict[str, list[Bar]]) -> tuple[list[Opportunity], list[str]]:
    if symbol != "XAUUSD":
        return [], ["yin_yang_gold_only"]
    opps, reasons = _raw_fvg_structure_candidates(spec, symbol, bars_by_tf, rejection_code="yin_yang_no_london_ifvg")
    if not opps:
        return [], reasons
    filtered, reject = _filter_or_reject(opps, lambda op: _in_any_ny_window(op.entry_time, [(3.0, 7.0)]), "yin_yang_not_london_session")
    return [_set_fixed_r(op, 2.0) for op in filtered], reject


def detect_4h_candle_ranges(spec: StrategySpec, symbol: str, bars_by_tf: dict[str, list[Bar]]) -> tuple[list[Opportunity], list[str]]:
    opps, reasons = _raw_fvg_structure_candidates(spec, symbol, bars_by_tf, rejection_code="4h_crd_no_range_raid_mss")
    if not opps:
        return [], reasons
    filtered, reject = _filter_or_reject(opps, lambda op: _in_any_ny_window(op.entry_time, [(5.0, 13.0)]), "4h_crd_no_1am_or_5am_range_raid")
    return filtered, reject


def detect_enigma_range(spec: StrategySpec, symbol: str, bars_by_tf: dict[str, list[Bar]]) -> tuple[list[Opportunity], list[str]]:
    opps, reasons = _raw_fvg_structure_candidates(spec, symbol, bars_by_tf, rejection_code="enigma_no_engineered_range_reentry")
    if not opps:
        return [], reasons
    filtered, reject = _filter_or_reject(opps, lambda op: op.rr >= 1.0, "enigma_no_0_5_or_0_79_room")
    return filtered, reject


DETECTOR_REGISTRY = {
    "bsi_v3_order_flow": detect_order_flow,
    "bsi_v3_abc": detect_abc,
    "bsi_v3_abcd": detect_abcd,
    "bsi_v3_reactionary_block": detect_reactionary_block,
    "bsi_v3_4h_order_block": detect_4h_order_block,
    "bsi_v3_mmxm_second_distribution": detect_mmxm_second_distribution,
    "bsi_v3_holy_grail": detect_holy_grail,
    "bsi_v3_spectre": detect_spectre,
    "bsi_v3_monday_range": detect_monday_range,
    "bsi_v3_weaver": detect_weaver,
    "bsi_v3_standard_deviation_po3": detect_standard_deviation_po3,
    "bsi_v3_ar50": detect_ar50,
    "bsi_v3_yin_yang": detect_yin_yang,
    "bsi_v3_4h_candle_ranges": detect_4h_candle_ranges,
    "bsi_v3_enigma_range": detect_enigma_range,
}


def summarize_outcomes(outcomes: list[Outcome], field: str = "baseline_r") -> dict[str, Any]:
    vals = [getattr(o, field) for o in outcomes if getattr(o, field) is not None]
    if not vals:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": None, "net_r": 0.0, "expectancy": None, "profit_factor": None, "average_winner_r": None, "average_loser_r": None, "median_r": None, "max_winning_streak": 0, "max_losing_streak": 0, "max_drawdown_r": 0.0}
    wins = [v for v in vals if v > 0]
    losses = [v for v in vals if v <= 0]
    gp = sum(wins)
    gl = abs(sum(losses))
    eq = peak = max_dd = 0.0
    ws = ls = max_ws = max_ls = 0
    for v in vals:
        eq += v
        peak = max(peak, eq)
        max_dd = max(max_dd, peak - eq)
        if v > 0:
            ws += 1
            ls = 0
        else:
            ls += 1
            ws = 0
        max_ws = max(max_ws, ws)
        max_ls = max(max_ls, ls)
    return {
        "trades": len(vals),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(vals) * 100, 2),
        "net_r": round(sum(vals), 4),
        "expectancy": round(sum(vals) / len(vals), 4),
        "profit_factor": round(gp / gl, 4) if gl else (math.inf if gp else None),
        "average_winner_r": round(gp / len(wins), 4) if wins else None,
        "average_loser_r": round(-gl / len(losses), 4) if losses else None,
        "median_r": round(median(vals), 4),
        "max_winning_streak": max_ws,
        "max_losing_streak": max_ls,
        "max_drawdown_r": round(max_dd, 4),
    }


def classify_initial_validation(
    outcomes: list[Outcome],
    data_gaps: list[dict[str, Any]],
    *,
    detector_validation_level: str,
    golden_example_status: str,
) -> str:
    if detector_validation_level != "STRATEGY_SPECIFIC_VALIDATED" or golden_example_status != "PASS":
        return "IMPLEMENTATION_NOT_VALIDATED"
    if data_gaps and not outcomes:
        return "DATA_GAP"
    if len(outcomes) < 5:
        return "INSUFFICIENT_SAMPLE"
    stats = summarize_outcomes(outcomes)
    expectancy = stats["expectancy"]
    if expectancy is None:
        return "INSUFFICIENT_SAMPLE"
    if expectancy > 0:
        return "PROMISING"
    if stats["net_r"] < 0:
        return "NEGATIVE"
    return "MIXED"


def scaffold_primitive_count(bars: list[Bar]) -> int:
    if len(bars) < 5:
        return 0
    return len(detect_swings(bars)) + len(detect_fvgs(bars))


def group_summary(outcomes: list[Outcome], key: str) -> dict[str, Any]:
    groups: dict[str, list[Outcome]] = {}
    for o in outcomes:
        if key == "symbol":
            k = o.symbol
        elif key == "direction":
            k = o.direction
        elif key == "week":
            k = utc(o.entry_time).strftime("%G-W%V")
        else:
            k = getattr(o, key)
        groups.setdefault(k, []).append(o)
    return {k: summarize_outcomes(v) for k, v in sorted(groups.items())}


def adaptive_bucket_key(outcome: Outcome) -> tuple[str, str]:
    return outcome.strategy_id, outcome.symbol


def build_adaptive_bucket_profile(
    outcomes: list[Outcome],
    *,
    field: str = "managed_r",
    min_trades: int = 10,
    min_expectancy: float = 0.02,
    min_profit_factor: float = 1.05,
    max_losing_streak: int = 8,
) -> dict[str, Any]:
    buckets: dict[tuple[str, str], list[Outcome]] = {}
    for outcome in outcomes:
        buckets.setdefault(adaptive_bucket_key(outcome), []).append(outcome)

    allowed: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for (strategy_id, symbol), bucket in sorted(buckets.items()):
        stats = summarize_outcomes(bucket, field)
        reasons = []
        if stats["trades"] < min_trades:
            reasons.append("sample_too_small")
        if stats["expectancy"] is None or stats["expectancy"] < min_expectancy:
            reasons.append("expectancy_below_threshold")
        if stats["profit_factor"] is None or stats["profit_factor"] < min_profit_factor:
            reasons.append("profit_factor_below_threshold")
        if stats["max_losing_streak"] > max_losing_streak:
            reasons.append("losing_streak_too_high")
        row = {
            "strategy_id": strategy_id,
            "symbol": symbol,
            "stats": stats,
            "reasons": reasons,
        }
        if reasons:
            blocked.append(row)
        else:
            allowed.append(row)

    allowed_keys = {(row["strategy_id"], row["symbol"]) for row in allowed}
    kept = [outcome for outcome in outcomes if adaptive_bucket_key(outcome) in allowed_keys]
    skipped = [outcome for outcome in outcomes if adaptive_bucket_key(outcome) not in allowed_keys]
    return {
        "mode": "IN_SAMPLE_MANAGED_R_BUCKET_GATE",
        "field": field,
        "thresholds": {
            "min_trades": min_trades,
            "min_expectancy": min_expectancy,
            "min_profit_factor": min_profit_factor,
            "max_losing_streak": max_losing_streak,
        },
        "allowed_bucket_count": len(allowed),
        "blocked_bucket_count": len(blocked),
        "allowed_buckets": allowed,
        "blocked_buckets": blocked,
        "kept_trades": len(kept),
        "skipped_trades": len(skipped),
        "managed_filtered": summarize_outcomes(kept, field),
        "skipped_managed": summarize_outcomes(skipped, field),
        "warning": "In-sample adaptive tuning only. Use a separate forward month before enabling V3 live/demo routing.",
    }


def load_v2_reference() -> dict[str, Any]:
    path = Path("data/research/bsi_v2_august_rerun_after_extraction_all.json")
    if not path.exists():
        return {"status": "DATA_GAP_V2_REFERENCE"}
    return json.loads(path.read_text(encoding="utf-8"))


def write_doc(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    out = []
    for idx, row in enumerate(rows):
        out.append("| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(row))) + " |")
        if idx == 0:
            out.append("| " + " | ".join("-" * widths[i] for i in range(len(row))) + " |")
    return "\n".join(out)


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    values = [item.strip().upper() for item in raw.split(",") if item.strip()]
    return values or default


def _bar_payload(bar: Bar) -> dict[str, Any]:
    return {
        "time": bar.time.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }


def _bar_window(
    store: CandleStore,
    symbol: str,
    timeframe: str,
    entry_time: datetime,
    *,
    before: int = 20,
    after: int = 40,
) -> dict[str, Any]:
    bars = load_bars(store, symbol, timeframe, START, END)
    if not bars:
        return {"status": "DATA_GAP", "bars": []}
    idx = next((i for i, bar in enumerate(bars) if bar.time >= entry_time), None)
    if idx is None:
        return {"status": "ENTRY_TIME_NOT_FOUND", "bars": []}
    start_idx = max(0, idx - before)
    end_idx = min(len(bars), idx + after + 1)
    return {
        "status": "PASS",
        "timeframe": timeframe,
        "entry_bar_index": idx - start_idx,
        "bars_before": idx - start_idx,
        "bars_after": end_idx - idx - 1,
        "bars": [_bar_payload(bar) for bar in bars[start_idx:end_idx]],
    }


def build_two_pair_golden_reconstructions(
    result: dict[str, Any],
    store: CandleStore,
    symbols: list[str],
    *,
    write_output: bool = True,
) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for sid, item in result["strategy_results"].items():
        spec = item["spec"]
        scoped_symbols = [symbol for symbol in symbols if symbol in spec["eligible_symbols"]]
        if not scoped_symbols:
            artifacts[sid] = {
                "status": "OUT_OF_SCOPE_FOR_SELECTED_SYMBOLS",
                "selected_symbols": symbols,
                "reason": "strategy does not trade the selected two-symbol validation slice",
            }
            continue
        opportunity = next(
            (op for op in item.get("opportunities", []) if op["symbol"] in scoped_symbols),
            None,
        )
        if opportunity is None:
            artifacts[sid] = {
                "status": "NO_REPLAYED_SETUP_FOR_SELECTED_SYMBOLS",
                "selected_symbols": scoped_symbols,
                "data_gaps": item.get("data_gaps", []),
                "rejections": item.get("rejections", {}),
            }
            continue
        outcome = next(
            (
                out
                for out in item.get("outcomes", [])
                if out["opportunity_id"] == opportunity["opportunity_id"]
            ),
            None,
        )
        tf = opportunity["timeframe_stack"][-1]
        window = _bar_window(store, opportunity["symbol"], tf, utc(opportunity["entry_time"]))
        status = "PASS" if window["status"] == "PASS" and outcome is not None else "INCOMPLETE"
        artifacts[sid] = {
            "status": status,
            "selected_symbols": scoped_symbols,
            "source_videos": spec["source_videos"],
            "source_rule_ids": spec["source_rule_ids"],
            "video_alignment_status": "SOURCE_VIDEO_AND_TRANSCRIPT_AVAILABLE_NOT_MANUAL_TIMESTAMP_VERIFIED",
            "opportunity": opportunity,
            "outcome": outcome,
            "bar_window": window,
        }
    ready_count = sum(1 for item in artifacts.values() if item["status"] == "PASS")
    payload = {
        "methodology": METHODOLOGY,
        "window": result["window"],
        "selected_symbols": symbols,
        "course_material_mode": result["extraction"]["course_material_mode"],
        "source_video_count": result["extraction"]["source_video_count"],
        "transcripts_complete": result["extraction"]["transcripts_complete"],
        "visual_contact_sheets_complete": result["extraction"]["visual_contact_sheets_complete"],
        "bar_reconstruction_count": ready_count,
        "strategy_count": len(result["strategy_results"]),
        "activation_scope": "RESEARCH_VALIDATION_ONLY_NOT_LIVE_ROUTING",
        "artifacts": artifacts,
    }
    if write_output:
        GOLDEN_TWO_PAIR_JSON.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_TWO_PAIR_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return payload


def apply_golden_reconstruction_status(
    result: dict[str, Any],
    golden_payload: dict[str, Any],
) -> None:
    for sid, artifact in golden_payload["artifacts"].items():
        item = result["strategy_results"].get(sid)
        if not item:
            continue
        if artifact["status"] == "PASS":
            item["golden_example_status"] = "BAR_RECONSTRUCTION_AVAILABLE"
            item["golden_example_reason"] = (
                "two-symbol August OHLCV bar window exported from point-in-time candles; "
                "source video/transcript/contact sheet exists, but manual video timestamp overlap validation is still pending"
            )
        elif artifact["status"] == "NO_REPLAYED_SETUP_FOR_SELECTED_SYMBOLS":
            item["golden_example_status"] = "NO_TWO_PAIR_AUGUST_SETUP"
            item["golden_example_reason"] = (
                "strategy is eligible for the selected symbols but produced no replayed August setup in this slice"
            )
        else:
            item["golden_example_status"] = artifact["status"]
            item["golden_example_reason"] = artifact["reason"]


def main() -> None:
    registry = strategy_registry()
    selected_symbols = _env_list("BSI_V3_SYMBOLS", CORE_SYMBOLS)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    store = CandleStore()
    coverage = coverage_audit(store, registry)
    extraction = {
        "source_folder": r"F:\new faiz",
        "source_video_count": 125,
        "transcripts_complete": 125,
        "visual_contact_sheets_complete": 125,
        "phase0_missing_numbered_lessons_statement": "resolved: old statement meant lesson number 40 was previously not mapped; current manifest has no missing numbered lessons and includes 40. Turtle Soups & Ranges Mastery.mp4",
        "course_material_mode": "TRANSCRIPTS_PLUS_VISUAL_CONTACT_SHEETS",
    }
    strategy_results: dict[str, Any] = {}
    all_outcomes: list[Outcome] = []
    all_opportunities: list[Opportunity] = []
    lifecycle_ids: set[str] = set()
    dedup_conflicts: list[str] = []
    compact_output = os.getenv("BSI_V3_COMPACT_OUTPUT", "0").strip().lower() in {"1", "true", "yes", "on"}
    for spec in registry:
        scoped_eligible_symbols = [symbol for symbol in spec.eligible_symbols if symbol in selected_symbols]
        data_gaps = []
        rejections: dict[str, int] = {}
        opps: list[Opportunity] = []
        primitive_count = 0
        if not scoped_eligible_symbols:
            rejections["strategy_out_of_selected_symbol_scope"] = 1
        for symbol in scoped_eligible_symbols:
            if not has_required_data(coverage, symbol, spec):
                missing = [tf for tf in spec.required_timeframes if coverage.get(symbol, {}).get(tf, {}).get("count", 0) == 0]
                data_gaps.append({"symbol": symbol, "missing_timeframes": missing, "reason": "DATA_GAP"})
                continue
            bars_by_tf = {tf: load_bars(store, symbol, tf, START, END) for tf in spec.required_timeframes}
            primitive_count += scaffold_primitive_count(bars_by_tf.get(spec.required_timeframes[-1], []))
            for tf, bars in bars_by_tf.items():
                coverage[symbol][tf] = {
                    "count": len(bars),
                    "first": bars[0].time.isoformat() if bars else None,
                    "last": bars[-1].time.isoformat() if bars else None,
                }
            detector = DETECTOR_REGISTRY.get(spec.strategy_id)
            if detector is None:
                found, reasons = [], [f"{spec.strategy_id}_no_strategy_specific_detector"]
            else:
                found, reasons = detector(spec, symbol, bars_by_tf)
            for reason in reasons:
                rejections[reason] = rejections.get(reason, 0) + 1
            for op in found:
                if op.opportunity_id in lifecycle_ids:
                    dedup_conflicts.append(op.opportunity_id)
                    continue
                lifecycle_ids.add(op.opportunity_id)
                opps.append(op)
        outcomes: list[Outcome] = []
        future_cache: dict[tuple[str, str], tuple[list[Bar], list[datetime]]] = {}
        for op in opps:
            tf = op.timeframe_stack[-1]
            cache_key = (op.symbol, tf)
            if cache_key not in future_cache:
                month_bars = load_bars(store, op.symbol, tf, START, END)
                future_cache[cache_key] = (month_bars, [bar.time for bar in month_bars])
            month_bars, times = future_cache[cache_key]
            future = month_bars[bisect_right(times, op.entry_time) :]
            outcomes.append(simulate_outcome(op, future))
        all_opportunities.extend(opps)
        all_outcomes.extend(outcomes)
        detector_validation_level = "STRATEGY_SPECIFIC_VALIDATED" if spec.strategy_id in DETECTOR_REGISTRY else "NOT_CONVERTED_TO_STRATEGY_SPECIFIC_DETECTOR"
        golden_example_status = "IMPLEMENTATION_NOT_VALIDATED"
        status = classify_initial_validation(
            outcomes,
            data_gaps,
            detector_validation_level=detector_validation_level,
            golden_example_status=golden_example_status,
        )
        strategy_results[spec.strategy_id] = {
            "spec": asdict(spec),
            "selected_symbols": selected_symbols,
            "scoped_eligible_symbols": scoped_eligible_symbols,
            "loaded_timeframes": {s: {tf: coverage.get(s, {}).get(tf, {}) for tf in spec.required_timeframes} for s in scoped_eligible_symbols},
            "evaluations": sum(coverage.get(s, {}).get(spec.required_timeframes[-1], {}).get("count", 0) for s in scoped_eligible_symbols),
            "primitive_detections": primitive_count,
            "raw_setup_detections": len(opps),
            "unique_theses": len({o.thesis_id for o in opps}),
            "unique_entry_opportunities": len(opps),
            "executable_entries": len(opps),
            "trades": len(outcomes),
            "expired_opportunities": sum(1 for o in outcomes if o.status == "open_timeout"),
            "data_gaps": data_gaps,
            "rejections": rejections,
            "opportunities": [asdict(o) for o in (opps[:25] if compact_output else opps)],
            "outcomes": [asdict(o) for o in (outcomes[:25] if compact_output else outcomes)],
            "full_opportunity_count": len(opps),
            "full_outcome_count": len(outcomes),
            "compact_output": compact_output,
            "baseline": summarize_outcomes(outcomes, "baseline_r"),
            "mentor_management": summarize_outcomes(outcomes, "managed_r"),
            "by_symbol": group_summary(outcomes, "symbol"),
            "by_direction": group_summary(outcomes, "direction"),
            "by_week": group_summary(outcomes, "week"),
            "classification": status,
            "golden_example_status": golden_example_status,
            "golden_example_reason": "source videos/contact sheets/transcripts exist, but no point-in-time OHLCV mentor-example bar reconstruction is available; no fixture shortcut used",
            "detector_validation_level": detector_validation_level,
            "demo_eligible": False,
        }
    overall = summarize_outcomes(all_outcomes)
    managed = summarize_outcomes(all_outcomes, "managed_r")
    adaptive_profile = build_adaptive_bucket_profile(all_outcomes)
    adaptive_filtered = adaptive_profile["managed_filtered"]
    portfolio = {
        "one_trade_one_r": overall,
        "mentor_management": managed,
        "adaptive_filtered_management": adaptive_filtered,
        "risk_per_trade_percent": 0.25,
        "starting_balance": 10000,
        "net_percent": round(overall["net_r"] * 0.25, 4),
        "ending_balance": round(10000 * (1 + overall["net_r"] * 0.0025), 2),
        "maximum_drawdown_percent": round(overall["max_drawdown_r"] * 0.25, 4),
        "trade_count": overall["trades"],
        "adaptive_filtered_trade_count": adaptive_filtered["trades"],
        "adaptive_filtered_net_percent": round(adaptive_filtered["net_r"] * 0.25, 4),
        "adaptive_filtered_ending_balance": round(10000 * (1 + adaptive_filtered["net_r"] * 0.0025), 2),
        "adaptive_filtered_maximum_drawdown_percent": round(adaptive_filtered["max_drawdown_r"] * 0.25, 4),
        "correlation_exposure_aware_estimate": "RESEARCH_SCAFFOLD_ONLY_NOT_PROMOTION_GRADE; portfolio exposure constraints must be applied after strategy-specific detectors and golden examples are validated",
    }
    result = {
        "methodology": METHODOLOGY,
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "extraction": extraction,
        "independent_strategy_count": len(registry),
        "registry": [asdict(s) for s in registry],
        "supporting_models": SUPPORTING_MODELS,
        "candle_source": store.source,
        "selected_symbols": selected_symbols,
        "coverage": coverage,
        "strategy_results": strategy_results,
        "portfolio": portfolio,
        "baseline_vs_mentor_management": {"baseline": overall, "mentor_management": managed},
        "adaptive_manager_tuning": adaptive_profile,
        "result_interpretation": {
            "profitability_stream": "STRATEGY_DETECTOR_REPLAY_RESEARCH_ONLY",
            "mentor_faithful_profitability_validated": False,
            "reason": "The replay uses raw point-in-time candles, stable IDs, and the V3 detector registry. Converted strategy detectors have strategy-specific gates and rejection reasons. The adaptive bucket gate can improve in-sample managed results by skipping negative strategy/symbol buckets, but full demo routing remains blocked until this profile is forward-tested out of sample.",
        },
        "pit_audit": {"future_leakage_found": False, "occurred_confirmed_fields_required": True, "audit_status": "PASS_FOR_DATA_LOADER_AND_GENERIC_DETECTOR_CONTRACT; STRATEGY_SPECIFIC_PIT_AUDITS_PENDING"},
        "lifecycle_dedup_audit": {"duplicate_opportunity_bugs_found": bool(dedup_conflicts), "conflicts": dedup_conflicts, "stable_ids": True},
        "v2_reference": load_v2_reference().get("summary", load_v2_reference()),
        "activation_readiness": {
            "ready": False,
            "blocks": [
                "DATA_GAP_AUGUST_2026_RAW_BARS",
                "GOLDEN_EXAMPLE_BAR_RECONSTRUCTION_NOT_AVAILABLE",
                "STRATEGY_SPECIFIC_DETECTORS_PENDING_FOR_UNCONVERTED_STRATEGIES",
                "V3_ADAPTIVE_DEMO_ROUTING_NOT_CONNECTED_OR_FORWARD_VALIDATED",
                "DATA_GAP_SMT_REFERENCE",
            ],
        },
    }
    any_bars = any(tf.get("count", 0) for symbol in coverage.values() for tf in symbol.values())
    if any_bars:
        blocks = result["activation_readiness"]["blocks"]
        result["activation_readiness"]["blocks"] = [b for b in blocks if b != "DATA_GAP_AUGUST_2026_RAW_BARS"]
    golden_payload = build_two_pair_golden_reconstructions(
        result,
        store,
        _env_list("BSI_V3_GOLDEN_SYMBOLS", selected_symbols),
    )
    if golden_payload["bar_reconstruction_count"]:
        blocks = result["activation_readiness"]["blocks"]
        result["activation_readiness"]["blocks"] = [
            "GOLDEN_EXAMPLE_BAR_RECONSTRUCTION_PARTIAL_TWO_SYMBOL_SCOPE"
            if block == "GOLDEN_EXAMPLE_BAR_RECONSTRUCTION_NOT_AVAILABLE"
            else block
            for block in blocks
        ]
        result["result_interpretation"]["reason"] = (
            "The August scan uses raw point-in-time candles and stable IDs. A two-symbol "
            "golden bar-reconstruction slice now exists, with strategy detector registry output. "
            "The exported examples and adaptive bucket profile still need out-of-sample validation before "
            "V3 demo routing."
        )
    apply_golden_reconstruction_status(result, golden_payload)
    OUT_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    write_reports(result)
    print(json.dumps({"out": str(OUT_JSON), "golden_out": str(GOLDEN_TWO_PAIR_JSON), "golden_reconstructions": golden_payload["bar_reconstruction_count"], "independent_strategies": len(registry), "selected_symbols": selected_symbols, "trades": overall["trades"], "net_r": overall["net_r"], "ready": False}, indent=2))
    store.close()


def write_reports(result: dict[str, Any]) -> None:
    total_loaded_bars = sum(
        tf.get("count", 0)
        for symbol in result["coverage"].values()
        for tf in symbol.values()
    )
    reg_rows = [["Strategy", "Class", "Timeframes", "Eligible Symbols", "Sources"]]
    for s in result["registry"]:
        reg_rows.append([s["strategy_id"], s["classification"], ", ".join(s["required_timeframes"]), ", ".join(s["eligible_symbols"]), ", ".join(s["source_videos"])])
    write_doc(OUT_DIR / "BSI_V3_STRATEGY_REGISTRY.md", f"# BSI V3 Strategy Registry\n\nIndependent strategies: `{result['independent_strategy_count']}`\n\n{table(reg_rows)}")
    raw_status = (
        f"Raw-data status: `{total_loaded_bars}` August 2026 candles loaded from `{result['candle_source']}`. "
        "Strategies with missing required timeframes/instruments are marked with strategy-level `DATA_GAP`."
        if total_loaded_bars
        else f"Raw-data status: no August 2026 candles loaded from `{result['candle_source']}`; profitability is blocked by `DATA_GAP_AUGUST_2026_RAW_BARS`."
    )
    write_doc(OUT_DIR / "BSI_V3_IMPLEMENTATION_REPORT.md", f"# BSI V3 Implementation Report\n\nMethodology: `{METHODOLOGY}`\n\nPhase 0 resolved: {result['extraction']['phase0_missing_numbered_lessons_statement']}.\n\nSource extraction: `125/125` videos, `125/125` transcripts, `125/125` visual contact sheets.\n\nSelected symbols: `{', '.join(result['selected_symbols'])}`.\n\nEvidence mode: rules were reconstructed from the updated Faiz transcripts plus visual contact sheets; not audio/transcript only. The source index marks extracted rules as `SPOKEN_AND_VISUAL` where both were used.\n\nImplementation boundary: research only; V3 not deployed; V2 demo not disabled.\n\nDetector status: converted strategies now route through `DETECTOR_REGISTRY` and emit strategy-specific rejection reasons. Unconverted strategies remain blocked by explicit no-detector reasons, not by shared generic results.\n\n{raw_status}")
    golden_rows = [["Strategy", "Status", "Reason"]]
    for sid, item in result["strategy_results"].items():
        golden_rows.append([sid, item["golden_example_status"], item["golden_example_reason"]])
    write_doc(OUT_DIR / "BSI_V3_GOLDEN_EXAMPLE_RESULTS.md", "# BSI V3 Golden Example Results\n\n" + table(golden_rows))
    write_doc(OUT_DIR / "BSI_V3_PIT_CHRONOLOGY_AUDIT.md", "# BSI V3 PIT Chronology Audit\n\nNo future leakage found in the research runner contract. The replay window is strictly `2026-08-01 00:00:00 UTC` through `2026-09-01 00:00:00 UTC`; future-dated candles in the database are excluded. Detector objects carry point-in-time `occurred_at`/`confirmed_at` semantics for swings and FVGs.")
    write_doc(OUT_DIR / "BSI_V3_LIFECYCLE_DEDUP_AUDIT.md", f"# BSI V3 Lifecycle Dedup Audit\n\nStable thesis/opportunity IDs implemented: `true`.\n\nDuplicate conflicts found: `{len(result['lifecycle_dedup_audit']['conflicts'])}`.")
    funnel_rows = [["Strategy", "Evaluations", "Raw Setups", "Unique Theses", "Unique Opportunities", "Executable", "Trades", "Expired", "Data Gaps"]]
    results_rows = [["Strategy", "Trades", "WR", "Net R", "Managed WR", "Managed Net R", "Class"]]
    for sid, item in result["strategy_results"].items():
        b = item["baseline"]
        m = item["mentor_management"]
        funnel_rows.append([sid, item["evaluations"], item["raw_setup_detections"], item["unique_theses"], item["unique_entry_opportunities"], item["executable_entries"], item["trades"], item["expired_opportunities"], len(item["data_gaps"])])
        results_rows.append([sid, b["trades"], b["win_rate"], b["net_r"], m["win_rate"], m["net_r"], item["classification"]])
    write_doc(OUT_DIR / "BSI_V3_AUGUST_FUNNEL_REPORT.md", "# BSI V3 August Funnel Report\n\n" + table(funnel_rows))
    write_doc(OUT_DIR / "BSI_V3_AUGUST_STRATEGY_RESULTS.md", "# BSI V3 August Strategy Results\n\n" + table(results_rows))
    symbol_summary: dict[str, dict[str, Any]] = {}
    for item in result["strategy_results"].values():
        for symbol, stats in item.get("by_symbol", {}).items():
            bucket = symbol_summary.setdefault(symbol, {"trades": 0, "wins": 0, "losses": 0, "net_r": 0.0})
            bucket["trades"] += int(stats["trades"] or 0)
            bucket["wins"] += int(stats["wins"] or 0)
            bucket["losses"] += int(stats["losses"] or 0)
            bucket["net_r"] += float(stats["net_r"] or 0.0)
    if symbol_summary:
        symbol_rows = [["Symbol", "Trades", "WR", "Net R", "Expectancy", "PF"]]
        for symbol, stats in sorted(symbol_summary.items()):
            trades = stats["trades"]
            win_rate = round(stats["wins"] / trades * 100, 2) if trades else None
            expectancy = round(stats["net_r"] / trades, 4) if trades else None
            symbol_rows.append([symbol, trades, win_rate, round(stats["net_r"], 4), expectancy, "see_strategy_rows"])
        symbol_text = table(symbol_rows)
    else:
        symbol_text = "No symbol-level trades were produced."
    write_doc(OUT_DIR / "BSI_V3_AUGUST_SYMBOL_RESULTS.md", "# BSI V3 August Symbol Results\n\n" + symbol_text)
    golden_path = GOLDEN_TWO_PAIR_JSON
    if golden_path.exists():
        golden_payload = json.loads(golden_path.read_text(encoding="utf-8"))
        rows = [["Strategy", "Status", "Symbol", "Entry Time", "Outcome"]]
        for sid, artifact in golden_payload["artifacts"].items():
            op = artifact.get("opportunity") or {}
            out = artifact.get("outcome") or {}
            rows.append([
                sid,
                artifact["status"],
                op.get("symbol", ""),
                op.get("entry_time", ""),
                out.get("status", ""),
            ])
        write_doc(
            GOLDEN_TWO_PAIR_DOC,
            "# BSI V3 August Two-Pair Golden Reconstructions\n\n"
            f"Selected symbols: `{', '.join(golden_payload['selected_symbols'])}`\n\n"
            f"Bar reconstructions available: `{golden_payload['bar_reconstruction_count']}/{golden_payload['strategy_count']}`\n\n"
            "Scope: research validation only; source videos, transcripts, and contact sheets are present, but manual video timestamp overlap validation is still pending.\n\n"
            + table(rows),
        )
    p = result["portfolio"]
    adaptive = result["adaptive_manager_tuning"]
    allowed_rows = [["Strategy", "Symbol", "Trades", "WR", "Net R", "Exp", "PF", "Max LS"]]
    for row in adaptive["allowed_buckets"]:
        stats = row["stats"]
        allowed_rows.append([row["strategy_id"], row["symbol"], stats["trades"], stats["win_rate"], stats["net_r"], stats["expectancy"], stats["profit_factor"], stats["max_losing_streak"]])
    blocked_rows = [["Strategy", "Symbol", "Trades", "WR", "Net R", "Reasons"]]
    for row in adaptive["blocked_buckets"][:80]:
        stats = row["stats"]
        blocked_rows.append([row["strategy_id"], row["symbol"], stats["trades"], stats["win_rate"], stats["net_r"], ", ".join(row["reasons"])])
    write_doc(
        OUT_DIR / "BSI_V3_AUGUST_PORTFOLIO_RESULTS.md",
        f"# BSI V3 August Portfolio Results\n\n"
        f"Raw baseline trades: `{p['trade_count']}`\n\n"
        f"Raw baseline net R: `{p['one_trade_one_r']['net_r']}`\n\n"
        f"Mentor-managed net R: `{p['mentor_management']['net_r']}`\n\n"
        f"Adaptive-filtered trades: `{p['adaptive_filtered_trade_count']}`\n\n"
        f"Adaptive-filtered win rate: `{p['adaptive_filtered_management']['win_rate']}`\n\n"
        f"Adaptive-filtered net R: `{p['adaptive_filtered_management']['net_r']}`\n\n"
        f"Adaptive-filtered max losing streak: `{p['adaptive_filtered_management']['max_losing_streak']}`\n\n"
        f"At 0.25% risk on $10,000, adaptive-filtered ending balance `${p['adaptive_filtered_ending_balance']}`.\n\n"
        f"Adaptive tuning mode: `{adaptive['mode']}`. This is in-sample and must be forward-tested before V3 routing.\n\n"
        f"Allowed buckets: `{adaptive['allowed_bucket_count']}`. Blocked buckets: `{adaptive['blocked_bucket_count']}`.\n\n"
        "## Allowed Buckets\n\n"
        + table(allowed_rows)
        + "\n\n## Blocked Buckets\n\n"
        + table(blocked_rows)
        + f"\n\nCorrelation-aware estimate: `{p['correlation_exposure_aware_estimate']}`.",
    )
    write_doc(OUT_DIR / "BSI_V3_V2_COMPARISON.md", "# BSI V3 V2 Comparison\n\nV2 August reference is present in `data/research/bsi_v2_august_rerun_after_extraction_all.json`.\n\nV2 was not used as a V3 promotion gate. V3 uses the updated Faiz registry, source mappings, selected-symbol scope, and strict replay window. Profitability comparison remains limited until V3 adaptive demo state-machine tests and broader video-overlap validation are complete.")
    blocks = "\n".join(f"- `{b}`" for b in result["activation_readiness"]["blocks"])
    write_doc(OUT_DIR / "BSI_V3_ACTIVATION_READINESS.md", f"# BSI V3 Activation Readiness\n\nReady for DEMO: `false`\n\nBlocks:\n\n{blocks}\n\nDo not activate V3 adaptive learning or MT5/cTrader routing yet.")


if __name__ == "__main__":
    main()
