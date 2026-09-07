from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from backend.market_structure.models import ConceptStatus, Direction, ImbalanceZone, LiquidityLevel, LiquiditySide, StructureBreak, StructureBreakKind, SwingPoint
from backend.market_structure.pivot_trendlines import TrendlinePivotSummary
from backend.mt5_strategies.families import EVALUATORS
from backend.mt5_strategies.families.bsi_v2_engine import BSI_V2_RESEARCH_EVALUATORS, evaluate_bsi_v2_research, evaluate_bsi_v2_subtype
from backend.mt5_strategies.families.bsi_v2_lifecycle import BSILifecycleStore
from backend.mt5_strategies.families.bsi_v2_scaffold import BSI_BASELINE_V2_AUDIOVISUAL

NOW = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class _Ctx:
    symbol: str
    broker_symbol: str
    generated_at: datetime
    regime: str
    m15_snapshot: SimpleNamespace
    m15_rows: list[dict]
    atr_m15: Decimal
    bid: Decimal
    ask: Decimal
    spread: Decimal
    broker_min_stop_distance: Decimal | None = None
    trendline_pivots: tuple = ()


def _row(i: int, o: str, h: str, l: str, c: str) -> dict:
    return {"time": (NOW + timedelta(minutes=15 * i)).isoformat(), "open": o, "high": h, "low": l, "close": c}


def _bars() -> list[dict]:
    return [
        _row(0, "1.1000", "1.1010", "1.0990", "1.1005"),
        _row(1, "1.1005", "1.1015", "1.0995", "1.1010"),
        _row(2, "1.1010", "1.1055", "1.1008", "1.1050"),
        _row(3, "1.1060", "1.1080", "1.1045", "1.1070"),
        _row(4, "1.1060", "1.1070", "1.1030", "1.1040"),
    ]


def _level(level_id: str, side: str, level: str, source: str = "swing") -> LiquidityLevel:
    return LiquidityLevel(
        id=level_id, symbol="EURUSD", timeframe="M15", start_time=NOW, end_time=NOW,
        detected_time=NOW, confirmation_time=NOW, price_low=Decimal(level), price_high=Decimal(level),
        direction=Direction.UNKNOWN, configuration_version="1.0", configuration_hash="hash",
        side=side, level=Decimal(level), source=source, tolerance=Decimal("0.0001"),
    )


def _break(direction: str = "bullish", kind: str = "mss", idx: int = 3) -> StructureBreak:
    t = NOW + timedelta(minutes=15 * idx)
    return StructureBreak(
        id=f"brk_{direction}_{kind}_{idx}", symbol="EURUSD", timeframe="M15", start_time=t, end_time=t,
        detected_time=t, confirmation_time=t, price_low=Decimal("1.1000"), price_high=Decimal("1.1100"),
        direction=direction, configuration_version="1.0", configuration_hash="hash",
        break_kind=kind, broken_swing_id="sw_1", broken_level=Decimal("1.1050"),
        break_price=Decimal("1.1075"), break_distance=Decimal("0.0025"),
        confirmation_mode="close", bar_index=idx, explanation="test",
    )


def _fvg(direction: str = "bullish", low: str = "1.1040", high: str = "1.1060", strength: float = 0.2) -> ImbalanceZone:
    return ImbalanceZone(
        id=f"fvg_{direction}_{low}_{high}", symbol="EURUSD", timeframe="M15", start_time=NOW,
        end_time=NOW + timedelta(minutes=45), detected_time=NOW + timedelta(minutes=45),
        confirmation_time=NOW + timedelta(minutes=45), price_low=Decimal(low), price_high=Decimal(high),
        direction=direction, status=ConceptStatus.ACTIVE, strength=strength,
        configuration_version="1.0", configuration_hash="hash", supporting_bar_indexes=[1, 2, 3],
        midpoint=(Decimal(low) + Decimal(high)) / 2, consequent_encroachment=(Decimal(low) + Decimal(high)) / 2,
    )


