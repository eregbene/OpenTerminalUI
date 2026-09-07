from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from backend.market_structure.models import (
    ConceptStatus,
    Direction,
    ImbalanceZone,
    LiquidityLevel,
    LiquiditySide,
    LiquiditySweep,
)
from backend.mt5_strategies.families import EVALUATORS
from backend.mt5_strategies.families.bsi_engine import BSI_VERSION as BSI_V1_ENGINE_VERSION, evaluate_bsi_new_york as evaluate_bsi_v1_new_york
from backend.mt5_strategies.families.bsi_v2_lifecycle import BSILifecycleStore, evaluate_bare_retest_freshness
from backend.mt5_strategies.families.bsi_v2_new_york import evaluate_bsi_v2_new_york
from backend.mt5_strategies.families.bsi_v2_primitives import BSIIdentitySeed, BSILifecycleState, build_bsi_entry_opportunity_id
from backend.mt5_strategies.families.bsi_v2_scaffold import BSI_BASELINE_V1, BSI_BASELINE_V2_AUDIOVISUAL, bsi_v2_enabled
from backend.mt5_strategies.models import DISABLED, activation_status

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


def _row(i: int, o: str, h: str, l: str, c: str) -> dict:
    return {
        "time": (NOW + timedelta(minutes=15 * i)).isoformat(),
        "open": o,
        "high": h,
        "low": l,
        "close": c,
    }


def _level(*, level_id: str = "ny_high", source: str = "swing", level: str = "1.25485") -> LiquidityLevel:
    return LiquidityLevel(
        id=level_id,
        symbol="GBPUSD",
        timeframe="M15",
        start_time=NOW,
        end_time=NOW,
        detected_time=NOW,
        confirmation_time=NOW,
        price_low=Decimal(level),
        price_high=Decimal(level),
        direction=Direction.UNKNOWN,
        configuration_version="1.0",
        configuration_hash="hash",
        side=LiquiditySide.BUY_SIDE,
        level=Decimal(level),
        source=source,
        tolerance=Decimal("0.0001"),
        supporting_event_ids=("sw_high_1",),
    )


def _sweep(*, level_id: str = "ny_high", swept: str = "1.25610", bar_index: int = 2) -> LiquiditySweep:
    t = NOW + timedelta(minutes=15 * bar_index)
    return LiquiditySweep(
        id=f"sweep_{level_id}_{bar_index}",
        symbol="GBPUSD",
        timeframe="M15",
        start_time=t,
        end_time=t,
        detected_time=t,
        confirmation_time=t,
        price_low=Decimal("1.25400"),
        price_high=Decimal(swept),
        direction=Direction.BEARISH,
        configuration_version="1.0",
        configuration_hash="hash",
        level_id=level_id,
        side=LiquiditySide.BUY_SIDE,
        swept_price=Decimal(swept),
        reclaim_price=Decimal("1.25450"),
        penetration=Decimal(swept) - Decimal("1.25485"),
        bar_index=bar_index,
    )


def _ctx(
    *,
    level: LiquidityLevel | None = None,
    sweep: LiquiditySweep | None = None,
    rows: list[dict] | None = None,
    bid: str = "1.25485",
    ask: str = "1.25490",
    atr: str = "0.00100",
    broker_min_stop: Decimal | None = None,
) -> _Ctx:
    level = level or _level()
    sweep = sweep or _sweep(level_id=level.id)
    rows = rows or [
        _row(0, "1.2530", "1.2540", "1.2520", "1.2535"),
        _row(1, "1.2535", "1.25485", "1.2530", "1.2540"),
        _row(2, "1.2540", str(sweep.swept_price), "1.2538", "1.2544"),
        _row(3, "1.2538", "1.25485", "1.2530", "1.2534"),
        _row(4, "1.2534", "1.2540", "1.2525", "1.2530"),
    ]
    return _Ctx(
        symbol="GBPUSD",
        broker_symbol="GBPUSD",
        generated_at=NOW + timedelta(minutes=75),
        regime="NEUTRAL",
        m15_snapshot=SimpleNamespace(liquidity_levels=[level], liquidity_sweeps=[sweep], imbalances=[], order_blocks=[]),
        m15_rows=rows,
        atr_m15=Decimal(atr),
        bid=Decimal(bid),
        ask=Decimal(ask),
        spread=Decimal(str(Decimal(ask) - Decimal(bid))),
        broker_min_stop_distance=broker_min_stop,
    )


