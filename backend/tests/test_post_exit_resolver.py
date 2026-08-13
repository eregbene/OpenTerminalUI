"""Focused tests for the Part 8 post-exit counterfactual resolver fix.

Covers the root cause (never resolved a single position -- see AdaptiveManagerOutcomeResolver
docstring), the multi-account routing/scoping fix, the richer post-exit fields (MFE/MAE,
classification, timing), the conservative same-candle rule, and the missing-baseline backfill.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend.adaptive_management.analytics import manager_value_add_report
from backend.adaptive_management.orm import AdaptiveManagerCounterfactualORM, AdaptivePositionBaselineORM, AdaptivePositionStateORM, AdaptiveTradeEventORM
from backend.adaptive_management.outcome_resolver import AdaptiveManagerOutcomeResolver
from backend.brokers.mt5.models import MT5Candle
from backend.shared.db import SessionLocal
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite
from backend.tests.test_mt5_adapter import fake_adapter


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _seed_closed_position(
    db,
    *,
    position_id: str,
    account_id: str = "demo_10k",
    symbol: str = "EURUSD",
    direction: str = "LONG",
    entry: float = 1.1000,
    original_sl: float = 1.0950,
    original_tp: float = 1.1500,
    risk_money: float = 50.0,
    opened_at: datetime | None = None,
    closed_at: datetime | None = None,
    exit_price: float = 1.1050,
    exit_time: datetime | None = None,
    with_baseline: bool = True,
    with_cf_row: bool = False,
    extra_deals: list[dict] | None = None,
) -> None:
    closed_at = closed_at or utcnow()
    opened_at = opened_at or (closed_at - timedelta(hours=5))
    exit_time = exit_time or closed_at
    stop_distance = abs(entry - original_sl)

    state = AdaptivePositionStateORM(position_id=position_id, symbol=symbol, direction=direction, broker_ticket=position_id)
    state.account_id = account_id
    state.entry_price = entry
    state.original_entry = entry
    state.original_sl = original_sl
    state.original_tp = original_tp
    state.original_stop_distance = stop_distance
    state.current_sl = original_sl
    state.current_tp = original_tp
    state.original_risk_money = risk_money
    state.opened_at = opened_at
    state.closed_detected_at = closed_at
    state.strategy_id = "MTFAI1"
    db.add(state)

    if with_baseline:
        baseline = AdaptivePositionBaselineORM(position_id=position_id)
        baseline.account_id = account_id
        baseline.broker_ticket = position_id
        baseline.symbol = symbol
        baseline.direction = direction
        baseline.original_entry = entry
        baseline.original_sl = original_sl
        baseline.original_tp = original_tp
        baseline.initial_stop_distance = stop_distance
        baseline.initial_reward_risk = round(abs(original_tp - entry) / stop_distance, 4)
        baseline.initial_risk_money = risk_money
        baseline.original_strategy = "MTFAI1"
        db.add(baseline)

    if with_cf_row:
        cf = AdaptiveManagerCounterfactualORM(position_id=position_id, symbol=symbol, account_id=account_id)
        db.add(cf)

    deal = AdaptiveTradeEventORM(event_id=f"EVT_{position_id}_EXIT", session_id="S1", position_id=position_id, event_type="DEAL", symbol=symbol)
    deal.account_id = account_id
    deal.realized_pnl = 1.0
    deal.price = exit_price
    deal.utc_time = exit_time
    deal.raw_payload = {"entry": "OUT"}
    db.add(deal)

    for extra in extra_deals or []:
        row = AdaptiveTradeEventORM(event_id=extra["event_id"], session_id="S1", position_id=position_id, event_type="DEAL", symbol=symbol)
        row.account_id = extra.get("account_id", account_id)
        row.realized_pnl = extra.get("realized_pnl", 0.0)
        row.price = extra["price"]
        row.utc_time = extra["utc_time"]
        row.raw_payload = extra.get("raw_payload", {})
        db.add(row)


def _adapter_with_candles(candles: list[MT5Candle]):
    adapter = fake_adapter()

    async def _fake_candles(symbol, timeframe, count=100, completed_only=True):
        return candles

    adapter.candles = _fake_candles
    return adapter


# 1. Closed trade moves from PENDING to RESOLVED.
def test_closed_trade_moves_from_pending_to_resolved(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    exit_time = utcnow() - timedelta(days=4)
    with session_local() as db:
        _seed_closed_position(db, position_id="P1", closed_at=exit_time, exit_time=exit_time, exit_price=1.1050)
        db.commit()

    adapter = _adapter_with_candles([
        MT5Candle(symbol="EURUSD", timeframe="M5", time=exit_time + timedelta(minutes=5), open=1.1050, high=1.1052, low=1.1048, close=1.1051, tick_volume=1, spread=1, real_volume=1, complete=True),
    ])
    resolver = AdaptiveManagerOutcomeResolver(adapter=adapter)
    with SessionLocal() as db:
        before = db.get(AdaptiveManagerCounterfactualORM, "P1")
        assert before is None  # not even created yet -- created lazily by the original-SL/TP pass

    asyncio.run(resolver.run_once())

    with SessionLocal() as db:
        cf = db.get(AdaptiveManagerCounterfactualORM, "P1")
        assert cf.post_exit_status == "RESOLVED"


# 2. Long continuation is detected (price kept moving favorably past +1R after exit).
def test_long_continuation_detected(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    exit_time = utcnow() - timedelta(days=4)
    with session_local() as db:
        # entry 1.1000, sl 1.0950 -> stop_distance 0.0050; exit at 1.1050 -> +1R continuation level = 1.1100
        _seed_closed_position(db, position_id="P2", direction="LONG", entry=1.1000, original_sl=1.0950, original_tp=1.1500, closed_at=exit_time, exit_time=exit_time, exit_price=1.1050)
        db.commit()

    adapter = _adapter_with_candles([
        MT5Candle(symbol="EURUSD", timeframe="M5", time=exit_time + timedelta(minutes=5), open=1.1050, high=1.1110, low=1.1049, close=1.1105, tick_volume=1, spread=1, real_volume=1, complete=True),
    ])
    resolver = AdaptiveManagerOutcomeResolver(adapter=adapter)
    asyncio.run(resolver.run_once())

    with SessionLocal() as db:
        cf = db.get(AdaptiveManagerCounterfactualORM, "P2")
        assert cf.post_exit_reached_plus_1r is True
        assert cf.post_exit_reversed_strongly is False
        assert cf.post_exit_classification == "MILD_CONTINUATION"


# 3. Short continuation is detected (mirror of #2 for a SHORT position).
def test_short_continuation_detected(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    exit_time = utcnow() - timedelta(days=4)
    with session_local() as db:
        # entry 1.1000, sl 1.1050 (SHORT) -> stop_distance 0.0050; exit at 1.0950 -> +1R = 1.0900
        _seed_closed_position(db, position_id="P3", direction="SHORT", entry=1.1000, original_sl=1.1050, original_tp=1.0500, closed_at=exit_time, exit_time=exit_time, exit_price=1.0950)
        db.commit()

    adapter = _adapter_with_candles([
        MT5Candle(symbol="EURUSD", timeframe="M5", time=exit_time + timedelta(minutes=5), open=1.0950, high=1.0951, low=1.0890, close=1.0895, tick_volume=1, spread=1, real_volume=1, complete=True),
    ])
    resolver = AdaptiveManagerOutcomeResolver(adapter=adapter)
    asyncio.run(resolver.run_once())

    with SessionLocal() as db:
        cf = db.get(AdaptiveManagerCounterfactualORM, "P3")
        assert cf.post_exit_reached_plus_1r is True
        assert cf.post_exit_reversed_strongly is False


# 4. Later original-TP hit is detected.
def test_later_original_tp_hit_detected(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    exit_time = utcnow() - timedelta(days=4)
    with session_local() as db:
        _seed_closed_position(db, position_id="P4", direction="LONG", entry=1.1000, original_sl=1.0950, original_tp=1.1200, closed_at=exit_time, exit_time=exit_time, exit_price=1.1050)
        db.commit()

    adapter = _adapter_with_candles([
        MT5Candle(symbol="EURUSD", timeframe="M5", time=exit_time + timedelta(minutes=5), open=1.1050, high=1.1210, low=1.1049, close=1.1205, tick_volume=1, spread=1, real_volume=1, complete=True),
    ])
    resolver = AdaptiveManagerOutcomeResolver(adapter=adapter)
    asyncio.run(resolver.run_once())

    with SessionLocal() as db:
        cf = db.get(AdaptiveManagerCounterfactualORM, "P4")
        assert cf.post_exit_reached_original_tp is True
        assert cf.post_exit_classification == "TP_LATER_REACHED"


# 5. Reversal after exit is detected.
def test_reversal_after_exit_detected(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    exit_time = utcnow() - timedelta(days=4)
    with session_local() as db:
        # exit at 1.1050 -> reversal level (-1R) = 1.1000
        _seed_closed_position(db, position_id="P5", direction="LONG", entry=1.1000, original_sl=1.0950, original_tp=1.1500, closed_at=exit_time, exit_time=exit_time, exit_price=1.1050)
        db.commit()

    adapter = _adapter_with_candles([
        MT5Candle(symbol="EURUSD", timeframe="M5", time=exit_time + timedelta(minutes=5), open=1.1050, high=1.1051, low=1.0990, close=1.0995, tick_volume=1, spread=1, real_volume=1, complete=True),
    ])
    resolver = AdaptiveManagerOutcomeResolver(adapter=adapter)
    asyncio.run(resolver.run_once())

    with SessionLocal() as db:
        cf = db.get(AdaptiveManagerCounterfactualORM, "P5")
        assert cf.post_exit_reversed_strongly is True
        assert cf.post_exit_reached_plus_1r is False
        assert cf.post_exit_classification == "IMMEDIATE_REVERSAL"
        assert cf.post_exit_time_to_reversal_seconds is not None


# 6. No-touch window finalizes correctly (flat price -> NO_SIGNIFICANT_MOVE, not stuck PENDING).
def test_no_touch_window_finalizes_correctly(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    exit_time = utcnow() - timedelta(days=4)
    with session_local() as db:
        _seed_closed_position(db, position_id="P6", direction="LONG", entry=1.1000, original_sl=1.0950, original_tp=1.1500, closed_at=exit_time, exit_time=exit_time, exit_price=1.1050)
        db.commit()

    adapter = _adapter_with_candles([
        MT5Candle(symbol="EURUSD", timeframe="M5", time=exit_time + timedelta(minutes=5), open=1.1050, high=1.1052, low=1.1048, close=1.1051, tick_volume=1, spread=1, real_volume=1, complete=True),
    ])
    resolver = AdaptiveManagerOutcomeResolver(adapter=adapter)
    asyncio.run(resolver.run_once())

    with SessionLocal() as db:
        cf = db.get(AdaptiveManagerCounterfactualORM, "P6")
        assert cf.post_exit_status == "RESOLVED"
        assert cf.post_exit_classification == "NO_SIGNIFICANT_MOVE"
        assert cf.post_exit_reached_plus_1r is False
        assert cf.post_exit_reversed_strongly is False


# 7. Partial close is not mistaken for full position exit -- the LATEST closing deal wins,
#    not an earlier partial close nor the opening fill.
def test_partial_close_not_mistaken_for_full_exit(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    opened_at = utcnow() - timedelta(days=5)
    partial_time = utcnow() - timedelta(days=4, hours=12)
    final_exit_time = utcnow() - timedelta(days=4)
    with session_local() as db:
        _seed_closed_position(
            db, position_id="P7", direction="LONG", entry=1.1000, original_sl=1.0950, original_tp=1.1500,
            opened_at=opened_at, closed_at=final_exit_time, exit_time=final_exit_time, exit_price=1.1080,
            extra_deals=[
                {"event_id": "EVT_P7_OPEN", "price": 1.1000, "utc_time": opened_at, "raw_payload": {"entry": "IN"}, "realized_pnl": 0.0},
                {"event_id": "EVT_P7_PARTIAL", "price": 1.1030, "utc_time": partial_time, "raw_payload": {"entry": "OUT"}, "realized_pnl": 5.0},
            ],
        )
        db.commit()

    adapter = _adapter_with_candles([
        MT5Candle(symbol="EURUSD", timeframe="M5", time=final_exit_time + timedelta(minutes=5), open=1.1080, high=1.1082, low=1.1078, close=1.1081, tick_volume=1, spread=1, real_volume=1, complete=True),
    ])
    resolver = AdaptiveManagerOutcomeResolver(adapter=adapter)
    asyncio.run(resolver.run_once())

    with SessionLocal() as db:
        cf = db.get(AdaptiveManagerCounterfactualORM, "P7")
        assert cf.post_exit_status == "RESOLVED"
        # If the partial (1.1030) or the opening fill (1.1000) had been used as "exit price"
        # instead of the true final close (1.1080), the flat candle around 1.1080 would instead
        # register as a big continuation/reversal move relative to the wrong reference price.
        assert cf.post_exit_classification == "NO_SIGNIFICANT_MOVE"


# 8. Account-scoped ticket lookup: a DEAL row for the same position_id string but a DIFFERENT
#    account_id must never be picked up as this account's exit.
def test_account_scoped_ticket_lookup(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    exit_time = utcnow() - timedelta(days=4)
    foreign_time = utcnow() - timedelta(hours=1)  # much later -- would win a naive max()-by-time
    with session_local() as db:
        _seed_closed_position(
            db, position_id="P8", account_id="demo_10k", direction="LONG", entry=1.1000, original_sl=1.0950, original_tp=1.1500,
            closed_at=exit_time, exit_time=exit_time, exit_price=1.1050,
            extra_deals=[
                {"event_id": "EVT_P8_FOREIGN", "account_id": "ftmo_demo_25k", "price": 9.9999, "utc_time": foreign_time, "raw_payload": {"entry": "OUT"}, "realized_pnl": 1.0},
            ],
        )
        db.commit()

    adapter = _adapter_with_candles([
        MT5Candle(symbol="EURUSD", timeframe="M5", time=exit_time + timedelta(minutes=5), open=1.1050, high=1.1052, low=1.1048, close=1.1051, tick_volume=1, spread=1, real_volume=1, complete=True),
    ])
    resolver = AdaptiveManagerOutcomeResolver(adapter=adapter)
    asyncio.run(resolver.run_once())

    with SessionLocal() as db:
        cf = db.get(AdaptiveManagerCounterfactualORM, "P8")
        # If the foreign-account deal (price 9.9999, an hour ago) had leaked in, the window
        # would not even be elapsed yet and/or the price scale would be nonsensical -- either
        # way NOT this clean flat-price resolution against the real demo_10k exit.
        assert cf.post_exit_status == "RESOLVED"
        assert cf.post_exit_classification == "NO_SIGNIFICANT_MOVE"


# 9. 10K/25K/50K/100K cannot cross-resolve -- each account's position is resolved using ONLY
#    that account's own adapter/candle source, never another account's or the default's.
def test_accounts_cannot_cross_resolve(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    exit_time = utcnow() - timedelta(days=4)
    with session_local() as db:
        _seed_closed_position(db, position_id="DEMO1", account_id="demo_10k", direction="LONG", entry=1.1000, original_sl=1.0950, original_tp=1.1500, closed_at=exit_time, exit_time=exit_time, exit_price=1.1050)
        _seed_closed_position(db, position_id="FTMO1", account_id="ftmo_demo_25k", direction="LONG", entry=1.1000, original_sl=1.0950, original_tp=1.1500, closed_at=exit_time, exit_time=exit_time, exit_price=1.1050)
        db.commit()

    default_adapter = _adapter_with_candles([  # flat -- would resolve NO_SIGNIFICANT_MOVE
        MT5Candle(symbol="EURUSD", timeframe="M5", time=exit_time + timedelta(minutes=5), open=1.1050, high=1.1051, low=1.1049, close=1.1050, tick_volume=1, spread=1, real_volume=1, complete=True),
    ])
    ftmo_adapter = _adapter_with_candles([  # strong continuation -- distinguishable outcome
        MT5Candle(symbol="EURUSD", timeframe="M5", time=exit_time + timedelta(minutes=5), open=1.1050, high=1.1300, low=1.1049, close=1.1250, tick_volume=1, spread=1, real_volume=1, complete=True),
    ])
    factory_calls: list[str] = []

    def factory(account_id: str):
        factory_calls.append(account_id)
        assert account_id == "ftmo_demo_25k"
        return ftmo_adapter

    resolver = AdaptiveManagerOutcomeResolver(adapter=default_adapter, account_adapter_factory=factory)
    asyncio.run(resolver.run_once())

    assert factory_calls == ["ftmo_demo_25k"]  # never asked to resolve an adapter for demo_10k
    with SessionLocal() as db:
        demo_cf = db.get(AdaptiveManagerCounterfactualORM, "DEMO1")
        ftmo_cf = db.get(AdaptiveManagerCounterfactualORM, "FTMO1")
        assert demo_cf.post_exit_classification == "NO_SIGNIFICANT_MOVE"
        assert ftmo_cf.post_exit_classification in {"MILD_CONTINUATION", "SUBSTANTIAL_R_LEFT"}
        assert ftmo_cf.post_exit_mfe_r > demo_cf.post_exit_mfe_r


# 10. Missing candle data remains explicit, not silently resolved as "no movement."
def test_missing_candle_data_remains_explicit(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    exit_time = utcnow() - timedelta(days=4)
    with session_local() as db:
        _seed_closed_position(db, position_id="P10", closed_at=exit_time, exit_time=exit_time, exit_price=1.1050)
        db.commit()

    adapter = _adapter_with_candles([])  # broker/bridge never returns any candle for this window
    resolver = AdaptiveManagerOutcomeResolver(adapter=adapter)
    asyncio.run(resolver.run_once())

    with SessionLocal() as db:
        cf = db.get(AdaptiveManagerCounterfactualORM, "P10")
        assert cf.post_exit_status == "UNRESOLVABLE_NO_DATA"
        assert cf.post_exit_status != "RESOLVED"
        assert cf.post_exit_data_note == "NO_CANDLES_RETURNED_AFTER_WINDOW_ELAPSED"
        assert cf.post_exit_classification == "PENDING"  # never fabricated a movement verdict


# 11. Same-candle ambiguity (continuation AND reversal thresholds touched in one bar) is
#     resolved conservatively -- the reversal is confirmed, continuation's timing is not
#     credited from that bar.
def test_post_exit_same_candle_ambiguity_is_conservative(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    exit_time = utcnow() - timedelta(days=4)
    with session_local() as db:
        # exit 1.1050, stop_distance 0.0050 -> +1R=1.1100, -1R=1.1000. One bar's range covers both.
        _seed_closed_position(db, position_id="P11", direction="LONG", entry=1.1000, original_sl=1.0950, original_tp=1.1500, closed_at=exit_time, exit_time=exit_time, exit_price=1.1050)
        db.commit()

    adapter = _adapter_with_candles([
        MT5Candle(symbol="EURUSD", timeframe="M5", time=exit_time + timedelta(minutes=5), open=1.1050, high=1.1110, low=1.0990, close=1.1020, tick_volume=1, spread=1, real_volume=1, complete=True),
    ])
    resolver = AdaptiveManagerOutcomeResolver(adapter=adapter)
    asyncio.run(resolver.run_once())

    with SessionLocal() as db:
        cf = db.get(AdaptiveManagerCounterfactualORM, "P11")
        assert cf.post_exit_reversed_strongly is True
        # Conservative: the ambiguous bar does NOT credit continuation (matches _first_touch's
        # existing "the stop, never the target, wins a same-candle tie" convention).
        assert cf.post_exit_reached_plus_1r is False
        assert cf.post_exit_time_to_reversal_seconds is not None
        assert cf.post_exit_time_to_continuation_seconds is None
        assert cf.post_exit_classification == "IMMEDIATE_REVERSAL"


# 12. Existing manager analytics consume resolved post-exit data correctly.
def test_manager_analytics_consume_resolved_post_exit_data(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    exit_time = utcnow() - timedelta(days=4)
    with session_local() as db:
        _seed_closed_position(db, position_id="P12", direction="LONG", entry=1.1000, original_sl=1.0950, original_tp=1.1200, closed_at=exit_time, exit_time=exit_time, exit_price=1.1050)
        cf = AdaptiveManagerCounterfactualORM(position_id="P12", symbol="EURUSD", original_sltp_outcome="ORIGINAL_TP_FIRST", original_sltp_r=2.0)
        db.add(cf)
        db.commit()

    adapter = _adapter_with_candles([
        MT5Candle(symbol="EURUSD", timeframe="M5", time=exit_time + timedelta(minutes=5), open=1.1050, high=1.1210, low=1.1049, close=1.1205, tick_volume=1, spread=1, real_volume=1, complete=True),
    ])
    resolver = AdaptiveManagerOutcomeResolver(adapter=adapter)
    asyncio.run(resolver.run_once())

    report = manager_value_add_report()
    assert report["post_exit_resolved_count"] >= 1
    assert report["post_exit_classification_counts"].get("TP_LATER_REACHED", 0) >= 1
    assert any(p["position_id"] == "P12" for p in report["post_exit_opportunity_flags"])


# 13. No broker mutation occurs from the resolver (backlog run, not just a single cycle).
def test_backlog_run_causes_no_broker_mutation(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    exit_time = utcnow() - timedelta(days=4)
    with session_local() as db:
        for i in range(3):
            _seed_closed_position(db, position_id=f"P13_{i}", closed_at=exit_time, exit_time=exit_time, exit_price=1.1050)
        db.commit()

    adapter = _adapter_with_candles([
        MT5Candle(symbol="EURUSD", timeframe="M5", time=exit_time + timedelta(minutes=5), open=1.1050, high=1.1052, low=1.1048, close=1.1051, tick_volume=1, spread=1, real_volume=1, complete=True),
    ])
    resolver = AdaptiveManagerOutcomeResolver(adapter=adapter)
    asyncio.run(resolver.run_once())
    asyncio.run(resolver.run_once())

    assert adapter.client.mt5.order_send_calls == 0


# 14. Baseline backfill: a closed position with NO AdaptivePositionBaselineORM row (captured
#     before the baseline hook existed) still becomes resolvable, using AdaptivePositionStateORM's
#     own immutable original_* fields -- never left permanently, silently PENDING.
def test_baseline_backfill_recovers_positions_missing_baseline(monkeypatch: pytest.MonkeyPatch):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    exit_time = utcnow() - timedelta(days=4)
    with session_local() as db:
        _seed_closed_position(db, position_id="P14", direction="LONG", entry=1.1000, original_sl=1.0950, original_tp=1.1500, closed_at=exit_time, exit_time=exit_time, exit_price=1.1050, with_baseline=False)
        db.commit()

    with SessionLocal() as db:
        assert db.get(AdaptivePositionBaselineORM, "P14") is None

    adapter = _adapter_with_candles([
        MT5Candle(symbol="EURUSD", timeframe="M5", time=exit_time + timedelta(minutes=5), open=1.1050, high=1.1052, low=1.1048, close=1.1051, tick_volume=1, spread=1, real_volume=1, complete=True),
    ])
    resolver = AdaptiveManagerOutcomeResolver(adapter=adapter)
    counts = asyncio.run(resolver.run_once())

    assert counts["baselines_backfilled"] == 1
    with SessionLocal() as db:
        baseline = db.get(AdaptivePositionBaselineORM, "P14")
        assert baseline is not None
        assert baseline.original_sl == 1.0950
        cf = db.get(AdaptiveManagerCounterfactualORM, "P14")
        assert cf is not None
        assert cf.post_exit_status == "RESOLVED"
