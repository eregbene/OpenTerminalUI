from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.strategies import router as strategy_router
from backend.market_data.models import AssetClass, DataQualityMetadata, DataOrigin
from backend.strategies.engine import StrategyEngine
from backend.strategies.evaluator import evaluate_rule
from backend.strategies.models import Condition, DataPolicy, Direction, RuleGroup
from backend.strategies.registry import get_strategy, list_strategy_registrations


def _bars(count: int = 80, *, drift: float = 0.5) -> list[dict[str, object]]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    price = 100.0
    bars: list[dict[str, object]] = []
    for idx in range(count):
        price += drift
        bars.append(
            {
                "timestamp": (start + timedelta(minutes=15 * idx)).isoformat(),
                "open": price - (0.2 if drift >= 0 else -0.2),
                "high": price + 0.4,
                "low": price - 0.4,
                "close": price,
                "volume": 1000,
                "is_complete": True,
            }
        )
    return bars


def test_reference_registry_has_required_strategies() -> None:
    registrations = list_strategy_registrations()
    ids = {row.strategy_id for row in registrations}
    assert {
        "ema_trend_continuation_v1",
        "rsi_mean_reversion_v1",
        "donchian_breakout_v1",
        "smc_liquidity_reversal_v1",
        "smc_continuation_v1",
    }.issubset(ids)
    assert all(row.required_features for row in registrations)


def test_ema_reference_strategy_generates_long_proposal_with_evidence() -> None:
    spec = get_strategy("ema_trend_continuation_v1")
    assert spec is not None
    evaluation = StrategyEngine().evaluate_bars(spec, _bars(), symbol="TEST", asset_class=AssetClass.EQUITY)
    decision = evaluation.decisions[0]
    assert decision.decision_type == "long"
    assert decision.direction == Direction.LONG
    assert decision.rule_result is not None
    assert evaluation.proposals
    assert evaluation.proposals[0].invalidation_price < evaluation.proposals[0].reference_price
    assert evaluation.events[-1].event_type == "strategy.proposal.created"


def test_data_quality_policy_blocks_simulated_data() -> None:
    spec = get_strategy("ema_trend_continuation_v1")
    assert spec is not None
    spec = spec.model_copy(update={"data_policy": DataPolicy(allow_simulated=False)})
    quality = DataQualityMetadata(origin=DataOrigin.SIMULATED, is_simulated=True, quality_score=1)
    evaluation = StrategyEngine().evaluate_bars(spec, _bars(), symbol="TEST", data_quality=quality)
    assert evaluation.decisions[0].decision_type == "blocked"
    assert "simulated data" in evaluation.warnings[0]


def test_operator_validation_rejects_unknown_feature_namespace() -> None:
    spec = get_strategy("ema_trend_continuation_v1")
    assert spec is not None
    broken = spec.model_copy(
        deep=True,
        update={"entry": spec.entry.model_copy(update={"long": Condition(feature="unsafe.eval", operator="eq", value=True)})},
    )
    with pytest.raises(ValueError, match="unknown feature namespace"):
        StrategyEngine().compile(broken)


def test_feature_to_feature_comparison_is_deterministic() -> None:
    spec = get_strategy("ema_trend_continuation_v1")
    assert spec is not None
    context = StrategyEngine().evaluate_bars(spec, _bars(), symbol="TEST").feature_rows[0]
    from backend.strategies.inputs import build_context

    strategy_context = build_context(spec, _bars(), symbol="TEST")
    result = evaluate_rule(RuleGroup(all=[Condition(feature="indicator.ema.20", operator="gt", value="indicator.ema.50")]), strategy_context)
    assert result.result is True


def test_strategy_api_lists_and_evaluates_reference_strategy() -> None:
    app = FastAPI()
    app.include_router(strategy_router)
    client = TestClient(app)
    listed = client.get("/api/strategies")
    assert listed.status_code == 200
    assert any(row["strategy_id"] == "ema_trend_continuation_v1" for row in listed.json())

    response = client.post(
        "/api/strategies/evaluate",
        json={
            "strategy_id": "ema_trend_continuation_v1",
            "symbol": "TEST",
            "asset_class": "equity",
            "timeframe": "15m",
            "bars": _bars(),
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["decisions"][0]["decision_type"] == "long"
    assert payload["proposals"][0]["direction"] == "long"