def test_ny_v2_original_level_retest_positive_no_ob_fvg_required():
    signal = evaluate_bsi_v2_new_york(_ctx())

    assert signal.valid is True
    assert signal.direction == "SHORT"
    assert signal.evidence["bsi_version"] == BSI_BASELINE_V2_AUDIOVISUAL
    assert signal.evidence["entry_type"] == "bare_retest"
    assert signal.evidence["liquidity_level"] == 1.25485
    assert signal.evidence["sweep_wick_extreme"] == 1.2561
    assert signal.evidence["entry_price"] != signal.evidence["sweep_wick_extreme"]
    assert signal.reward_risk == 2.0


def test_ny_v2_wick_tip_substitution_negative():
    rows = [
        _row(0, "1.2530", "1.2540", "1.2520", "1.2535"),
        _row(1, "1.2535", "1.25485", "1.2530", "1.2540"),
        _row(2, "1.2540", "1.25610", "1.2538", "1.2544"),
        _row(3, "1.25590", "1.25610", "1.25580", "1.25600"),
    ]

    signal = evaluate_bsi_v2_new_york(_ctx(rows=rows, bid="1.25600", ask="1.25604"))

    assert signal.valid is False
    assert signal.rejection_reason == "ORIGINAL_LEVEL_NOT_RETESTED"
    assert signal.evidence["liquidity_level"] == 1.25485
    assert signal.evidence["sweep_wick_extreme"] == 1.2561


def test_ny_v2_accepts_liquidity_taken_by_wick_without_close_break():
    rows = [
        _row(0, "1.2530", "1.2540", "1.2520", "1.2535"),
        _row(1, "1.2535", "1.25485", "1.2530", "1.2540"),
        _row(2, "1.2540", "1.25610", "1.2538", "1.2544"),
        _row(3, "1.2538", "1.25485", "1.2530", "1.2534"),
    ]

    signal = evaluate_bsi_v2_new_york(_ctx(rows=rows))

    assert signal.valid is True
    assert signal.evidence["source_liquidity_level_id"] == "ny_high"


def test_ny_v2_invalid_liquidity_selection_negative():
    signal = evaluate_bsi_v2_new_york(_ctx(level=_level(source="asian_session")))

    assert signal.valid is False
    assert signal.rejection_reason == "NY_LIQUIDITY_NOT_SIGNIFICANT_SWING"


def test_ny_v2_stable_opportunity_id_and_no_duplicate_executable_on_repeated_scan():
    store = BSILifecycleStore()
    ctx = _ctx()

    first = evaluate_bsi_v2_new_york(ctx, lifecycle=store)
    second = evaluate_bsi_v2_new_york(ctx, lifecycle=store)

    assert first.valid is True
    assert second.valid is False
    assert second.rejection_reason == "OPPORTUNITY_ALREADY_CONSUMED"
    assert first.evidence["bsi_entry_opportunity_id"] == second.evidence["bsi_entry_opportunity_id"]
    assert store.state_for(first.evidence["bsi_entry_opportunity_id"]) == BSILifecycleState.CONSUMED


def test_ny_v2_new_valid_setup_gets_new_opportunity_id():
    store = BSILifecycleStore()
    first = evaluate_bsi_v2_new_york(_ctx(), lifecycle=store)
    new_level = _level(level_id="ny_high_2", level="1.26000")
    new_sweep = _sweep(level_id="ny_high_2", swept="1.26100", bar_index=2)
    rows = [
        _row(0, "1.2580", "1.2590", "1.2570", "1.2585"),
        _row(1, "1.2585", "1.26000", "1.2580", "1.2590"),
        _row(2, "1.2590", "1.26100", "1.2588", "1.2594"),
        _row(3, "1.2592", "1.26000", "1.2580", "1.2584"),
    ]
    second = evaluate_bsi_v2_new_york(_ctx(level=new_level, sweep=new_sweep, rows=rows, bid="1.26000", ask="1.26004"), lifecycle=store)

    assert first.valid is True
    assert second.valid is True
    assert first.evidence["bsi_entry_opportunity_id"] != second.evidence["bsi_entry_opportunity_id"]