def _swing(kind: str, idx: int, price: str) -> SwingPoint:
    t = NOW + timedelta(minutes=15 * idx)
    return SwingPoint(
        id=f"sw_{kind}_{idx}", symbol="EURUSD", timeframe="M15", start_time=t, end_time=t,
        detected_time=t, confirmation_time=t, price_low=Decimal(price), price_high=Decimal(price),
        direction=Direction.BULLISH if kind == "high" else Direction.BEARISH,
        configuration_version="1.0", configuration_hash="hash", swing_type=kind, bar_index=idx,
        price=Decimal(price), candidate_time=t,
    )


def _base_ctx(*, fixture: dict | None = None, imbalances=None, breaks=None, levels=None, bid="1.1050", ask="1.1050", trendlines=()) -> _Ctx:
    snap = SimpleNamespace(
        breaks=list(breaks or []),
        imbalances=list(imbalances or []),
        liquidity_levels=list(levels or [_level("target_high", LiquiditySide.BUY_SIDE.value, "1.1150"), _level("target_low", LiquiditySide.SELL_SIDE.value, "1.0950")]),
        liquidity_sweeps=[],
        session_levels=[],
        swings=[],
        bsi_v2_fixture=fixture or {},
    )
    return _Ctx("EURUSD", "EURUSD", NOW + timedelta(minutes=75), "NEUTRAL", snap, _bars(), Decimal("0.0010"), Decimal(bid), Decimal(ask), Decimal("0.0000"), trendline_pivots=trendlines)


def test_order_flow_golden_consumes_trendline_liquidity_and_mentor_ob_without_ote():
    sw1, sw2 = _swing("high", 1, "1.1000"), _swing("high", 3, "1.1040")
    line = TrendlinePivotSummary("tl1", "EURUSD", "M15", "high", 0.002, 1.098, 1, 3, 30, 3, True, 4)
    ctx = _base_ctx(imbalances=[_fvg("bullish", "1.1020", "1.1040", 0.2)], breaks=[_break("bullish", "mss")], bid="1.1030", ask="1.1030", trendlines=(line,))
    ctx.m15_snapshot.swings = [sw1, sw2]

    signal = evaluate_bsi_v2_subtype(ctx, "bsi_order_flow")

    assert signal.valid is True
    assert signal.evidence["mentor_structure"] == "mss"
    assert signal.evidence["trendline_liquidity_id"]
    assert signal.evidence["mentor_ob_id"]
    assert signal.evidence["premium_discount"] == "discount"
    assert "ote" not in str(signal.evidence).lower()


def test_order_flow_new_msb_new_array_gets_new_opportunity_without_whole_trend_dedup():
    store = BSILifecycleStore()
    first = evaluate_bsi_v2_subtype(_base_ctx(imbalances=[_fvg("bullish", "1.1040", "1.1060")], breaks=[_break("bullish", "bos", 3)]), "bsi_order_flow", lifecycle=store)
    second = evaluate_bsi_v2_subtype(_base_ctx(imbalances=[_fvg("bullish", "1.1070", "1.1090")], breaks=[_break("bullish", "bos", 4)], bid="1.1080", ask="1.1080"), "bsi_order_flow", lifecycle=store)

    assert first.valid is True
    assert second.valid is True
    assert first.evidence["bsi_entry_opportunity_id"] != second.evidence["bsi_entry_opportunity_id"]
    assert "ORDER_FLOW_MSB_PD_AMBIGUOUS" in first.evidence["ambiguity_flags"]


def test_abc_golden_requires_abc_geometry_and_targets_b_leg():
    fixture = {"abc": {"structure_direction": "bullish", "b_leg_target": "1.1150", "stop": "1.1020", "points": ["A", "B", "C"]}}
    signal = evaluate_bsi_v2_subtype(_base_ctx(fixture=fixture, imbalances=[_fvg("bullish")]), "bsi_abc")

    assert signal.valid is True
    assert signal.evidence["abc_points"] == ["A", "B", "C"]
    assert signal.evidence["target"] == 1.115
    assert signal.evidence["premium_discount"] == "NOT_USED"


