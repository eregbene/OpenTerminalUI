from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from backend.market_structure.bar_utils import StructureBar
from backend.market_structure.models import (
    ConceptStatus,
    Direction,
    ImbalanceZone,
    LiquidityLevel,
    LiquiditySide,
    SessionLevel,
    StructureBreak,
    StructureBreakKind,
    SwingPoint,
)
from backend.market_structure.pivot_trendlines import TrendlinePivotSummary
from backend.mt5_strategies.families import EVALUATORS
from backend.mt5_strategies.families.bsi_engine import BSI_VERSION as BSI_V1_ENGINE_VERSION
from backend.mt5_strategies.families.bsi_v2_interpretation import (
    build_dealing_leg,
    build_liquidity_taken_event,
    build_mentor_fvg_relation,
    build_mentor_order_block_from_fvg,
    interpret_mentor_structure,
    liquidity_from_level,
    liquidity_from_session_level,
    liquidity_from_trendline,
)
from backend.mt5_strategies.families.bsi_v2_primitives import (
    BSIEntryArray,
    BSIIdentitySeed,
    BSILiquidityType,
    BSIMentorStructureKind,
    BSIPremiumDiscount,
    BSIV2Evidence,
    build_bsi_entry_opportunity_id,
    build_bsi_thesis_id,
)
from backend.mt5_strategies.families.bsi_v2_scaffold import (
    BSI_BASELINE_V1,
    BSI_BASELINE_V2_AUDIOVISUAL,
    bsi_v2_enabled,
)
from backend.mt5_strategies.models import DISABLED, activation_status

NOW = datetime(2026, 1, 5, 10, 0, tzinfo=timezone.utc)


def _bar(i: int, o: str, h: str, l: str, c: str) -> StructureBar:
    return StructureBar(
        index=i,
        symbol="EURUSD",
        timeframe="M15",
        open_time=NOW + timedelta(minutes=15 * i),
        close_time=NOW + timedelta(minutes=15 * (i + 1)),
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(l),
        close=Decimal(c),
    )


def _break(kind: StructureBreakKind = StructureBreakKind.CHOCH) -> StructureBreak:
    t = NOW + timedelta(minutes=45)
    return StructureBreak(
        id=f"brk_{kind.value}",
        symbol="EURUSD",
        timeframe="M15",
        start_time=t,
        end_time=t,
        detected_time=t,
        confirmation_time=t,
        price_low=Decimal("1.0900"),
        price_high=Decimal("1.1100"),
        direction=Direction.BULLISH,
        configuration_version="1.0",
        configuration_hash="hash",
        break_kind=kind,
        broken_swing_id="swing_high_1",
        broken_level=Decimal("1.1050"),
        break_price=Decimal("1.1075"),
        break_distance=Decimal("0.0025"),
        confirmation_mode="close",
        bar_index=3,
        explanation="test",
    )


def _fvg() -> ImbalanceZone:
    return ImbalanceZone(
        id="fvg_video",
        symbol="EURUSD",
        timeframe="M15",
        start_time=NOW,
        end_time=NOW + timedelta(minutes=45),
        detected_time=NOW + timedelta(minutes=45),
        confirmation_time=NOW + timedelta(minutes=45),
        price_low=Decimal("1.1020"),
        price_high=Decimal("1.1040"),
        direction=Direction.BULLISH,
        status=ConceptStatus.ACTIVE,
        configuration_version="1.0",
        configuration_hash="hash",
        supporting_bar_indexes=[1, 2, 3],
        midpoint=Decimal("1.1030"),
        consequent_encroachment=Decimal("1.1030"),
    )


def _swing(swing_type: str, idx: int, price: str) -> SwingPoint:
    t = NOW + timedelta(minutes=15 * idx)
    return SwingPoint(
        id=f"sw_{swing_type}_{idx}",
        symbol="EURUSD",
        timeframe="M15",
        start_time=t,
        end_time=t,
        detected_time=t,
        confirmation_time=t,
        price_low=Decimal(price),
        price_high=Decimal(price),
        direction=Direction.BULLISH if swing_type == "high" else Direction.BEARISH,
        configuration_version="1.0",
        configuration_hash="hash",
        swing_type=swing_type,
        bar_index=idx,
        price=Decimal(price),
        candidate_time=t,
    )


