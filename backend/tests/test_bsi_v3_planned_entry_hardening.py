from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

import backend.brokers.mt5.autonomous as autonomous_mod
from backend.brokers.mt5.autonomous import (
    BSIV3OperatorLog,
    MT5AutonomousTradingService,
    _env_float,
    _env_int,
    _operator_price,
    _operator_short_opportunity_id,
    _v3_entry_window_decision,
    _v3_execution_confidence_blockers,
    _v3_m5_direct_execution_enabled,
    _v3_planned_entry_live,
)
from backend.brokers.mt5.config import MT5Config
from backend.brokers.mt5.ownership import is_bensim_owned_position, is_bensim_owned_order
from backend.adaptive_management.service import _v3_trade_horizon
from backend.adaptive_management.v3_faiz import V3AdaptiveRoutingBucket, V3NextSessionProfile
from backend.mt5_strategies.context import REGIME_NEUTRAL, StrategyContext
from backend.mt5_strategies.families import bsi_v3_engine
from backend.mt5_strategies.families.bsi_v3_runtime_detectors import (
    RUNTIME_SPECS,
    RUNTIME_CLOCK_POLICIES,
    clock_policy_for_strategy,
    evaluate_bsi_v3_existing_planned_queue,
    _lower_tf_confirms,
    _load_queue,
    _make_plan_from_fvg,
    _compact_queue_by_opportunity,
    _same_poi_group,
    _upsert_new_plans,
    plan_monitoring_state,
    planned_entry_queue_path,
)
from backend.mt5_strategies.models import StrategySignal


class _FakeAdapter:
    def __init__(self, account_id: str) -> None:
        self.config = MT5Config(account_id=account_id, enabled=True, autonomous_submission_enabled=True)


class _FakeQuery:
    def filter(self, *args, **kwargs):
        return self

    def count(self) -> int:
        return 999


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def query(self, *_args, **_kwargs):
        return _FakeQuery()


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


def test_all_v3_strategies_have_clock_policy() -> None:
    assert set(RUNTIME_CLOCK_POLICIES) == {spec.strategy_id for spec in RUNTIME_SPECS}
    assert sum(1 for policy in RUNTIME_CLOCK_POLICIES.values() if policy.trade_horizon == "SWING") == 6
    assert sum(1 for policy in RUNTIME_CLOCK_POLICIES.values() if policy.trade_horizon == "INTRADAY") == 8
    assert sum(1 for policy in RUNTIME_CLOCK_POLICIES.values() if policy.trade_horizon == "SESSION") == 8
    assert sum(1 for policy in RUNTIME_CLOCK_POLICIES.values() if policy.trade_horizon == "SCALP") == 4


def test_adaptive_horizon_resolver_recognizes_full_and_compact_v3_strategy_ids() -> None:
    assert _v3_trade_horizon("bsi_v3_mmxm") == "SWING"
    assert _v3_trade_horizon("v3mmxm+v3holy") == "SWING"
    assert _v3_trade_horizon("v3react+v3spec") == "INTRADAY"
    assert _v3_trade_horizon("v3930") == "SCALP"


def test_fast_watcher_daily_cap_is_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("BSI_V3_FAST_ENTRY_MAX_ACCEPTED_PER_ACCOUNT_DAY", raising=False)
    monkeypatch.setenv("BSI_V3_FAST_ENTRY_SYMBOL_COOLDOWN_MINUTES", "0")
    monkeypatch.setattr(autonomous_mod, "SessionLocal", lambda: _FakeSession())
    service = MT5AutonomousTradingService(_FakeAdapter("demo_10k"))

    blockers = service._fast_watcher_trade_frequency_blockers({"canonical_pair": "EURUSD"})

    assert "BSI_V3_FAST_DAILY_ACCOUNT_CAP" not in blockers


def _v3_window_candidate(confirmation_at: datetime) -> dict:
    return {
        "context": {
            "strategy_evidence": {
                "v3_strategy_id": "bsi_v3_mmxm",
                "confirmation_bar_close_at": confirmation_at.isoformat(),
            }
        }
    }


