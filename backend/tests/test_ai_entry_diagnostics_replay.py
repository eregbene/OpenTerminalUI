from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from backend.intelligence.trading.config import AITradingConfig
from backend.intelligence.trading.diagnostics import audit_candles, build_live_diagnostics, run_replay
from backend.intelligence.trading.market_context import build_market_context
from backend.intelligence.trading.strategies import entry_geometry, evaluate_strategies


def candles(count: int = 120, *, start: datetime | None = None, drift: float = 0.00005) -> list[dict[str, float]]:
    base_ts = int((start or datetime(2026, 7, 20, tzinfo=timezone.utc)).timestamp())
    price = 1.08
    out = []
    for idx in range(count):
        price += drift
        out.append({"t": base_ts + idx * 900, "o": price - drift, "h": price + 0.0003, "l": price - 0.0003, "c": price, "v": 1000})
    return out


def config(**overrides):
    values = {
        "trading_mode": "AUTO_PAPER",
        "order_submission_enabled": True,
        "symbol_allowlist": ("EURUSD",),
        "timeframe_allowlist": ("15m",),
        "acceptance_validation_mode": True,
    }
    values.update(overrides)
    return AITradingConfig(**values)


def test_every_strategy_no_trade_has_granular_rejection_reason():
    context = build_market_context(candles(drift=0), symbol="EURUSD", timeframe="15m", spread=0.8)
    outputs = evaluate_strategies(context)
    no_trade = [row for row in outputs if row.decision == "NO_TRADE"]

    assert no_trade
    assert all(row.rejection_codes for row in no_trade)
    assert all("NO_ENTRY" not in row.rejection_codes for row in no_trade)


def test_long_and_short_geometry_validation():
    ctx = build_market_context(candles(), symbol="EURUSD", timeframe="15m", spread=0.8)
    long_geo = entry_geometry("LONG", 1.1, 1.098, 1.104, ctx)
    short_geo = entry_geometry("SHORT", 1.1, 1.102, 1.096, ctx)
    bad_long = entry_geometry("LONG", 1.1, 1.102, 1.104, ctx)

    assert long_geo["valid"] is True
    assert short_geo["valid"] is True
    assert bad_long["valid"] is False
    assert "LONG_GEOMETRY_INVALID" in bad_long["rejection_codes"]
    assert long_geo["stop_distance_pips"] > 0


def test_data_quality_detects_duplicate_missing_and_completed_timestamps():
    rows = candles(5)
    broken = rows[:3] + [rows[2]] + rows[4:]
    quality = audit_candles(broken, interval_seconds=900, source="test")

    assert quality["status"] == "INVALID"
    assert "DUPLICATE_CANDLES" in quality["reasons"]
    assert quality["timezone"] == "UTC"
    assert quality["completed_candles_only"] is True


def test_live_diagnostics_identify_consensus_gate():
    context = {
        "symbol": "EURUSD",
        "timeframe": "15m",
        "strategy_outputs": [{"strategy": "EMA Trend", "confidence": 0, "entry_conditions_met": False, "rejection_codes": ["EMA_ALIGNMENT_FAILED"]}],
        "strategy_consensus": {"agreement_score": 0.2, "bull_score": 0.2, "bear_score": 0, "eligible_strategy_count": 0},
        "consensus_eligibility": {"eligible": False, "reasons": ["CONSENSUS_BELOW_THRESHOLD"]},
        "data_quality": {"status": "VALID"},
    }
    diagnostics = build_live_diagnostics(context, profile=config().validation_profile.model_dump())

    assert diagnostics["failed_gate"] == "strategy_gate"
    assert diagnostics["closest_strategy_to_eligibility"]["strategy"] == "EMA Trend"


def test_replay_is_read_only_and_makes_zero_external_calls(monkeypatch):
    import backend.intelligence.trading.diagnostics as module

    state: dict[str, dict] = {"ai_auto_paper_acceptance": {"entry_used": False, "complete": False}}
    monkeypatch.setattr(module, "get_state", lambda key: dict(state.get(key, {})))
    monkeypatch.setattr(module, "set_state", lambda key, value: state.__setitem__(key, dict(value)))

    async def fake_chart(symbol, interval, range_str):
        assert interval == "15m"
        return {"candles": candles(160), "source_symbol": "EURUSD=X", "current_rate": 1.1}

    monkeypatch.setattr(module.forex_service, "get_pair_chart", fake_chart)

    before_acceptance = dict(state["ai_auto_paper_acceptance"])
    result = asyncio.run(run_replay(symbol="EURUSD", days=7, config=config()))

    assert result["read_only"] is True
    assert result["provider_calls"] == 0
    assert result["ibkr_calls"] == 0
    assert state["ai_auto_paper_acceptance"] == before_acceptance
    assert result["report"]["analytics"]["candles_evaluated"] > 0
    assert "production_thresholds" in result["report"]
    assert "validation_thresholds" in result["report"]
