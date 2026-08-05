from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

from backend.forex_strategies.models import CandidateStatus, ExecutionMode, StrategyStatus
from backend.forex_strategies.registry import strategy_registry
from backend.forex_strategies.service import ForexSignalService
from backend.forex_strategies.store import ForexSignalStore
from backend.trading.persistence import TradingStore
from backend.trading.services import TradingControlService


def _service(tmp_path: Path) -> ForexSignalService:
    return ForexSignalService(ForexSignalStore(tmp_path / "fx"), TradingControlService(TradingStore(tmp_path / "trading")))


def _context(symbol: str = "EURUSD", **overrides):
    payload = {
        "symbol": symbol,
        "timeframe": "1h",
        "price": 1.085 if symbol != "USDJPY" else 151.25,
        "regime": "trend",
        "session": "london",
        "data_quality": 1,
        "stale": False,
        "spread": 0.00008,
        "framework_agreement": 0.72,
        "framework_bias": "BULLISH",
        "frameworks": ["trend_following", "price_action", "support_resistance", "breakout", "momentum", "smc", "ict"],
    }
    payload.update(overrides)
    return payload


def test_strategy_registry_safe_defaults():
    strategies = {item.strategy_id: item for item in strategy_registry.all()}

    assert strategies["trend_pullback_v1"].status in {StrategyStatus.PAPER_CANDIDATE, StrategyStatus.PAPER_APPROVED, StrategyStatus.PAPER_ACTIVE}
    assert strategies["trend_pullback_v1"].execution_mode == ExecutionMode.MANUAL_CONFIRMATION
    assert strategies["xauusd_analysis_only_v1"].status == StrategyStatus.ANALYSIS_ONLY
    assert strategies["xauusd_analysis_only_v1"].enabled is False


def test_candidate_generation_is_idempotent(tmp_path):
    service = _service(tmp_path)
    candle = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)

    first = service.generate(_context(candle_timestamp=candle))
    second = service.generate(_context(candle_timestamp=candle))

    assert first.candidate_id == second.candidate_id
    assert first.status == CandidateStatus.AWAITING_CONFIRMATION
    assert first.risk_decision and first.risk_decision.approved


def test_xauusd_execution_is_blocked(tmp_path):
    service = _service(tmp_path)

    candidate = service.generate(_context("XAUUSD", price=2350.0, frameworks=["trend_following", "smc", "ict"]))

    assert candidate.status == CandidateStatus.REJECTED
    assert any("XAUUSD_EXECUTION_DISABLED" in row.reasons for row in candidate.eligibility)
    assert service.instrument_status()["XAUUSD"] == "CONTRACT_UNAVAILABLE"


def test_stale_data_rejects_candidate(tmp_path):
    service = _service(tmp_path)

    candidate = service.generate(_context(stale=True))

    assert candidate.status == CandidateStatus.REJECTED
    assert any("STALE_DATA" in row.reasons for row in candidate.eligibility)


def test_manual_approval_uses_existing_oms_and_fill_path(tmp_path):
    service = _service(tmp_path)
    candidate = service.generate(_context())

    approved = service.approve(candidate.candidate_id)
    filled = service.simulate_fill(candidate.candidate_id)

    assert approved.oms_order_id
    assert filled.status == CandidateStatus.FILLED
    assert filled.fill_ids
    assert service.summary()["active_paper_trades"] == 1