def test_abc_negative_b_leg_invalidates_a():
    fixture = {"abc": {"structure_direction": "bullish", "b_leg_target": "1.1150", "stop": "1.1020", "b_exceeds_a_start": True}}
    signal = evaluate_bsi_v2_subtype(_base_ctx(fixture=fixture, imbalances=[_fvg("bullish")]), "bsi_abc")

    assert signal.valid is False
    assert signal.rejection_reason == "B_LEG_INVALIDATES_A"


def test_asian_golden_uses_session_box_target_and_no_daily_bias():
    fixture = {"asian": {"structure_direction": "bullish", "target_level_name": "high", "stop": "1.1010"}}
    ctx = _base_ctx(fixture=fixture, imbalances=[_fvg("bullish")])
    ctx.m15_snapshot.session_levels = [SimpleNamespace(id="asian_high", symbol="EURUSD", timeframe="M15", start_time=NOW, end_time=NOW, detected_time=NOW, confirmation_time=NOW, session_name="asian", level_name="high", level=Decimal("1.1150"))]

    signal = evaluate_bsi_v2_subtype(ctx, "bsi_asian")

    assert signal.valid is True
    assert signal.evidence["target_type"] == "asian_opposite_edge"
    assert signal.evidence["daily_bias_required"] is False
    assert signal.evidence["premium_discount"] == "NOT_USED"


def test_under_over_golden_requires_three_touches_close_reclaim_and_partials_metadata():
    fixture = {"under_over": {"direction": "SHORT", "level": "1.1050", "target": "1.0950", "stop": "1.1070", "touch_count": 3}}
    signal = evaluate_bsi_v2_subtype(_base_ctx(fixture=fixture, bid="1.1050", ask="1.1050"), "bsi_under_over")

    assert signal.valid is True
    assert signal.evidence["touch_count"] == 3
    assert signal.evidence["management_style"] == "partial_at_intermediate_fvgs_full_at_target"
    assert signal.evidence["premium_discount"] == "NOT_USED"


def test_under_over_wick_only_break_rejected():
    fixture = {"under_over": {"direction": "SHORT", "level": "1.1050", "target": "1.0950", "stop": "1.1070", "touch_count": 3, "wick_only_break": True}}
    signal = evaluate_bsi_v2_subtype(_base_ctx(fixture=fixture), "bsi_under_over")

    assert signal.valid is False
    assert signal.rejection_reason == "WICK_BREAK_DOES_NOT_COUNT"


def test_0930_golden_uses_index_window_displacement_extreme_fvg_and_3r_min():
    fixture = {"0930": {"structure_direction": "bearish", "in_930_window": True, "is_index": True, "displacement_mss": True, "target": "1.0980", "stop": "1.1070", "execution_timeframe": "1m"}}
    signal = evaluate_bsi_v2_subtype(_base_ctx(fixture=fixture, imbalances=[_fvg("bearish", "1.1040", "1.1060")], bid="1.1050", ask="1.1050"), "bsi_0930")

    assert signal.valid is True
    assert signal.evidence["target_type"] == "bounded_3_5"
    assert signal.evidence["instrument_scope"] == "index"
    assert signal.reward_risk >= 3.0


def test_0930_rejects_non_index_scope():
    fixture = {"0930": {"structure_direction": "bearish", "in_930_window": True, "is_index": False, "displacement_mss": True, "target": "1.0980", "stop": "1.1070"}}
    signal = evaluate_bsi_v2_subtype(_base_ctx(fixture=fixture), "bsi_0930")

    assert signal.valid is False
    assert signal.rejection_reason == "INSTRUMENT_NOT_INDEX"


def test_reactionary_golden_uses_second_array_not_array_one():
    fixture = {"reactionary": {"direction": "SHORT", "array1_id": "array1", "array2_id": "array2", "array2_low": "1.1040", "array2_high": "1.1060", "stop": "1.1070", "target": "1.0950", "reaction_event": "push_away"}}
    signal = evaluate_bsi_v2_subtype(_base_ctx(fixture=fixture, bid="1.1050", ask="1.1050"), "bsi_reactionary")

    assert signal.valid is True
    assert signal.evidence["array1_id"] == "array1"
    assert signal.evidence["array2_id"] == signal.evidence["entry_array_id"] == "array2"
    assert "REACTIONARY_ARRAY2_KIND_AMBIGUOUS" in signal.evidence["ambiguity_flags"]


