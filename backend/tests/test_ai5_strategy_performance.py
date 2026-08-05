from __future__ import annotations

import sys
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.modules.setdefault("pypdf", SimpleNamespace(PdfReader=object))

from backend.api.routes.research import router as research_router
from backend.intelligence.trading.consensus import build_weighted_consensus
from backend.intelligence.trading.strategies import StrategyOutput
from backend.research.performance_intelligence import StrategyPerformanceService


def _install_state(monkeypatch, trades):
    import backend.research.performance_intelligence as module

    state = {}
    monkeypatch.setattr(module, "list_trade_memory", lambda limit=500: list(trades)[:limit])
    monkeypatch.setattr(module, "get_state", lambda key: dict(state.get(key, {})))
    monkeypatch.setattr(module, "set_state", lambda key, value: state.__setitem__(key, dict(value)))
    return state


def _trade(idx: int, strategy: str = "Momentum", pnl: float = 1.0, confidence: float = 0.7, regime: str = "TRENDING", session: str = "LONDON"):
    end = datetime(2026, 7, 30, tzinfo=timezone.utc) - timedelta(days=idx)
    return {
        "id": f"t{idx}",
        "strategy": strategy,
        "created_at": (end - timedelta(minutes=45)).isoformat(),
        "exit_timestamp": end.isoformat(),
        "pnl": pnl,
        "reward": pnl,
        "confidence": confidence,
        "consensus_score": 0.72,
        "market_regime": regime,
        "session": session,
        "mae": -0.4,
        "mfe": 1.2,
        "spread_pips": 0.2,
        "strategy_outputs": [{"strategy": strategy}, {"strategy": "VWAP"}],
        "indicators": {"rsi": 51, "ema20": 1.1},
    }


def test_strategy_performance_metrics_weights_calibration_and_attribution(monkeypatch):
    trades = [_trade(i, pnl=1 if i % 3 else -0.5, confidence=0.8 if i % 3 else 0.9) for i in range(35)]
    _install_state(monkeypatch, trades)
    service = StrategyPerformanceService()

    snapshot = service.refresh_from_paper_trades()
    momentum = service.strategy("momentum")

    assert momentum is not None
    assert momentum["metrics"]["trades"] == 35
    assert "30" in momentum["rolling"]
    assert momentum["calibration"]["expected_calibration_error"] >= 0
    assert momentum["attribution"]["indicator_frequency"]["rsi"] == 35
    assert snapshot["regime_analysis"][0]["name"] == "TRENDING"
    assert round(sum(snapshot["adaptive_weights"].values()) / len(snapshot["adaptive_weights"]), 4) == 1.0


def test_sample_size_protection_keeps_weights_neutral(monkeypatch):
    _install_state(monkeypatch, [_trade(1), _trade(2, pnl=-1)])
    service = StrategyPerformanceService()

    weights = service.refresh_from_paper_trades()["adaptive_weights"]

    assert set(weights.values()) == {1.0}
    assert service.leaderboard()["sample_size_protection"]["active"] is True


def test_research_dashboard_endpoints_do_not_touch_broker_or_openai(monkeypatch):
    _install_state(monkeypatch, [_trade(i) for i in range(3)])

    import backend.brokers as brokers
    import backend.intelligence.providers.openai_client as openai_client

    monkeypatch.setattr(brokers.broker_registry, "get", lambda name: (_ for _ in ()).throw(AssertionError("broker call forbidden")))
    monkeypatch.setattr(openai_client.OpenAITradeDecisionClient, "generate_trade_decision", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("OpenAI call forbidden")))

    app = FastAPI()
    app.include_router(research_router)
    client = TestClient(app)

    assert client.get("/api/research/strategies").status_code == 200
    assert client.get("/api/research/leaderboard").status_code == 200
    assert client.get("/api/research/performance").status_code == 200
    assert client.get("/api/research/equity").status_code == 200
    assert client.get("/api/research/calibration").status_code == 200


def test_weighted_consensus_is_reproducible_and_never_zero_weight():
    outputs = [
        StrategyOutput("Momentum", "LONG", 0.7, 1.5, 1.1, 1.0, 1.3, "ok"),
        StrategyOutput("VWAP", "SHORT", 0.6, 1.5, 1.1, 1.2, 0.9, "ok"),
    ]

    weighted = build_weighted_consensus(outputs, {"momentum": 1.25, "vwap": 0.5})
    repeated = build_weighted_consensus(outputs, {"momentum": 1.25, "vwap": 0.5})
    floor_weighted = build_weighted_consensus(outputs, {"momentum": 0, "vwap": 0})

    assert weighted.model_dump() == repeated.model_dump()
    assert weighted.recommended_direction == "LONG"
    assert floor_weighted.eligible_strategy_count == 2