def test_ny_v2_stale_entry_waits_without_rescuing_geometry():
    signal = evaluate_bsi_v2_new_york(_ctx(bid="1.25300", ask="1.25304"))

    assert signal.valid is False
    assert signal.rejection_reason == "PRICE_NOT_AT_ORIGINAL_RETEST_LEVEL"
    assert signal.evidence["freshness"] == "PRICE_NOT_AT_ORIGINAL_RETEST_LEVEL"


def test_ny_v2_required_rr_destroyed_by_quote_drift_expires_without_moving_target():
    store = BSILifecycleStore()
    signal = evaluate_bsi_v2_new_york(_ctx(bid="1.25460", ask="1.25464", atr="0.00100"), lifecycle=store)

    assert signal.valid is False
    assert signal.rejection_reason == "REQUIRED_RR_DESTROYED_BY_DRIFT"
    opportunity_id = signal.evidence["bsi_entry_opportunity_id"]
    assert store.state_for(opportunity_id) == BSILifecycleState.EXPIRED


def test_freshness_broker_stop_constraint_is_persisted_separately_from_mentor_invalidation():
    decision = evaluate_bare_retest_freshness(
        direction="LONG",
        bid=Decimal("1.0998"),
        ask=Decimal("1.1000"),
        original_liquidity_level=Decimal("1.1000"),
        mentor_invalidation_level=Decimal("1.0998"),
        tolerance=Decimal("0.0003"),
        broker_min_stop_distance=Decimal("0.0005"),
    )

    assert decision.is_executable is True
    assert decision.engineering_adjustments["mentor_invalidation_level"] == "1.0998"
    assert decision.engineering_adjustments["final_stop"] == "1.0995"
    assert decision.engineering_adjustments["constraint_source"] == "broker_min_stop"


def test_freshness_repairs_tiny_mentor_sssl_with_atr_spread_floor_instead_of_rejecting():
    decision = evaluate_bare_retest_freshness(
        direction="LONG",
        bid=Decimal("1.0998"),
        ask=Decimal("1.1000"),
        original_liquidity_level=Decimal("1.1000"),
        mentor_invalidation_level=Decimal("1.09998"),
        tolerance=Decimal("0.0003"),
        min_rr=Decimal("2"),
        planned_target=Decimal("1.10004"),
        atr=Decimal("0.0010"),
        spread=Decimal("0.00004"),
        broker_min_stop_distance=Decimal("0.00005"),
        min_stop_atr_mult=Decimal("0.25"),
    )

    assert decision.is_executable is True
    assert decision.engineering_adjustments["mentor_invalidation_level"] == "1.09998"
    assert decision.engineering_adjustments["engineering_min_stop"] == "0.000250"
    assert decision.engineering_adjustments["final_stop"] == "1.099750"
    assert decision.engineering_adjustments["constraint_source"] == "mentor_safe_sssl"
    assert decision.target == Decimal("1.100500")
    assert decision.reward_risk == Decimal("2")


def test_lifecycle_identity_does_not_depend_on_scheduler_timestamp():
    seed = BSIIdentitySeed(subtype="bsi_new_york", symbol="GBPUSD", direction="SHORT", timeframe="M15", liquidity_id="liq_1", retest_level=Decimal("1.25485"))
    later = BSIIdentitySeed(**seed.__dict__)

    assert build_bsi_entry_opportunity_id(seed) == build_bsi_entry_opportunity_id(later)


def test_ny_v2_remains_disabled_from_production_dispatch_by_default(monkeypatch):
    monkeypatch.delenv("BSI_BASELINE_V2_AUDIOVISUAL_ENABLED", raising=False)
    monkeypatch.delenv("MT5_STRATEGY_ACTIVATION_BSI", raising=False)

    assert bsi_v2_enabled() is False
    assert "bsi_v2" not in EVALUATORS
    assert activation_status("bsi") == DISABLED


def test_v1_new_york_behavior_and_version_constant_remain_unchanged():
    assert BSI_V1_ENGINE_VERSION == BSI_BASELINE_V1
    assert evaluate_bsi_v1_new_york.__name__ == "evaluate_bsi_new_york"