def test_bucharest_entry_window_exact_boundaries(monkeypatch) -> None:
    monkeypatch.setenv("BSI_V3_GLOBAL_ENTRY_WINDOW_ENABLED", "true")
    monkeypatch.setenv("BSI_V3_ENTRY_TIMEZONE", "Europe/Bucharest")

    cases = [
        (datetime(2026, 1, 15, 7, 49, 59, tzinfo=timezone.utc), False),  # 09:49:59 winter
        (datetime(2026, 1, 15, 7, 50, 0, tzinfo=timezone.utc), True),  # 09:50:00 winter
        (datetime(2026, 1, 15, 10, 59, 59, tzinfo=timezone.utc), True),  # 12:59:59 winter
        (datetime(2026, 1, 15, 11, 0, 0, tzinfo=timezone.utc), False),  # 13:00:00 winter
        (datetime(2026, 7, 15, 12, 29, 59, tzinfo=timezone.utc), False),  # 15:29:59 summer
        (datetime(2026, 7, 15, 12, 30, 0, tzinfo=timezone.utc), True),  # 15:30:00 summer
        (datetime(2026, 7, 15, 14, 59, 59, tzinfo=timezone.utc), True),  # 17:59:59 summer
        (datetime(2026, 7, 15, 15, 0, 0, tzinfo=timezone.utc), False),  # 18:00:00 summer
    ]

    for confirmation_at, expected in cases:
        decision = _v3_entry_window_decision(_v3_window_candidate(confirmation_at))
        assert decision["allowed"] is expected


def test_bucharest_entry_window_uses_confirmation_close_not_plan_time(monkeypatch) -> None:
    monkeypatch.setenv("BSI_V3_GLOBAL_ENTRY_WINDOW_ENABLED", "true")
    candidate = _v3_window_candidate(datetime(2026, 1, 15, 11, 3, tzinfo=timezone.utc))
    candidate["context"]["strategy_evidence"]["v3_plan_created_at"] = datetime(2026, 1, 15, 7, 30, tzinfo=timezone.utc).isoformat()

    decision = _v3_entry_window_decision(candidate)

    assert decision["allowed"] is False
    assert decision["reason"] == "ENTRY_WINDOW_BLOCKED"
    assert decision["entry_window_source"] == "BENSIM_USER_POLICY"


def test_strategy_time_exceptions_are_disabled_unless_authorized(monkeypatch) -> None:
    candidate = _v3_window_candidate(datetime(2026, 1, 15, 11, 3, tzinfo=timezone.utc))
    candidate["context"]["strategy_evidence"]["session_window"] = "ASIAN_LONDON_NY"
    candidate["context"]["strategy_evidence"]["v3_strategy_id"] = "bsi_v3_asian_v2"
    monkeypatch.setenv("BSI_V3_ALLOW_STRATEGY_TIME_EXCEPTIONS", "false")
    monkeypatch.setenv("BSI_V3_ENTRY_WINDOW_EXCEPTION_STRATEGIES", "bsi_v3_asian_v2")

    blocked = _v3_entry_window_decision(candidate)

    assert blocked["allowed"] is False
    assert blocked["conflict"] == "TIME_WINDOW_CONFLICT"

    monkeypatch.setenv("BSI_V3_ALLOW_STRATEGY_TIME_EXCEPTIONS", "true")
    allowed = _v3_entry_window_decision(candidate)

    assert allowed["allowed"] is True
    assert allowed["policy_exception"] is True


def test_bucharest_entry_window_handles_dst_transition(monkeypatch) -> None:
    monkeypatch.setenv("BSI_V3_GLOBAL_ENTRY_WINDOW_ENABLED", "true")
    monkeypatch.setenv("BSI_V3_ENTRY_TIMEZONE", "Europe/Bucharest")

    winter = _v3_entry_window_decision(_v3_window_candidate(datetime(2026, 1, 15, 7, 50, tzinfo=timezone.utc)))
    summer = _v3_entry_window_decision(_v3_window_candidate(datetime(2026, 7, 15, 6, 50, tzinfo=timezone.utc)))
    transition = _v3_entry_window_decision(_v3_window_candidate(datetime(2026, 3, 29, 6, 50, tzinfo=timezone.utc)))

    assert winter["allowed"] is True
    assert winter["local_bucharest_time"].endswith("+02:00")
    assert summer["allowed"] is True
    assert summer["local_bucharest_time"].endswith("+03:00")
    assert transition["allowed"] is True
    assert transition["local_bucharest_time"].endswith("+03:00")


def _operator_result(account_id: str = "demo_10k", *, status: str = "ENTRY_WINDOW_BLOCKED") -> dict:
    return {
        "account_id": account_id,
        "status": status,
        "blockers": ["ENTRY_WINDOW_BLOCKED"] if status == "ENTRY_WINDOW_BLOCKED" else [],
        "entry_window": {
            "allowed": False,
            "reason": "ENTRY_WINDOW_BLOCKED",
            "confirmation_completed_at": "2026-09-08T12:00:00+00:00",
            "local_bucharest_time": "2026-09-08T15:00:00+03:00",
            "next_entry_window": "2026-09-08T15:30:00+03:00",
            "entry_timezone": "Europe/Bucharest",
        },
        "cycle_id": "MT5_FAST_TEST",
        "selected_symbol": "XAUUSD",
        "selected_direction": "SHORT",
        "selected_strategy": "bsi_v3_mmxm",
        "confluence": ["bsi_v3_holy_grail", "bsi_v3_juggernaut", "bsi_v3_mmxm", "bsi_v3_standard_deviation_po3"],
        "confidence": 84.28123,
        "order_send_calls": 0,
        "monitoring_counts": {"DORMANT_PLAN": 10, "APPROACHING_POI": 2, "POI_ACTIVE": 1},
        "horizon_counts": {"SWING": 4, "INTRADAY": 2, "SESSION": 1},
        "observability": {"FAST_WATCHER_POLLS": 1, "CONFIRMATIONS_CREATED": 3, "ORDERS_SUBMITTED": 0},
    }


