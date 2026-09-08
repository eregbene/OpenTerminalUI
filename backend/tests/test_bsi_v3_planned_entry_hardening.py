from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

from backend.brokers.mt5.autonomous import MT5AutonomousTradingService, _env_float, _env_int, _v3_execution_confidence_blockers, _v3_m5_direct_execution_enabled, _v3_planned_entry_live
from backend.brokers.mt5.config import MT5Config
from backend.brokers.mt5.ownership import is_bensim_owned_position, is_bensim_owned_order
from backend.adaptive_management.v3_faiz import V3AdaptiveRoutingBucket, V3NextSessionProfile
from backend.mt5_strategies.context import REGIME_NEUTRAL, StrategyContext
from backend.mt5_strategies.families import bsi_v3_engine
from backend.mt5_strategies.families.bsi_v3_runtime_detectors import (
    RUNTIME_SPECS,
    _lower_tf_confirms,
    _load_queue,
    _make_plan_from_fvg,
    _compact_queue_by_opportunity,
    _same_poi_group,
    _upsert_new_plans,
    planned_entry_queue_path,
)
from backend.mt5_strategies.models import StrategySignal


class _FakeAdapter:
    def __init__(self, account_id: str) -> None:
        self.config = MT5Config(account_id=account_id, enabled=True, autonomous_submission_enabled=True)


def _rows_with_recent_fvg(*, timeframe_seconds: int = 60, count: int = 10) -> list[dict]:
    start = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
    rows = []
    price = Decimal("1.1000")
    for idx in range(count):
        rows.append(
            {
                "time": (start + timedelta(seconds=timeframe_seconds * idx)).isoformat(),
                "open": float(price),
                "high": float(price + Decimal("0.0002")),
                "low": float(price - Decimal("0.0002")),
                "close": float(price + Decimal("0.0001")),
                "tick_volume": 100,
            }
        )
        price += Decimal("0.0001")
    rows[-3]["high"] = 1.1000
    rows[-1]["low"] = 1.1010
    rows[-1]["high"] = 1.1018
    rows[-1]["close"] = 1.1016
    return rows


def _ctx(*, m1_rows: list[dict] | None = None, m5_rows: list[dict] | None = None) -> StrategyContext:
    return StrategyContext(
        account_id="demo_10k",
        symbol="EURUSD",
        broker_symbol="EURUSD",
        generated_at=datetime(2026, 9, 7, 12, 10, tzinfo=timezone.utc),
        regime=REGIME_NEUTRAL,
        regime_confidence=0.0,
        htf_trend_h4="LONG",
        htf_trend_h1="LONG",
        m15_snapshot=None,  # type: ignore[arg-type]
        m15_rows=[],
        h1_rows=[],
        h4_rows=[],
        atr_m15=Decimal("0.0010"),
        bid=Decimal("1.1012"),
        ask=Decimal("1.1013"),
        spread=Decimal("0.0001"),
        m1_rows=m1_rows or [],
        m5_rows=m5_rows or [],
    )


def test_same_thesis_and_entry_opportunity_merge_but_different_thesis_stays_separate() -> None:
    base = {
        "symbol": "EURUSD",
        "direction": "LONG",
        "source_timeframe": "M15",
        "entry": 1.1,
        "stop": 1.099,
        "target": 1.102,
        "fvg_low": 1.0998,
        "fvg_high": 1.1002,
        "bsi_v3_entry_opportunity_id": "entry:eurusd:long:one",
    }
    same = {**base, "strategy_id": "bsi_v3_spectre"}
    different = {**base, "bsi_v3_entry_opportunity_id": "entry:eurusd:long:two"}

    assert _same_poi_group(base, same)
    assert not _same_poi_group(base, different)