def test_bsi_v2_version_exists_and_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("BSI_BASELINE_V2_AUDIOVISUAL_ENABLED", raising=False)
    monkeypatch.delenv("MT5_STRATEGY_ACTIVATION_BSI", raising=False)

    assert BSI_V1_ENGINE_VERSION == BSI_BASELINE_V1
    assert BSI_BASELINE_V2_AUDIOVISUAL == "BSI_BASELINE_V2_AUDIOVISUAL"
    assert bsi_v2_enabled() is False
    assert "bsi_v2" not in EVALUATORS
    assert activation_status("bsi") == DISABLED


def test_mentor_structure_maps_continuation_to_msb_and_reversal_to_mss_without_choch_state():
    continuation = interpret_mentor_structure(_break(StructureBreakKind.BOS), direction_before="bullish", direction_after="bullish")
    reversal = interpret_mentor_structure(_break(StructureBreakKind.CHOCH), direction_before="bearish", direction_after="bullish")

    assert continuation.mentor_classification == BSIMentorStructureKind.MSB
    assert reversal.mentor_classification == BSIMentorStructureKind.MSS
    assert reversal.mentor_classification.value != "choch"
    assert reversal.source_break_id == "brk_choch"
    assert reversal.relevant_swing_id == "swing_high_1"
    assert reversal.break_level == Decimal("1.1050")


def test_fvg_relation_preserves_three_candle_relationship():
    relation = build_mentor_fvg_relation(_fvg())

    assert relation.fvg_id == "fvg_video"
    assert relation.candle_1_index == 1
    assert relation.impulse_candle_index == 2
    assert relation.candle_3_index == 3
    assert relation.low == Decimal("1.1020")
    assert relation.high == Decimal("1.1040")


def test_mentor_ob_is_fvg_anchored_first_candle_not_generic_last_opposite_candidate():
    bars = [
        _bar(0, "1.1000", "1.1010", "1.0990", "1.1005"),
        _bar(1, "1.1005", "1.1015", "1.0995", "1.1010"),
        _bar(2, "1.1010", "1.1055", "1.1008", "1.1050"),
        _bar(3, "1.1060", "1.1080", "1.1045", "1.1070"),
    ]

    mentor_ob = build_mentor_order_block_from_fvg(_fvg(), bars, rejected_competing_ob_ids=("generic_last_opposite_0",))

    assert mentor_ob.selected_ob_candle_index == 1
    assert mentor_ob.low == Decimal("1.0995")
    assert mentor_ob.high == Decimal("1.1015")
    assert mentor_ob.reason_selected == "FVG_ANCHORED_FIRST_CANDLE"
    assert "generic_last_opposite_0" in mentor_ob.rejected_competing_ob_ids


def test_horizontal_session_and_trendline_liquidity_objects_are_distinct():
    level = LiquidityLevel(
        id="liq_swing",
        symbol="EURUSD",
        timeframe="M15",
        start_time=NOW,
        end_time=NOW,
        detected_time=NOW,
        confirmation_time=NOW,
        price_low=Decimal("1.1000"),
        price_high=Decimal("1.1000"),
        direction=Direction.UNKNOWN,
        configuration_version="1.0",
        configuration_hash="hash",
        side=LiquiditySide.BUY_SIDE,
        level=Decimal("1.1000"),
        source="swing",
        tolerance=Decimal("0.0002"),
    )
    session = SessionLevel(
        id="asian_high",
        symbol="EURUSD",
        timeframe="M15",
        start_time=NOW,
        end_time=NOW,
        detected_time=NOW,
        confirmation_time=NOW,
        configuration_version="1.0",
        configuration_hash="hash",
        session_name="asian",
        level_name="high",
        level=Decimal("1.1100"),
    )

    swing_liq = liquidity_from_level(level, liquidity_type=BSILiquidityType.SWING_LEVEL, source_rule_ids=("BSI2-LIQ-001",))
    session_liq = liquidity_from_session_level(session, side="buy_side", source_rule_ids=("BSI2-LIQ-004",))

    assert swing_liq.liquidity_type == BSILiquidityType.SWING_LEVEL
    assert session_liq.liquidity_type == BSILiquidityType.SESSION_BOX_EDGE
    assert session_liq.geometry["session_name"] == "asian"


