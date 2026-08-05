from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.intelligence.trading.models import TradeDecision


def _base(**kwargs):
    now = datetime.now(timezone.utc)
    payload = {
        "symbol": "EURUSD",
        "timeframe": "15m",
        "decision": "NO_TRADE",
        "entry_type": "NONE",
        "confidence": 0.7,
        "risk_reward_ratio": 0,
        "risk_percent": 0,
        "strategy": "session_breakout",
        "market_regime": "neutral",
        "reasoning_summary": "No trade.",
        "invalidation_conditions": ["fresh signal"],
        "data_timestamp": now,
        "decision_timestamp": now,
    }
    payload.update(kwargs)
    return payload


def test_long_geometry_validation() -> None:
    row = TradeDecision.model_validate(_base(decision="LONG", entry_type="MARKET", proposed_entry=1.1, stop_loss=1.0, take_profit=1.2, risk_reward_ratio=2, risk_percent=0.1))
    assert row.decision == "LONG"
    with pytest.raises(ValueError):
        TradeDecision.model_validate(_base(decision="LONG", entry_type="MARKET", proposed_entry=1.1, stop_loss=1.2, take_profit=1.0, risk_reward_ratio=2, risk_percent=0.1))


def test_short_geometry_validation() -> None:
    row = TradeDecision.model_validate(_base(decision="SHORT", entry_type="MARKET", proposed_entry=1.1, stop_loss=1.2, take_profit=1.0, risk_reward_ratio=2, risk_percent=0.1))
    assert row.decision == "SHORT"
    with pytest.raises(ValueError):
        TradeDecision.model_validate(_base(decision="SHORT", entry_type="MARKET", proposed_entry=1.1, stop_loss=1.0, take_profit=1.2, risk_reward_ratio=2, risk_percent=0.1))


def test_no_trade_validation() -> None:
    row = TradeDecision.model_validate(_base())
    assert row.decision == "NO_TRADE"
    assert row.proposed_entry is None


def test_schema_rejection_extra_field() -> None:
    with pytest.raises(ValueError):
        TradeDecision.model_validate(_base(secret="not allowed"))