def test_queue_compaction_collapses_same_opportunity_into_confluence_row() -> None:
    base = {
        "plan_id": "react:entry:1",
        "strategy_id": "bsi_v3_reactionary_block",
        "status": "PENDING_POI_TOUCH",
        "bsi_v3_entry_opportunity_id": "entry:eurusd:short:poi1",
        "current_plan_confidence": 70.0,
        "max_plan_confidence": 70.0,
        "confluence_strategy_ids": ["bsi_v3_reactionary_block"],
        "source_videos": ["Reactionary Block Trading Strategy Example.mp4"],
    }
    duplicate = {
        **base,
        "plan_id": "spectre:entry:1",
        "strategy_id": "bsi_v3_spectre",
        "status": "CONFIRMED_FOR_ENTRY",
        "current_plan_confidence": 76.0,
        "max_plan_confidence": 76.0,
        "confluence_strategy_ids": ["bsi_v3_spectre"],
        "source_videos": ["The Spectre Example.mp4"],
    }

    compacted = _compact_queue_by_opportunity([base, duplicate])

    assert len(compacted) == 1
    assert compacted[0]["status"] == "CONFIRMED_FOR_ENTRY"
    assert compacted[0]["strategy_id"] == "bsi_v3_spectre"
    assert compacted[0]["confluence_strategy_ids"] == ["bsi_v3_reactionary_block", "bsi_v3_spectre"]
    assert compacted[0]["confluence_plan_ids"] == ["react:entry:1", "spectre:entry:1"]