def test_abcd_golden_uses_p2_d_leg_retest_and_fixed_1_to_2():
    fixture = {"abcd": {"d_direction": "LONG", "p2_level": "1.1050", "stop": "1.1030", "points": ["A", "B", "C", "D"]}}
    signal = evaluate_bsi_v2_subtype(_base_ctx(fixture=fixture, bid="1.1050", ask="1.1050"), "bsi_abcd")

    assert signal.valid is True
    assert signal.evidence["d_break_level"] == 1.105
    assert signal.reward_risk == 2.0
    assert "ABCD_CLOSE_BASED_D_BREAK_AMBIGUOUS" in signal.evidence["ambiguity_flags"]


def test_ob_liquidity_golden_uses_same_array_fakeout_reclaim():
    fixture = {"ob_liquidity": {"direction": "LONG", "origin_ob_id": "origin_ob", "ob_low": "1.1040", "ob_high": "1.1060", "stop": "1.1030", "target": "1.1150"}}
    signal = evaluate_bsi_v2_subtype(_base_ctx(fixture=fixture, bid="1.1050", ask="1.1050"), "bsi_ob_liquidity")

    assert signal.valid is True
    assert signal.evidence["origin_ob_id"] == signal.evidence["entry_array_id"]
    assert signal.evidence["same_array_fakeout_reclaim"] is True


def test_ob_liquidity_rejects_heavy_reaction_and_uncleared_residual_liquidity():
    heavy = {"ob_liquidity": {"direction": "LONG", "origin_ob_id": "origin_ob", "ob_low": "1.1040", "ob_high": "1.1060", "stop": "1.1030", "target": "1.1150", "heavy_clean_reaction": True}}
    residual = {"ob_liquidity": {"direction": "LONG", "origin_ob_id": "origin_ob", "ob_low": "1.1040", "ob_high": "1.1060", "stop": "1.1030", "target": "1.1150", "residual_liquidity_uncleared": True}}

    assert evaluate_bsi_v2_subtype(_base_ctx(fixture=heavy), "bsi_ob_liquidity").rejection_reason == "VALID_OB_NOT_LIQUIDITY_OB"
    assert evaluate_bsi_v2_subtype(_base_ctx(fixture=residual), "bsi_ob_liquidity").rejection_reason == "RESIDUAL_LIQUIDITY_UNCLEARED"


def test_v2_research_dispatcher_has_exact_nine_and_is_not_legacy_dispatch():
    expected = {"bsi_order_flow", "bsi_asian", "bsi_new_york", "bsi_abc", "bsi_under_over", "bsi_0930", "bsi_reactionary", "bsi_abcd", "bsi_ob_liquidity"}

    assert set(BSI_V2_RESEARCH_EVALUATORS) == expected
    assert "bsi_v2" not in EVALUATORS
    assert not any(name in BSI_V2_RESEARCH_EVALUATORS for name in ("mtfai1", "ema_trend", "breakout", "smc_continuation"))


def test_no_bsi_v2_setup_means_no_bsi_v2_trade():
    signal = evaluate_bsi_v2_research(_base_ctx(), subtype_order=("bsi_abc", "bsi_asian", "bsi_under_over"))

    assert signal.valid is False
    assert signal.rejection_reason == "NO_BSI_V2_SETUP"
    assert signal.evidence["bsi_version"] == BSI_BASELINE_V2_AUDIOVISUAL


def test_raw_abc_handles_short_real_swing_lists_without_error():
    ctx = _base_ctx()
    ctx.m15_snapshot.swings = [_swing("high", 1, "1.1050"), _swing("low", 2, "1.1000"), _swing("high", 3, "1.1030")]

    signal = evaluate_bsi_v2_subtype(ctx, "bsi_abc")

    assert signal.valid is False
    assert signal.rejection_reason == "NO_ABC_GEOMETRY"