def test_trendline_liquidity_keeps_diagonal_geometry_without_horizontal_level():
    a1 = _swing("high", 2, "1.1000")
    a2 = _swing("high", 6, "1.1080")
    trendline = TrendlinePivotSummary(
        id="tl_high_2_6",
        symbol="EURUSD",
        timeframe="M15",
        swing_type="high",
        slope=0.002,
        intercept=1.096,
        start_bar_index=2,
        end_bar_index=6,
        angle_degrees=30,
        touch_count=3,
        is_broken=False,
        break_bar_index=None,
    )

    liq = liquidity_from_trendline(trendline, anchor_1=a1, anchor_2=a2)

    assert liq.liquidity_type == BSILiquidityType.TRENDLINE_LIQUIDITY
    assert liq.reference_level == Decimal("1.108")
    assert liq.geometry["slope"] == "0.002"
    assert liq.source_swing_ids == ("sw_high_2", "sw_high_6")


def test_original_liquidity_level_and_sweep_extreme_are_separate_values():
    level = LiquidityLevel(
        id="ny_high",
        symbol="GBPUSD",
        timeframe="M15",
        start_time=NOW,
        end_time=NOW,
        detected_time=NOW,
        confirmation_time=NOW,
        configuration_version="1.0",
        configuration_hash="hash",
        side=LiquiditySide.BUY_SIDE,
        level=Decimal("1.25485"),
        source="swing",
        tolerance=Decimal("0.0001"),
    )
    liq = liquidity_from_level(level, liquidity_type=BSILiquidityType.SWING_LEVEL, source_rule_ids=("BSI2-NY-002",))
    taken = build_liquidity_taken_event(liq, sweep_extreme=Decimal("1.25610"), event_bar_index=10, event_time=NOW)

    assert taken.original_liquidity_level == Decimal("1.25485")
    assert taken.sweep_extreme == Decimal("1.25610")
    assert taken.original_liquidity_level != taken.sweep_extreme


def test_dealing_leg_uses_midpoint_not_generic_ote(monkeypatch):
    monkeypatch.setenv("MT5_OTE_MIN", "0.62")
    monkeypatch.setenv("MT5_OTE_MAX", "0.79")

    leg = build_dealing_leg(
        source_break_id="brk_1",
        start_anchor_id="sw_low",
        end_anchor_id="sw_high",
        start_price=Decimal("1.1000"),
        end_price=Decimal("1.1200"),
        start_bar_index=1,
        end_bar_index=5,
    )

    assert leg.midpoint == Decimal("1.1100")
    assert leg.classify(Decimal("1.1150")) == BSIPremiumDiscount.PREMIUM
    assert leg.classify(Decimal("1.1050")) == BSIPremiumDiscount.DISCOUNT
    assert leg.classify(Decimal("1.1100")) == BSIPremiumDiscount.AT_EQUILIBRIUM


def test_thesis_and_entry_opportunity_ids_are_stable_and_distinguish_new_arrays():
    seed = BSIIdentitySeed(
        subtype="bsi_order_flow",
        symbol="EURUSD",
        direction="LONG",
        timeframe="M15",
        structure_event_id="brk_1",
        liquidity_id="liq_1",
        entry_array_id="array_1",
    )
    same_next_cycle = BSIIdentitySeed(**seed.__dict__)
    new_array = BSIIdentitySeed(**{**seed.__dict__, "entry_array_id": "array_2"})

    assert build_bsi_thesis_id(seed) == build_bsi_thesis_id(same_next_cycle)
    assert build_bsi_entry_opportunity_id(seed) == build_bsi_entry_opportunity_id(same_next_cycle)
    assert build_bsi_entry_opportunity_id(seed) != build_bsi_entry_opportunity_id(new_array)


def test_v2_evidence_model_keeps_optional_strategy_fields_optional():
    array = BSIEntryArray(array_id="arr_1", array_type="fvg", low=Decimal("1.1"), high=Decimal("1.2"), source_fvg_id="fvg_1")
    seed = BSIIdentitySeed(subtype="bsi_abc", symbol="EURUSD", direction="SHORT", timeframe="M15", entry_array_id=array.array_id)
    evidence = BSIV2Evidence(
        bsi_version=BSI_BASELINE_V2_AUDIOVISUAL,
        strategy_subtype="bsi_abc",
        mentor_rule_ids=("BSI2-ABC-004",),
        entry_array=array,
        bsi_thesis_id=build_bsi_thesis_id(seed),
        bsi_entry_opportunity_id=build_bsi_entry_opportunity_id(seed),
    )

    payload = evidence.as_dict()
    assert payload["bsi_version"] == BSI_BASELINE_V2_AUDIOVISUAL
    assert payload["mentor_rule_ids"] == ["BSI2-ABC-004"]
    assert payload["bsi_thesis_id"].startswith("bsi2_thesis_")
    assert payload["bsi_entry_opportunity_id"].startswith("bsi2_entry_")