def test_queue_load_salvages_valid_array_with_trailing_partial_write(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BSI_V3_PLANNED_ENTRY_QUEUE_PATH", str(tmp_path / "queue.json"))
    ctx = _ctx()
    path = planned_entry_queue_path(ctx.account_id)
    path.write_text(
        json.dumps(
            [
                {
                    "plan_id": "p1",
                    "strategy_id": "bsi_v3_order_flow",
                    "status": "PENDING_POI_TOUCH",
                    "bsi_v3_entry_opportunity_id": "entry:eurusd:long:poi1",
                }
            ]
        )
        + '{"partial":',
        encoding="utf-8",
    )

    rows = _load_queue(ctx)

    assert len(rows) == 1
    assert rows[0]["plan_id"] == "p1"


def test_profile_gated_bridge_evaluates_all_v3_detectors_before_profile_ranking(monkeypatch) -> None:
    captured: dict[str, set[str]] = {}

    def fake_detector(ctx: StrategyContext, allowed_strategy_ids: set[str]) -> StrategySignal:
        captured["allowed_strategy_ids"] = set(allowed_strategy_ids)
        return StrategySignal(
            strategy_id="bsi",
            strategy_family="bsi",
            symbol=ctx.symbol,
            broker_symbol=ctx.broker_symbol,
            direction="LONG",
            timeframe="M15(bsi_v3)",
            generated_at=ctx.generated_at,
            valid=True,
            raw_signal_strength=88.0,
            proposed_entry=1.1012,
            stop_loss=1.1002,
            take_profit=1.1027,
            reward_risk=1.5,
            regime=ctx.regime,
            evidence={
                "v3_strategy_id": "bsi_v3_reactionary_block",
                "bsi_v3_entry_opportunity_id": "entry:eurusd:long:poi1",
            },
            metadata={},
        )

    monkeypatch.setattr(
        bsi_v3_engine,
        "load_v3_next_session_profile",
        lambda: V3NextSessionProfile(
            profile_id="PIT",
            next_trading_day_utc_date="2026-09-08",
            risk_mode="NORMAL",
            allowed_buckets=(V3AdaptiveRoutingBucket("bsi_v3_abc", "EURUSD", 3.0, ("year_to_date",)),),
            methodology="BSI_BASELINE_V3_UPDATED_FAIZ_ROLLING_INTELLIGENCE",
            as_of_utc_date="2026-09-07",
            point_in_time_safe=True,
        ),
    )
    monkeypatch.setattr(bsi_v3_engine, "evaluate_bsi_v3_planned_runtime_detectors", fake_detector)

    signal = bsi_v3_engine.evaluate_bsi_v3_profile_gated(_ctx())

    assert captured["allowed_strategy_ids"] == set()
    assert signal.valid is True
    assert signal.evidence["v3_profile_decision"] == "v3_profile_bucket_neutral_rank_not_hard_blocked"


def test_m1_required_strategy_does_not_fallback_to_m5() -> None:
    spec = next(row for row in RUNTIME_SPECS if row.strategy_id == "bsi_v3_asian_v2")
    confirmed, tf = _lower_tf_confirms(_ctx(m5_rows=_rows_with_recent_fvg(timeframe_seconds=300)), spec, "LONG")

    assert not confirmed
    assert tf == "NONE"


def test_m5_required_strategy_accepts_m5_confirmation() -> None:
    spec = next(row for row in RUNTIME_SPECS if row.strategy_id == "bsi_v3_mmxm")
    confirmed, tf = _lower_tf_confirms(_ctx(m5_rows=_rows_with_recent_fvg(timeframe_seconds=300)), spec, "LONG")

    assert confirmed
    assert tf == "M5"


def test_confluence_submission_marks_all_linked_plans_consumed_atomically(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BSI_V3_PLANNED_ENTRY_QUEUE_PATH", str(tmp_path / "queue.json"))
    service = MT5AutonomousTradingService(_FakeAdapter("demo_10k"))
    path = planned_entry_queue_path("demo_10k")
    path.write_text(
        json.dumps(
            [
                {"plan_id": "p1", "status": "CONFIRMED_FOR_ENTRY"},
                {"plan_id": "p2", "status": "CONFIRMED_FOR_ENTRY"},
                {"plan_id": "p3", "status": "CONFIRMED_FOR_ENTRY"},
            ]
        ),
        encoding="utf-8",
    )

    service._mark_planned_entry_submission(["p1", "p2"], "CONSUMED", {"status": "ACCEPTED"})

    rows = json.loads(path.read_text(encoding="utf-8"))
    statuses = {row["plan_id"]: row["status"] for row in rows}
    assert statuses == {"p1": "CONSUMED", "p2": "CONSUMED", "p3": "CONFIRMED_FOR_ENTRY"}


def test_confluence_reject_does_not_mark_consumed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BSI_V3_PLANNED_ENTRY_QUEUE_PATH", str(tmp_path / "queue.json"))
    service = MT5AutonomousTradingService(_FakeAdapter("demo_10k"))
    path = planned_entry_queue_path("demo_10k")
    path.write_text(json.dumps([{"plan_id": "p1", "status": "CONFIRMED_FOR_ENTRY"}]), encoding="utf-8")

    service._mark_planned_entry_submission(["p1"], "BROKER_SUBMISSION_REJECTED", {"status": "RISK_REJECTED"})

    rows = json.loads(path.read_text(encoding="utf-8"))
    assert rows[0]["status"] == "BROKER_SUBMISSION_REJECTED"


def test_bsm_position_and_order_are_bensim_owned() -> None:
    position = SimpleNamespace(magic=0, comment="BSM|v3react+v3spec|0559")
    order = SimpleNamespace(magic=0, comment="BSM|v3mmxm|0915")

    assert is_bensim_owned_position(position, bensim_magic=5601001)
    assert is_bensim_owned_order(order, bensim_magic=5601001)


def test_legacy_bensim_auto_position_still_owned() -> None:
    position = SimpleNamespace(magic=0, comment="BENSIM_AUTO EURUSD")

    assert is_bensim_owned_position(position, bensim_magic=5601001)


def test_magic_number_is_authoritative_even_without_comment() -> None:
    position = SimpleNamespace(magic=5601001, comment="")

    assert is_bensim_owned_position(position, bensim_magic=5601001)


def test_v3_confidence_7999_rejected_and_80_eligible(monkeypatch) -> None:
    monkeypatch.setenv("BSI_V3_MIN_EXECUTION_CONFIDENCE", "80")

    assert _v3_execution_confidence_blockers(79.99) == ["BSI_V3_EXECUTION_CONFIDENCE_BELOW_MIN"]
    assert _v3_execution_confidence_blockers(80.0) == []


def test_v3_m5_direct_execution_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("BSI_V3_M5_DIRECT_EXECUTION_ENABLED", raising=False)

    assert _v3_m5_direct_execution_enabled() is False


def test_v3_m5_direct_execution_can_be_explicitly_enabled(monkeypatch) -> None:
    monkeypatch.setenv("BSI_V3_M5_DIRECT_EXECUTION_ENABLED", "true")

    assert _v3_m5_direct_execution_enabled() is True


def test_v3_planned_entry_live_flag_controls_m5_submission_guard(monkeypatch) -> None:
    monkeypatch.setenv("BSI_V3_PLANNED_ENTRY_LIVE", "true")

    assert _v3_planned_entry_live() is True


def test_fast_watcher_requires_high_confidence_or_strong_confluence(monkeypatch) -> None:
    monkeypatch.setenv("BSI_V3_FAST_ENTRY_HIGH_CONFIDENCE", "87")
    monkeypatch.setenv("BSI_V3_FAST_ENTRY_STRONG_CONFLUENCE_COUNT", "3")

    def blocked(confidence: float, confluence_count: int) -> bool:
        return confidence < _env_float("BSI_V3_FAST_ENTRY_HIGH_CONFIDENCE", 87.0) and confluence_count < _env_int("BSI_V3_FAST_ENTRY_STRONG_CONFLUENCE_COUNT", 3)

    assert blocked(86.99, 2)
    assert not blocked(87.0, 1)
    assert not blocked(82.0, 3)


def test_confluence_cannot_bypass_execution_confidence_floor(monkeypatch) -> None:
    monkeypatch.setenv("BSI_V3_MIN_EXECUTION_CONFIDENCE", "80")

    blockers = _v3_execution_confidence_blockers(69.0)

    assert "BSI_V3_EXECUTION_CONFIDENCE_BELOW_MIN" in blockers


def test_plan_confidence_6499_not_active_but_65_active(monkeypatch) -> None:
    spec = next(row for row in RUNTIME_SPECS if row.strategy_id == "bsi_v3_spectre")
    rows = _rows_with_recent_fvg(timeframe_seconds=900)
    fvg = {"direction": "LONG", "index": len(rows) - 1, "low": 1.1000, "high": 1.1010, "confirmed_at": datetime(2026, 9, 7, 12, 9, tzinfo=timezone.utc), "timeframe": "M15"}
    monkeypatch.setenv("BSI_V3_MIN_PLAN_CONFIDENCE", "65")
    ctx = _ctx()

    plan = _make_plan_from_fvg(ctx, spec, fvg, rows)
    assert plan is not None
    plan["current_plan_confidence"] = 64.99
    queue = []
    assert float(plan["current_plan_confidence"]) < 65

    plan["current_plan_confidence"] = 65.0
    assert float(plan["current_plan_confidence"]) >= 65


def test_same_h4_context_repeated_scan_updates_one_plan(monkeypatch) -> None:
    monkeypatch.setenv("BSI_V3_MIN_PLAN_CONFIDENCE", "65")
    monkeypatch.setenv("BSI_V3_PLANNED_SETUP_LOOKBACK_BARS", "12")
    spec = next(row for row in RUNTIME_SPECS if row.strategy_id == "bsi_v3_4h_order_block")
    ctx = _ctx(m5_rows=_rows_with_recent_fvg(timeframe_seconds=300))
    ctx.m15_rows[:] = _rows_with_recent_fvg(timeframe_seconds=900, count=60)
    ctx.h4_rows[:] = _rows_with_recent_fvg(timeframe_seconds=14400, count=60)
    queue: list[dict] = []

    for _ in range(20):
        _upsert_new_plans(ctx, [spec], queue)

    active = [row for row in queue if row["status"] == "PENDING_POI_TOUCH"]
    opportunity_ids = {row["bsi_v3_entry_opportunity_id"] for row in active}
    assert len(active) == len(opportunity_ids)


def test_same_reactionary_spectre_thesis_becomes_confluence(monkeypatch) -> None:
    monkeypatch.setenv("BSI_V3_MIN_PLAN_CONFIDENCE", "65")
    monkeypatch.setenv("BSI_V3_PLANNED_SETUP_LOOKBACK_BARS", "12")
    ctx = _ctx()
    ctx.m15_rows[:] = _rows_with_recent_fvg(timeframe_seconds=900, count=60)
    specs = [row for row in RUNTIME_SPECS if row.strategy_id in {"bsi_v3_reactionary_block", "bsi_v3_spectre"}]
    queue: list[dict] = []

    _upsert_new_plans(ctx, specs, queue)

    assert queue
    assert len(queue) == len({row["bsi_v3_entry_opportunity_id"] for row in queue})
    assert all(set(row["confluence_strategy_ids"]) == {"bsi_v3_reactionary_block", "bsi_v3_spectre"} for row in queue)
