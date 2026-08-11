from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.brokers.mt5 import persistence
from backend.brokers.mt5.models import MT5Candle
from backend.brokers.mt5.orm import (
    MT5AIDecisionORM,
    MT5CanonicalCandleORM,
    MT5OrderRecordORM,
    MT5SchedulerCandidateORM,
    MT5SchedulerCycleORM,
    MT5TradeMemorySnapshotORM,
    MT5TradeRecordORM,
)
from backend.shared.db import Base


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(persistence, "SessionLocal", SessionLocal)
    return SessionLocal


def test_sanitized_cycle_trade_candidate_and_decision_are_persisted(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    result = {
        "cycle_id": "MT5_M5_202608031200",
        "status": "ACCEPTED",
        "symbols_discovered": 4,
        "eligible_symbols": 1,
        "openai_calls": 1,
        "order_send_calls": 1,
        "winner": {
            "canonical_pair": "EURUSD",
            "broker_symbol": "EURUSD",
            "direction": "LONG",
            "ranking_score": 82.5,
            "entry": "1.1010",
            "stop_loss": "1.0990",
            "take_profit": "1.1046",
            "risk_reward": "1.8",
            "strategy_outputs": {"trend": "aligned"},
            "consensus": {"score": 82.5},
            "market_regime": "TREND",
            "session": "NEW_YORK",
            "timeframe_context": {"policy": "MT5_ONLY", "timeframes": ["M15", "H1", "H4"]},
            "context": {"broker_server": "MetaQuotes-Demo", "account_mode": "DEMO", "password": "never-store"},
            "context_hash": "abc123",
        },
        "ai_decision": {
            "decision": "LONG",
            "confidence": 0.91,
            "model": "gpt-test",
            "raw": '{"decision":"LONG","api_key":"secret"}',
            "input_tokens": 25,
            "output_tokens": 5,
            "estimated_cost": 0.001,
        },
        "trade": {
            "trade_id": "MT5AUTO_TEST_EURUSD",
            "intent": {
                "intent_id": "MT5AUTO_TEST_EURUSD",
                "account_id": "123",
                "broker_symbol": "EURUSD",
                "canonical_pair": "EURUSD",
                "direction": "LONG",
                "volume": "0.10",
                "entry_price": "1.1010",
                "stop_loss": "1.0990",
                "take_profit": "1.1046",
            },
            "risk": {"projected_loss_usd": "20", "risk_reward": "1.8"},
            "projected_margin": "110.10",
            "submission": {
                "status": "ACCEPTED",
                "order_ticket": 1001,
                "deal_ticket": 2002,
                "fill_price": "1.1011",
                "requested_volume": "0.10",
                "filled_volume": "0.10",
                "request": {"symbol": "EURUSD", "token": "secret"},
                "raw": {"retcode": 10009, "password": "secret"},
            },
            "open_timestamp": "2026-08-03T12:01:00+00:00",
        },
    }

    persistence.persist_cycle_result(result)

    with SessionLocal() as db:
        cycle = db.get(MT5SchedulerCycleORM, "MT5_M5_202608031200")
        candidate = db.query(MT5SchedulerCandidateORM).one()
        decision = db.query(MT5AIDecisionORM).one()
        order = db.query(MT5OrderRecordORM).one()
        trade = db.query(MT5TradeRecordORM).one()

    assert cycle is not None
    assert cycle.selected_candidate_id == candidate.candidate_id
    assert cycle.ai_decision_id == decision.decision_id
    assert cycle.trade_id == trade.trade_id
    assert candidate.rejected is False
    assert decision.decision == "LONG"
    assert order.raw_request["token"] == "***REDACTED***"
    assert order.raw_response["password"] == "***REDACTED***"
    assert trade.symbol == "EURUSD"
    assert trade.order_ticket == "1001"
    assert trade.deal_tickets == ["2002"]
    assert trade.projected_margin == 110.10
    assert trade.projected_risk == 20.0
    assert trade.broker_server == "MetaQuotes-Demo"


def test_rejected_candidates_and_no_trade_decisions_are_persisted(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    result = {
        "cycle_id": "MT5_M5_202608031205",
        "status": "NO_TRADE",
        "symbols_discovered": 2,
        "eligible_symbols": 1,
        "openai_calls": 1,
        "order_send_calls": 0,
        "winner": {
            "canonical_pair": "GBPUSD",
            "broker_symbol": "GBPUSD",
            "direction": "LONG",
            "ranking_score": 75,
            "context_hash": "gbp-context",
        },
        "candidates": [
            {"canonical_pair": "USDJPY", "broker_symbol": "USDJPY", "direction": "NO_TRADE", "rejection_reasons": ["WEAK_CONSENSUS"], "context_hash": "reject-context"}
        ],
        "ai_decision": {"decision": "NO_TRADE", "confidence": 0.22, "model": "gpt-test", "raw": "{}"},
    }

    persistence.persist_cycle_result(result)

    with SessionLocal() as db:
        candidates = db.query(MT5SchedulerCandidateORM).order_by(MT5SchedulerCandidateORM.symbol).all()
        decision = db.query(MT5AIDecisionORM).one()
        cycle = db.get(MT5SchedulerCycleORM, "MT5_M5_202608031205")

    assert [row.symbol for row in candidates] == ["GBPUSD", "USDJPY"]
    assert [row.rejected for row in candidates] == [False, True]
    assert decision.decision == "NO_TRADE"
    assert cycle.order_send_calls == 0


def test_mt5_candles_are_canonical_mt5_only_with_lineage(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    ts1 = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
    ts2 = datetime(2026, 8, 3, 12, 15, tzinfo=timezone.utc)
    future = datetime.now(timezone.utc) + timedelta(days=1)
    candles = [
        MT5Candle(symbol="EURUSD", timeframe="M15", time=ts1, open=Decimal("1.1"), high=Decimal("1.2"), low=Decimal("1.0"), close=Decimal("1.15"), tick_volume=10, spread=1, real_volume=0, server="MetaQuotes-Demo"),
        MT5Candle(symbol="EURUSD", timeframe="M15", time=ts2, open=Decimal("1.15"), high=Decimal("1.25"), low=Decimal("1.12"), close=Decimal("1.2"), tick_volume=12, spread=1, real_volume=0, server="MetaQuotes-Demo"),
        MT5Candle(symbol="EURUSD", timeframe="M15", time=future, open=Decimal("1"), high=Decimal("1.1"), low=Decimal("0.9"), close=Decimal("1.05"), tick_volume=1, spread=0, real_volume=0, server="MetaQuotes-Demo"),
    ]

    assert persistence.persist_candles(candles, broker_symbol="EURUSD") == 3
    availability = persistence.history_availability(provider="MT5", dataset_policy="MT5_ONLY")
    stored = persistence.query_trades()

    with SessionLocal() as db:
        future_row = db.get(MT5CanonicalCandleORM, f"MT5:EURUSD:M15:{future.isoformat()}")

    assert stored == []
    assert future_row.quality == "INVALID_FUTURE_TIMESTAMP"
    assert availability == [
        {
            "provider": "MT5",
            "dataset_policy": "MT5_ONLY",
            "symbol": "EURUSD",
            "broker_symbol": "EURUSD",
            "timeframe": "M15",
            "earliest": ts1.isoformat(),
            "latest": ts2.isoformat(),
            "count": 2,
        }
    ]


def test_query_filters_and_retention_rules(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    persistence.persist_cycle_result(
        {
            "cycle_id": "MT5_M5_202608031210",
            "status": "SKIPPED_NO_CANDIDATE",
            "symbols_discovered": 1,
            "eligible_symbols": 0,
            "candidates": [{"canonical_pair": "XAUUSD", "broker_symbol": "XAUUSD", "direction": "NO_TRADE", "rejection_reasons": ["WEAK_CONSENSUS"], "context_hash": "xau"}],
            "openai_calls": 0,
            "order_send_calls": 0,
        }
    )

    cycles = persistence.query_cycles(symbol=None, status="SKIPPED_NO_CANDIDATE")
    rejected = persistence.query_candidates(symbol="XAUUSD", rejected=True)
    policies = {row["entity"]: row["retention_days"] for row in persistence.retention_policies()}

    with SessionLocal() as db:
        candle_count = db.query(MT5CanonicalCandleORM).count()

    assert len(cycles) == 1
    assert len(rejected) == 1
    assert policies["cycles"] == 365
    assert policies["trades"] == 2555
    assert policies["candles"] == 3650
    assert candle_count == 0


def test_reconciliation_persists_observed_open_position_and_order(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    persistence.update_trade_reconciliation(
        {
            "status": "MATCHED_OPEN",
            "positions": [
                {
                    "ticket": 9841071875,
                    "identifier": 9530788363,
                    "symbol": "EURJPY",
                    "type": 1,
                    "volume": "1.61",
                    "price_open": "180.53",
                    "sl": "180.764",
                    "tp": "180.108",
                    "profit": "-12.34",
                    "commission": "-1.20",
                    "swap": "0",
                    "time": "2026-08-03T16:30:00+00:00",
                    "password": "never-store",
                }
            ],
            "orders": [
                {
                    "ticket": 111,
                    "symbol": "EURJPY",
                    "type": 1,
                    "volume_current": "1.61",
                    "comment": "BENSIM_AUTO",
                    "api_key": "never-store",
                }
            ],
        }
    )

    with SessionLocal() as db:
        trade = db.get(MT5TradeRecordORM, "MT5POS_9841071875")
        order = db.get(MT5OrderRecordORM, "111")

    assert trade.symbol == "EURJPY"
    assert trade.direction == "SHORT"
    assert trade.order_ticket == "9841071875"
    assert trade.deal_tickets == ["9530788363"]
    assert trade.reconciliation_state == "MATCHED_OPEN"
    assert trade.raw_payload["observed_position"]["password"] == "***REDACTED***"
    assert order.symbol == "EURJPY"
    assert order.status == "OPEN"
    assert order.raw_response["observed_order"]["api_key"] == "***REDACTED***"


def test_trade_history_marks_closed_loss_and_tracks_mistake_review(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    persistence.persist_cycle_result(
        {
            "cycle_id": "MT5_M5_202608031300",
            "status": "ACCEPTED",
            "winner": {
                "canonical_pair": "AUDUSD",
                "broker_symbol": "AUDUSD",
                "direction": "SHORT",
                "ranking_score": 88,
                "entry": "0.6992",
                "stop_loss": "0.6995",
                "take_profit": "0.6986",
                "risk_reward": "1.8",
                "consensus": {"score": 88},
                "market_regime": "TREND",
                "session": "NEW_YORK",
                "context_hash": "aud-loss-context",
            },
            "ai_decision": {"decision": "SHORT", "confidence": 0.88, "model": "gpt-test"},
            "trade": {
                "trade_id": "MT5AUTO_LOSS_AUDUSD",
                "intent": {
                    "intent_id": "MT5AUTO_LOSS_AUDUSD",
                    "broker_symbol": "AUDUSD",
                    "canonical_pair": "AUDUSD",
                    "direction": "SHORT",
                    "volume": "1.0",
                    "entry_price": "0.6992",
                    "stop_loss": "0.6995",
                    "take_profit": "0.6986",
                },
                "risk": {"projected_loss_usd": "30", "risk_reward": "1.8"},
                "submission": {"status": "ACCEPTED", "order_ticket": 12345, "deal_ticket": 777, "fill_price": "0.6992"},
                "open_timestamp": "2026-08-03T13:00:00+00:00",
            },
            "openai_calls": 1,
            "order_send_calls": 1,
        }
    )

    result = persistence.update_trade_history(
        [
            {"ticket": 777, "order": 12345, "symbol": "AUDUSD", "entry": "IN", "price": "0.6992", "profit": "0", "commission": "0", "swap": "0", "time": "2026-08-03T13:00:01+00:00"},
            {"ticket": 778, "order": 12345, "symbol": "AUDUSD", "entry": "OUT", "price": "0.6995", "profit": "-31.25", "commission": "-1.00", "swap": "0", "time": "2026-08-03T13:22:00+00:00"},
        ]
    )

    with SessionLocal() as db:
        trade = db.get(MT5TradeRecordORM, "MT5AUTO_LOSS_AUDUSD")

    reviews = persistence.query_trade_reviews()
    performance = persistence.trade_performance_summary()

    assert result == {"updated_trades": 1, "reviews_created": 1, "deals_seen": 2}
    assert trade.realized_pnl == -32.25
    assert trade.exit_reason == "STOP_LOSS"
    assert trade.reconciliation_state == "MATCHED_CLOSED"
    assert trade.raw_payload["outcome_review"]["outcome"] == "LOSS"
    assert "LOSS_RECORDED" in trade.raw_payload["outcome_review"]["mistakes"]
    assert reviews[0]["trade_id"] == "MT5AUTO_LOSS_AUDUSD"
    assert performance["closed_trades"] == 1
    assert performance["losing_trades"] == 1
    assert performance["mistake_count"] >= 1


def test_trade_history_marks_closed_profit_without_loss_mistake(monkeypatch):
    _session_factory(monkeypatch)
    persistence.persist_cycle_result(
        {
            "cycle_id": "MT5_M5_202608031305",
            "status": "ACCEPTED",
            "winner": {"canonical_pair": "USDCHF", "broker_symbol": "USDCHF", "direction": "LONG", "ranking_score": 88, "entry": "0.8109", "stop_loss": "0.8104", "take_profit": "0.8119", "risk_reward": "1.8", "consensus": {"score": 88}, "context_hash": "usdchf-win"},
            "ai_decision": {"decision": "LONG", "confidence": 0.88, "model": "gpt-test"},
            "trade": {
                "trade_id": "MT5AUTO_WIN_USDCHF",
                "intent": {"intent_id": "MT5AUTO_WIN_USDCHF", "broker_symbol": "USDCHF", "canonical_pair": "USDCHF", "direction": "LONG", "volume": "1.0", "entry_price": "0.8109", "stop_loss": "0.8104", "take_profit": "0.8119"},
                "risk": {"projected_loss_usd": "30", "risk_reward": "1.8"},
                "submission": {"status": "ACCEPTED", "order_ticket": 22345, "deal_ticket": 877, "fill_price": "0.8109"},
                "open_timestamp": "2026-08-03T13:05:00+00:00",
            },
        }
    )

    persistence.update_trade_history([{"ticket": 878, "order": 22345, "symbol": "USDCHF", "entry": "OUT", "price": "0.8119", "profit": "42.50", "commission": "-1.00", "swap": "0", "time": "2026-08-03T13:45:00+00:00"}])

    reviews = persistence.query_trade_reviews(outcome="WIN")
    performance = persistence.trade_performance_summary()

    assert reviews[0]["outcome"] == "WIN"
    assert "LOSS_RECORDED" not in reviews[0]["mistakes"]
    assert performance["winning_trades"] == 1
    assert performance["total_realized_pnl"] == 41.5


def test_trade_memory_groups_losses_and_returns_candidate_guidance(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    for index, pnl in enumerate(["-20", "-25", "-30"], start=1):
        persistence.persist_cycle_result(
            {
                "cycle_id": f"MT5_M5_2026080314{index:02d}",
                "status": "ACCEPTED",
                "winner": {
                    "canonical_pair": "GBPUSD",
                    "broker_symbol": "GBPUSD",
                    "direction": "LONG",
                    "ranking_score": 70,
                    "entry": "1.3000",
                    "stop_loss": "1.2990",
                    "take_profit": "1.3010",
                    "risk_reward": "1.0",
                    "consensus": {"score": 70},
                    "market_regime": "RANGE",
                    "session": "LONDON",
                    "context_hash": f"gbp-loss-{index}",
                },
                "ai_decision": {"decision": "LONG", "confidence": 0.7, "model": "gpt-test"},
                "trade": {
                    "trade_id": f"MT5AUTO_MEMORY_LOSS_{index}",
                    "intent": {"intent_id": f"MT5AUTO_MEMORY_LOSS_{index}", "broker_symbol": "GBPUSD", "canonical_pair": "GBPUSD", "direction": "LONG", "volume": "1.0", "entry_price": "1.3000", "stop_loss": "1.2990", "take_profit": "1.3010"},
                    "risk": {"projected_loss_usd": "20", "risk_reward": "1.0"},
                    "submission": {"status": "ACCEPTED", "order_ticket": 44000 + index, "deal_ticket": 55000 + index, "fill_price": "1.3000"},
                    "open_timestamp": "2026-08-03T14:00:00+00:00",
                },
            }
        )
        persistence.update_trade_history([{"ticket": 56000 + index, "order": 44000 + index, "symbol": "GBPUSD", "entry": "OUT", "price": "1.2990", "profit": pnl, "commission": "0", "swap": "0", "time": "2026-08-03T14:05:00+00:00"}])

    refreshed = persistence.refresh_trade_memory()
    memories = persistence.query_trade_memory(symbol="GBPUSD")
    context = persistence.learning_context_for_candidate({"canonical_pair": "GBPUSD", "session": "LONDON", "market_regime": "RANGE", "direction": "LONG"})

    with SessionLocal() as db:
        memory_rows = db.query(MT5TradeMemorySnapshotORM).count()

    exact = next(row for row in memories if row["scope"] == "SYMBOL_SESSION_REGIME")
    assert refreshed["snapshots"] >= 4
    assert memory_rows >= 4
    assert exact["recommendation"] == "AVOID"
    assert exact["expectancy"] < 0
    assert exact["mistake_counts"]["LOSS_RECORDED"] == 3
    assert context["guidance"]["action"] == "AVOID_OR_REQUIRE_STRONG_OVERRIDE"


def test_trade_memory_prefers_profitable_symbol(monkeypatch):
    _session_factory(monkeypatch)
    for index, pnl in enumerate(["15", "20", "10"], start=1):
        persistence.persist_cycle_result(
            {
                "cycle_id": f"MT5_M5_2026080315{index:02d}",
                "status": "ACCEPTED",
                "winner": {"canonical_pair": "EURUSD", "broker_symbol": "EURUSD", "direction": "SHORT", "ranking_score": 90, "entry": "1.1000", "stop_loss": "1.1010", "take_profit": "1.0980", "risk_reward": "2.0", "consensus": {"score": 90}, "market_regime": "TREND", "session": "NEW_YORK", "context_hash": f"eur-win-{index}"},
                "ai_decision": {"decision": "SHORT", "confidence": 0.9, "model": "gpt-test"},
                "trade": {
                    "trade_id": f"MT5AUTO_MEMORY_WIN_{index}",
                    "intent": {"intent_id": f"MT5AUTO_MEMORY_WIN_{index}", "broker_symbol": "EURUSD", "canonical_pair": "EURUSD", "direction": "SHORT", "volume": "1.0", "entry_price": "1.1000", "stop_loss": "1.1010", "take_profit": "1.0980"},
                    "risk": {"projected_loss_usd": "20", "risk_reward": "2.0"},
                    "submission": {"status": "ACCEPTED", "order_ticket": 66000 + index, "deal_ticket": 77000 + index, "fill_price": "1.1000"},
                    "open_timestamp": "2026-08-03T15:00:00+00:00",
                },
            }
        )
        persistence.update_trade_history([{"ticket": 78000 + index, "order": 66000 + index, "symbol": "EURUSD", "entry": "OUT", "price": "1.0980", "profit": pnl, "commission": "0", "swap": "0", "time": "2026-08-03T15:05:00+00:00"}])

    memories = persistence.query_trade_memory(symbol="EURUSD")
    symbol_memory = next(row for row in memories if row["scope"] == "SYMBOL")

    assert symbol_memory["recommendation"] == "PREFER"
    assert symbol_memory["win_rate"] == 1.0
    assert symbol_memory["expectancy"] == 15.0


def test_deterministic_confidence_score_rank_and_components_are_persisted(monkeypatch):
    """12. Confidence components are persisted -- proves the deterministic confidence
    engine's output survives a real persist_cycle_result round-trip into the new
    trade_confidence_score/rank columns (migration 0038) and the full component
    breakdown into raw_payload, with no ai_decision/openai involvement."""
    SessionLocal = _session_factory(monkeypatch)
    winner_confidence = {
        "overall_score": 82.5,
        "band": "strong",
        "rule_version": "deterministic_confidence_v1",
        "components": [
            {"name": "trend_multi_timeframe", "score": 85.0, "weight": 0.20, "contribution": 17.0, "reason": "M15/H1/H4 trend-alignment score 85.0", "inputs": {}},
            {"name": "structure_confluence", "score": 78.0, "weight": 0.18, "contribution": 14.04, "reason": "SMC/ICT structure score 0.78", "inputs": {}},
        ],
    }
    runner_up_confidence = {"overall_score": 76.0, "band": "valid_autonomous", "rule_version": "deterministic_confidence_v1", "components": []}
    persistence.persist_cycle_result(
        {
            "cycle_id": "MT5_M5_202608101200",
            "status": "ACCEPTED",
            "openai_calls": 0,
            "order_send_calls": 1,
            "winner": {
                "canonical_pair": "GBPUSD", "broker_symbol": "GBPUSD", "direction": "LONG", "ranking_score": 82.5,
                "rank": 1, "trade_confidence": winner_confidence, "rejection_reasons": [],
                "entry": "1.2700", "stop_loss": "1.2650", "take_profit": "1.2800", "risk_reward": "2.0",
                "context_hash": "gbp-hash-1",
            },
            "candidates": [
                {
                    "canonical_pair": "GBPUSD", "broker_symbol": "GBPUSD", "direction": "LONG", "ranking_score": 82.5,
                    "rank": 1, "trade_confidence": winner_confidence, "rejection_reasons": [],
                    "entry": "1.2700", "stop_loss": "1.2650", "take_profit": "1.2800", "risk_reward": "2.0",
                    "context_hash": "gbp-hash-1",
                },
                {
                    "canonical_pair": "EURUSD", "broker_symbol": "EURUSD", "direction": "LONG", "ranking_score": 76.0,
                    "rank": 2, "trade_confidence": runner_up_confidence, "rejection_reasons": ["LOWER_RANKED_CANDIDATE"],
                    "context_hash": "eur-hash-1",
                },
            ],
            "trade": {
                "trade_id": "MT5AUTO_CONF_1",
                "intent": {"intent_id": "MT5AUTO_CONF_1", "broker_symbol": "GBPUSD", "canonical_pair": "GBPUSD", "direction": "LONG", "volume": "0.1", "entry_price": "1.2700", "stop_loss": "1.2650", "take_profit": "1.2800"},
                "risk": {"projected_loss_usd": "50", "risk_reward": "2.0"},
                "submission": {"status": "ACCEPTED", "order_ticket": 99001, "deal_ticket": 99002, "fill_price": "1.2700"},
                "open_timestamp": "2026-08-10T12:00:00+00:00",
            },
        }
    )

    with SessionLocal() as db:
        cycle = db.get(MT5SchedulerCycleORM, "MT5_M5_202608101200")
        assert cycle.openai_calls == 0
        assert cycle.ai_decision_id is None  # no AI decision row -- deterministic cycle
        rows = {row.symbol: row for row in db.query(MT5SchedulerCandidateORM).filter(MT5SchedulerCandidateORM.cycle_id == "MT5_M5_202608101200").all()}

    winner_row = rows["GBPUSD"]
    assert winner_row.trade_confidence_score == pytest.approx(82.5)
    assert winner_row.rank == 1
    assert winner_row.selected is True
    assert winner_row.raw_payload["trade_confidence"]["components"][0]["name"] == "trend_multi_timeframe"

    runner_up_row = rows["EURUSD"]
    assert runner_up_row.trade_confidence_score == pytest.approx(76.0)
    assert runner_up_row.rank == 2
    assert runner_up_row.selected is False
    assert "LOWER_RANKED_CANDIDATE" in runner_up_row.rejection_reasons