def test_operator_log_hides_full_ids_formats_bucharest_and_prices() -> None:
    op = BSIV3OperatorLog()
    full_id = "bsi_v3_order_flow:entry:GBPJPY:M15:SHORT:2026-09-07T21:00:00+00:00:208.97200000"

    line = op.format_result_line(_operator_result())

    assert "15:00:00 RO" in line
    assert "BLOCKED: ENTRY WINDOW" in line
    assert "MMXM +3 confluence" in line
    assert "bsi_v3_" not in line
    assert full_id not in line
    assert _operator_short_opportunity_id("GBPJPY", "SHORT", full_id).startswith("OPP-GBPJPY-S-")
    assert _operator_price(208.80899999999997, "GBPJPY") == "208.809"
    assert _operator_price(1.3815699999999995, "GBPUSD") == "1.38157"


def test_operator_log_dedupes_repeated_fast_watcher_polls_but_logs_transition(monkeypatch, tmp_path, caplog) -> None:
    monkeypatch.setenv("BSI_OPERATOR_JSON_LOG_PATH", str(tmp_path / "operator.jsonl"))
    monkeypatch.setenv("BSI_OPERATOR_STATUS_INTERVAL_SECONDS", "300")
    op = BSIV3OperatorLog()
    first = _operator_result()

    with caplog.at_level("INFO", logger="bensim.operator"):
        op.log_fast_watch_results([first])
        op.log_fast_watch_results([first])
        changed = _operator_result(status="ACCEPTED")
        changed["blockers"] = []
        op.log_fast_watch_results([changed])

    messages = [row.getMessage() for row in caplog.records if row.name == "bensim.operator"]
    assert sum("BLOCKED: ENTRY WINDOW" in msg for msg in messages) == 1
    assert any("ORDER_ACCEPTED" in msg for msg in messages)
    raw_lines = (tmp_path / "operator.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 3
    assert all("raw_result" in json.loads(line) for line in raw_lines)


def test_operator_log_collapses_identical_account_blockers(monkeypatch, tmp_path, caplog) -> None:
    monkeypatch.setenv("BSI_OPERATOR_JSON_LOG_PATH", str(tmp_path / "operator.jsonl"))
    op = BSIV3OperatorLog()
    results = [_operator_result(account_id) for account_id in ["demo_10k", "ftmo_demo_25k", "ftmo_demo_50k", "ftmo_demo_100k"]]

    with caplog.at_level("INFO", logger="bensim.operator"):
        op.log_fast_watch_results(results)

    messages = [row.getMessage() for row in caplog.records if row.name == "bensim.operator"]
    assert any("ALL ACCOUNTS" in msg for msg in messages)


def test_new_plan_persists_strategy_clock_metadata() -> None:
    spec = next(row for row in RUNTIME_SPECS if row.strategy_id == "bsi_v3_4h_order_block")
    ctx = _ctx()
    fvg = {"direction": "LONG", "index": 9, "low": 1.1010, "high": 1.1018, "confirmed_at": ctx.generated_at, "timeframe": "H4"}

    plan = _make_plan_from_fvg(ctx, spec, fvg, _rows_with_recent_fvg(count=12))

    assert plan is not None
    assert plan["trade_horizon"] == "SWING"
    assert plan["planning_timeframe"] == "H4"
    assert plan["confirmation_timeframe"] == "M15"
    assert plan["mentor_invalidation_model"] == spec.stop_model


def test_far_from_poi_plan_remains_dormant() -> None:
    plan = {"direction": "LONG", "fvg_low": 1.1000, "fvg_high": 1.1010}

    state = plan_monitoring_state(plan, bid=1.1200, ask=1.1201, spread=0.0001)

    assert state["monitoring_state"] == "DORMANT_PLAN"
    assert state["activation_basis"].startswith("BENSIM_ENGINEERING")


def test_approaching_poi_activates_monitoring() -> None:
    plan = {"direction": "LONG", "fvg_low": 1.1000, "fvg_high": 1.1010}

    state = plan_monitoring_state(plan, bid=1.1025, ask=1.1026, spread=0.0001)

    assert state["monitoring_state"] == "APPROACHING_POI"


def test_inside_poi_is_active() -> None:
    plan = {"direction": "SHORT", "fvg_low": 1.1000, "fvg_high": 1.1010}

    state = plan_monitoring_state(plan, bid=1.1005, ask=1.1006, spread=0.0001)

    assert state["monitoring_state"] == "POI_ACTIVE"


def test_confirmation_id_uses_closed_confirmation_candle(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BSI_V3_PLANNED_ENTRY_QUEUE_PATH", str(tmp_path / "queue.json"))
    spec = next(row for row in RUNTIME_SPECS if row.strategy_id == "bsi_v3_asian_v2")
    ctx = _ctx(m1_rows=_rows_with_recent_fvg(count=60))
    queue = []
    _upsert_new_plans(ctx, [spec], queue)
    assert queue
    for plan in queue:
        plan["fvg_low"] = 1.1010
        plan["fvg_high"] = 1.1018
    path = planned_entry_queue_path(ctx.account_id)
    path.write_text(json.dumps(queue), encoding="utf-8")

    signal = evaluate_bsi_v3_existing_planned_queue(ctx, set())

    assert signal is not None
    expected_key = ctx.m1_rows[-1]["time"]
    assert str(signal.evidence["bsi_v3_confirmation_id"]).endswith(f":M1:{expected_key}")


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


def _v3_candidate(symbol: str, direction: str, opportunity_id: str, confidence: float) -> dict:
    return {
        "canonical_pair": symbol,
        "broker_symbol": symbol,
        "direction": direction,
        "ranking_score": confidence,
        "raw_trend_score": confidence,
        "risk_reward": 1.8,
        "trade_confidence": {"overall_score": confidence},
        "context": {
            "strategy_id": "bsi_v3_reactionary_block",
            "strategy_evidence": {
                "v3_strategy_id": "bsi_v3_reactionary_block",
                "v3_confluence_strategy_ids": ["bsi_v3_reactionary_block", "bsi_v3_spectre"],
                "bsi_v3_market_context_id": f"context:{symbol}:{direction}:H1",
                "bsi_v3_market_thesis_id": f"thesis:{symbol}:{direction}:H1",
                "bsi_v3_poi_id": f"poi:{symbol}:{direction}:zone",
                "bsi_v3_entry_opportunity_id": opportunity_id,
                "bsi_v3_confirmation_id": f"confirm:{opportunity_id}:M1",
                "v3_poi_touch_status": "CONFIRMED_FOR_ENTRY",
                "plan_confidence": confidence,
            },
        },
    }


def test_global_opportunity_book_defers_weaker_shared_usd_exposure(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BSI_V3_PLANNED_ENTRY_QUEUE_PATH", str(tmp_path / "queue.json"))
    service = MT5AutonomousTradingService(_FakeAdapter("demo_10k"))
    stronger_path = planned_entry_queue_path("ftmo_demo_100k")
    stronger_path.write_text(
        json.dumps(
            [
                {
                    "account_id": "ftmo_demo_100k",
                    "symbol": "EURUSD",
                    "direction": "LONG",
                    "status": "CONFIRMED_FOR_ENTRY",
                    "strategy_id": "bsi_v3_reactionary_block",
                    "confluence_strategy_ids": ["bsi_v3_reactionary_block", "bsi_v3_spectre"],
                    "bsi_v3_market_context_id": "context:EURUSD:LONG:H1",
                    "bsi_v3_market_thesis_id": "thesis:EURUSD:LONG:H1",
                    "bsi_v3_poi_id": "poi:EURUSD:LONG:zone",
                    "bsi_v3_entry_opportunity_id": "entry:eurusd:long:strong",
                    "current_plan_confidence": 90.0,
                    "rr": 1.8,
                }
            ]
        ),
        encoding="utf-8",
    )

    blockers = service._v3_global_portfolio_blockers(_v3_candidate("GBPUSD", "LONG", "entry:gbpusd:long:weak", 84.0))

    assert "BSI_V3_PORTFOLIO_DEFERRED_CORRELATED_EXPOSURE" in blockers


def test_global_opportunity_book_allows_independent_selected_opportunity(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BSI_V3_PLANNED_ENTRY_QUEUE_PATH", str(tmp_path / "queue.json"))
    service = MT5AutonomousTradingService(_FakeAdapter("demo_10k"))
    planned_entry_queue_path("ftmo_demo_100k").write_text(
        json.dumps(
            [
                {
                    "symbol": "EURUSD",
                    "direction": "LONG",
                    "status": "CONFIRMED_FOR_ENTRY",
                    "strategy_id": "bsi_v3_reactionary_block",
                    "bsi_v3_entry_opportunity_id": "entry:eurusd:long:strong",
                    "current_plan_confidence": 90.0,
                    "rr": 1.8,
                }
            ]
        ),
        encoding="utf-8",
    )

    blockers = service._v3_global_portfolio_blockers(_v3_candidate("USDJPY", "LONG", "entry:usdjpy:long:ok", 86.0))

    assert blockers == []


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
