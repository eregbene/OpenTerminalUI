"""Confidence Validation & Calibration layer -- Part 14's 15 required tests.

Covers: immutable candidate snapshots, no-lookahead-bias, shadow tracking (no broker orders,
correct TP/SL/MFE/MAE detection), executed-trade linking, band/component/ranking/rejection/
threshold analytics, and the frozen-trading-logic guarantees (70-74 still non-executable,
threshold still 75, zero OpenAI dependencies, live trading still blocked).
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone

import pytest

from backend.adaptive_management.orm import AdaptivePositionStateORM
from backend.brokers.mt5.candidate_evaluation import capture_cycle_candidate_evaluations, record_shadow_outcome
from backend.brokers.mt5.confidence_calibration import component_effectiveness_report, confidence_band_report, ranking_quality_report, threshold_simulation_report
from backend.brokers.mt5.config import mt5_config
from backend.brokers.mt5.models import MT5Candle
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM, MT5TradeRecordORM
from backend.brokers.mt5.outcome_resolver import CandidateOutcomeResolver
from backend.shared.db import SessionLocal
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite
from backend.tests.test_mt5_adapter import fake_adapter

COMPONENT_NAMES = (
    "trend_multi_timeframe", "structure_confluence", "reward_risk_quality", "strategy_performance",
    "symbol_performance", "correlation_quality", "volatility_suitability", "execution_conditions", "signal_freshness",
)


def _confidence(overall: float = 80.0, band: str = "strong") -> dict:
    return {
        "overall_score": overall,
        "band": band,
        "rule_version": "deterministic_confidence_v1",
        "components": [
            {"name": name, "score": overall, "weight": round(1 / 9, 4), "contribution": overall / 9, "reason": "test", "inputs": {}}
            for name in COMPONENT_NAMES
        ],
    }


def _candidate(*, candidate_id: str, symbol: str = "EURUSD", direction: str = "LONG", rank: int = 1, confidence: dict | None = None, rejection_reasons: list | None = None, entry: float = 1.1000, sl: float = 1.0950, tp: float = 1.1100) -> dict:
    return {
        "candidate_id": candidate_id,
        "canonical_pair": symbol,
        "broker_symbol": symbol,
        "direction": direction,
        "rank": rank,
        "trade_confidence": confidence or _confidence(),
        "rejection_reasons": rejection_reasons or [],
        "entry": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "context": {"risk_reward": round(abs(tp - entry) / abs(entry - sl), 2), "atr": 0.0010, "spread": 0.0001, "timestamp": datetime.now(timezone.utc).isoformat()},
    }


def _cycle_result(cycle_id: str, candidates: list[dict], *, winner: dict | None = None, status: str = "NO_TRADE", trade: dict | None = None) -> dict:
    return {"cycle_id": cycle_id, "status": status, "candidates": candidates, "winner": winner, "trade": trade, "openai_calls": 0, "order_send_calls": 0}


def _candle(t: datetime, o: float, h: float, l: float, c: float) -> MT5Candle:
    return MT5Candle(symbol="EURUSD", timeframe="M5", time=t, open=o, high=h, low=l, close=c, tick_volume=1, spread=1, real_volume=1, complete=True)


# 1. Candidate snapshot is immutable after creation.
def test_candidate_snapshot_immutable_after_creation(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    candidate = _candidate(candidate_id="C1:EURUSD:abc", confidence=_confidence(82.0, "strong"))
    written = capture_cycle_candidate_evaluations(_cycle_result("C1", [candidate], winner=candidate, status="NO_TRADE"))
    assert written == 1

    mutated = _candidate(candidate_id="C1:EURUSD:abc", confidence=_confidence(10.0, "reject"))
    written_again = capture_cycle_candidate_evaluations(_cycle_result("C1", [mutated], winner=mutated, status="NO_TRADE"))
    assert written_again == 0  # idempotent -- no second write, no overwrite

    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id="C1:EURUSD:abc").one()
        assert row.overall_confidence == 82.0
        assert row.confidence_band == "strong"


# 2. Future prices cannot affect initial confidence -- outcome writes touch ONLY outcome_*
# columns; every decision-time field recorded at capture stays exactly as first written.
def test_outcome_write_never_touches_decision_fields(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    candidate = _candidate(candidate_id="C2:EURUSD:abc", confidence=_confidence(81.0, "strong"))
    capture_cycle_candidate_evaluations(_cycle_result("C2", [candidate], winner=None, status="NO_TRADE"))

    record_shadow_outcome("C2:EURUSD:abc", outcome_status="TP_HIT", tp_hit=True, hypothetical_r=2.5)

    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id="C2:EURUSD:abc").one()
        assert row.overall_confidence == 81.0
        assert row.confidence_band == "strong"
        assert row.proposed_entry == 1.1000
        assert row.proposed_stop_loss == 1.0950
        assert row.outcome_status == "TP_HIT"
        assert row.hypothetical_r == 2.5


# 3. Shadow candidate outcome resolution never submits a broker order (source-level proof).
def test_shadow_tracker_source_never_submits_orders():
    import backend.brokers.mt5.outcome_resolver as outcome_resolver_module

    source = inspect.getsource(outcome_resolver_module)
    assert "order_send" not in source
    assert "submit_market_order" not in source
    assert "submit_order" not in source


def _resolve_single_shadow_candidate(monkeypatch: pytest.MonkeyPatch, *, candidate_id: str, entry: float, sl: float, tp: float, candles: list[MT5Candle], created_at: datetime) -> dict:
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    candidate = _candidate(candidate_id=candidate_id, entry=entry, sl=sl, tp=tp)
    capture_cycle_candidate_evaluations(_cycle_result("CYC_" + candidate_id, [candidate], winner=None, status="NO_TRADE"))
    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id=candidate_id).one()
        row.created_at = created_at
        row.expiry_at = created_at + timedelta(days=5)
        db.commit()

    adapter = fake_adapter()

    async def _fake_candles(symbol, timeframe, count=100, completed_only=True):
        return candles

    monkeypatch.setattr(adapter, "candles", _fake_candles)
    resolver = CandidateOutcomeResolver(adapter=adapter)
    result = asyncio.run(resolver.run_once())
    assert adapter.client.mt5.order_send_calls == 0

    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id=candidate_id).one()
        return {column.name: getattr(row, column.name) for column in MT5CandidateEvaluationORM.__table__.columns} | {"resolved_count": result["shadow_resolved"]}


# 4. Shadow outcome detects TP correctly.
def test_shadow_outcome_detects_tp_correctly(monkeypatch: pytest.MonkeyPatch):
    created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    candles = [
        _candle(created_at + timedelta(minutes=5), 1.1000, 1.1010, 1.0990, 1.1005),
        _candle(created_at + timedelta(minutes=10), 1.1005, 1.1105, 1.1000, 1.1100),  # TP (1.1100) touched here
    ]
    row = _resolve_single_shadow_candidate(monkeypatch, candidate_id="C4:EURUSD:x", entry=1.1000, sl=1.0950, tp=1.1100, candles=candles, created_at=created_at)
    assert row["resolved_count"] == 1
    assert row["outcome_status"] == "TP_HIT"
    assert row["tp_hit"] is True
    assert row["sl_hit"] is False
    assert row["hypothetical_r"] > 0
    assert row["time_to_tp_seconds"] == pytest.approx(600.0)


# 5. Shadow outcome detects SL correctly.
def test_shadow_outcome_detects_sl_correctly(monkeypatch: pytest.MonkeyPatch):
    created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    candles = [
        _candle(created_at + timedelta(minutes=5), 1.1000, 1.1010, 1.0995, 1.1005),
        _candle(created_at + timedelta(minutes=10), 1.1005, 1.1010, 1.0940, 1.0945),  # SL (1.0950) touched here
    ]
    row = _resolve_single_shadow_candidate(monkeypatch, candidate_id="C5:EURUSD:x", entry=1.1000, sl=1.0950, tp=1.1500, candles=candles, created_at=created_at)
    assert row["resolved_count"] == 1
    assert row["outcome_status"] == "SL_HIT"
    assert row["tp_hit"] is False
    assert row["sl_hit"] is True
    assert row["hypothetical_r"] == -1.0
    assert row["time_to_sl_seconds"] == pytest.approx(600.0)


# 6. MFE/MAE calculation is correct.
def test_mfe_mae_calculation_correct(monkeypatch: pytest.MonkeyPatch):
    created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    candles = [
        _candle(created_at + timedelta(minutes=5), 1.1000, 1.1010, 1.0995, 1.1005),   # favorable 0.0010
        _candle(created_at + timedelta(minutes=10), 1.1005, 1.1030, 1.1000, 1.1020),  # favorable 0.0030 (MFE peak)
        _candle(created_at + timedelta(minutes=15), 1.1020, 1.1010, 1.0945, 1.0950),  # SL touched; adverse 0.0055 (MAE peak)
    ]
    row = _resolve_single_shadow_candidate(monkeypatch, candidate_id="C6:EURUSD:x", entry=1.1000, sl=1.0950, tp=1.1500, candles=candles, created_at=created_at)
    assert row["outcome_status"] == "SL_HIT"
    assert row["mfe_r"] == pytest.approx(0.0030 / 0.0050, abs=1e-6)
    assert row["mae_r"] == pytest.approx(0.0055 / 0.0050, abs=1e-6)


# 7. Executed trade links back to the original candidate evaluation.
def test_executed_trade_links_back_to_original_candidate(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    candidate = _candidate(candidate_id="C7:EURUSD:x", entry=1.1000, sl=1.0950, tp=1.1100)
    trade = {"submission": {"status": "ACCEPTED", "order_ticket": 999111}, "intent": {"entry_price": "1.1001"}}
    capture_cycle_candidate_evaluations(_cycle_result("C7", [candidate], winner=candidate, status="ORDER_SUBMITTED", trade=trade))

    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id="C7:EURUSD:x").one()
        assert row.outcome_type == "EXECUTED"
        assert row.broker_ticket == "999111"

        opened_at = datetime.now(timezone.utc) - timedelta(hours=3)
        closed_at = datetime.now(timezone.utc)
        position = AdaptivePositionStateORM(position_id="999111", symbol="EURUSD", direction="LONG", broker_ticket="999111")
        position.entry_price = 1.1001
        position.opened_at = opened_at
        position.closed_detected_at = closed_at
        position.max_achieved_r = 1.8
        position.min_achieved_r = -0.2
        position.original_risk_money = 50.0
        position.winner_classification = "clean_winner"
        db.add(position)
        trade_row = MT5TradeRecordORM(trade_id="T7", cycle_id="C7", candidate_id="C7:EURUSD:x", symbol="EURUSD", broker_symbol="EURUSD", direction="LONG", lot_size=0.1)
        trade_row.order_ticket = "999111"
        trade_row.realized_pnl = 90.0
        trade_row.exit_reason = "TAKE_PROFIT"
        trade_row.open_timestamp = opened_at
        trade_row.close_timestamp = closed_at
        db.add(trade_row)
        db.commit()

    resolver = CandidateOutcomeResolver(adapter=fake_adapter())
    linked = resolver._link_executed_outcomes()
    assert linked == 1

    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id="C7:EURUSD:x").one()
        assert row.candidate_id == "C7:EURUSD:x"  # linkage intact
        assert row.outcome_status == "CLOSED"
        assert row.realized_pnl == 90.0
        assert row.realized_r == pytest.approx(1.8, abs=0.01)
        assert row.exit_reason == "TAKE_PROFIT"
        assert row.mfe_r == 1.8
        assert row.mae_r == -0.2
        assert row.proposed_entry == 1.1000  # decision-time fields still untouched


# 8. Confidence-band statistics are correct.
def test_confidence_band_statistics_correct(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    for i, (score, r) in enumerate([(82.0, 2.0), (83.0, -1.0)]):
        candidate_id = f"C8_{i}:EURUSD:x"
        candidate = _candidate(candidate_id=candidate_id, confidence=_confidence(score, "strong"))
        capture_cycle_candidate_evaluations(_cycle_result(f"C8_{i}", [candidate], winner=None, status="NO_TRADE"))
        record_shadow_outcome(candidate_id, outcome_status="TP_HIT" if r > 0 else "SL_HIT", tp_hit=r > 0, sl_hit=r < 0, hypothetical_r=r, mfe_r=max(r, 0.1), mae_r=abs(min(r, 0.0)) or 0.1)

    band = confidence_band_report()["combined"]["80-84"]
    assert band["resolved"] == 2
    assert band["win_rate"] == pytest.approx(0.5)
    assert band["avg_r"] == pytest.approx(0.5)


# 9. Threshold simulations do not change production config.
def test_threshold_simulation_does_not_change_production_config(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    before = mt5_config().min_trade_confidence
    candidate = _candidate(candidate_id="C9:EURUSD:x", confidence=_confidence(88.0, "very_strong"))
    capture_cycle_candidate_evaluations(_cycle_result("C9", [candidate], winner=None, status="NO_TRADE"))
    record_shadow_outcome("C9:EURUSD:x", outcome_status="TP_HIT", tp_hit=True, hypothetical_r=2.0, mfe_r=2.0, mae_r=0.1)

    report = threshold_simulation_report()
    assert len(report) > 0
    assert any(row["qualifying_candidates"] >= 1 for row in report if row["threshold"] <= 85.0)
    # The actual invariant under test: simulating thresholds must never mutate the real,
    # operational config -- deliberately not coupled to a specific numeric value (that value is
    # a live operational setting, see MT5_MIN_TRADE_CONFIDENCE, and changes independently of
    # this test's own concern).
    assert mt5_config().min_trade_confidence == before


# 10. Rank analysis compares multiple candidates from the same cycle.
def test_ranking_analysis_compares_multiple_candidates_same_cycle(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    c1 = _candidate(candidate_id="C10:EURUSD:x", rank=1, confidence=_confidence(85.0))
    c2 = _candidate(candidate_id="C10:GBPUSD:x", symbol="GBPUSD", rank=2, confidence=_confidence(80.0))
    capture_cycle_candidate_evaluations(_cycle_result("C10", [c1, c2], winner=c1, status="ORDER_SUBMITTED", trade={"submission": {"status": "REJECTED"}}))
    record_shadow_outcome("C10:EURUSD:x", outcome_status="SL_HIT", tp_hit=False, sl_hit=True, hypothetical_r=-1.0, mfe_r=0.1, mae_r=1.0)
    record_shadow_outcome("C10:GBPUSD:x", outcome_status="TP_HIT", tp_hit=True, sl_hit=False, hypothetical_r=3.0, mfe_r=3.0, mae_r=0.1)

    report = ranking_quality_report()
    assert report["multi_candidate_cycles"] == 1
    assert report["rank1_was_best_performing_rate"] == pytest.approx(0.0)
    assert report["avg_r_by_rank"][1] == pytest.approx(-1.0)
    assert report["avg_r_by_rank"][2] == pytest.approx(3.0)
    assert report["cycles_where_lower_rank_substantially_outperformed"] == 1


# 11. Component analysis uses only historical (resolved) outcomes.
def test_component_analysis_uses_only_historical_outcomes(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    resolved = _candidate(candidate_id="C11:EURUSD:x", confidence=_confidence(85.0))
    unresolved = _candidate(candidate_id="C11:GBPUSD:x", symbol="GBPUSD", confidence=_confidence(80.0))
    capture_cycle_candidate_evaluations(_cycle_result("C11", [resolved, unresolved], winner=None, status="NO_TRADE"))
    record_shadow_outcome("C11:EURUSD:x", outcome_status="TP_HIT", tp_hit=True, hypothetical_r=2.0, mfe_r=2.0, mae_r=0.1)
    # C11:GBPUSD:x deliberately left PENDING -- must not count toward the sample.

    report = component_effectiveness_report()
    trend_component = next(row for row in report if row["component"] == "trend_multi_timeframe")
    assert trend_component["sample_size"] == 1


# 12. 70-74 (observe_only band) candidates remain non-executable.
def test_observe_only_band_candidates_remain_non_executable(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    candidate = _candidate(candidate_id="C12:EURUSD:x", confidence=_confidence(72.0, "observe_only"), rejection_reasons=["BELOW_CONFIDENCE_THRESHOLD"])
    capture_cycle_candidate_evaluations(_cycle_result("C12", [candidate], winner=candidate, status="NO_TRADE"))

    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id="C12:EURUSD:x").one()
        assert row.eligible_for_execution is False
        assert row.selected is False
        assert row.outcome_type == "SHADOW"


# 13. Production threshold reflects the real, current operational value -- 2026-08-26:
# user-requested trade-frequency increase lowered MT5_MIN_TRADE_CONFIDENCE 75 -> 55
# (docker-compose.yml). This asserts the deliberate live value, not a fixed code constant.
def test_production_threshold_matches_current_operational_value():
    assert mt5_config().min_trade_confidence == 55.0


# 14. OpenAI calls remain zero -- no AI/provider dependency anywhere in the calibration layer.
def test_calibration_layer_source_has_zero_openai_dependencies():
    import backend.brokers.mt5.candidate_evaluation as candidate_evaluation_module
    import backend.brokers.mt5.confidence_calibration as confidence_calibration_module
    import backend.brokers.mt5.outcome_resolver as outcome_resolver_module

    for module in (candidate_evaluation_module, outcome_resolver_module, confidence_calibration_module):
        source = inspect.getsource(module)
        assert "openai" not in source.lower()
        assert "provider_registry" not in source
        assert "ai_trading_config" not in source


# 15. Live trading remains blocked.
def test_live_trading_remains_blocked():
    assert mt5_config().live_trading_enabled is False
