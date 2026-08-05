from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.routes.strategy_research import router
from backend.research.backtests import EventDrivenBacktester
from backend.research.metrics import compute_metrics
from backend.research.models import (
    BacktestConfiguration,
    CostModel,
    DatasetSnapshot,
    ExecutionModel,
    FillPolicy,
    ParameterDefinition,
    ParameterSpace,
    WalkForwardPlan,
    WorkflowRequest,
)
from backend.research.optimization import run_optimization
from backend.research.scorecards import build_candidate, build_scorecard
from backend.research.services import ResearchService
from backend.research.validation import run_validation_job
from backend.strategies.registry import get_strategy


def bars(count: int = 90, *, drift: float = 0.5) -> list[dict[str, object]]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    price = 100.0
    out = []
    for idx in range(count):
        price += drift
        out.append(
            {
                "timestamp": (start + timedelta(minutes=15 * idx)).isoformat(),
                "open": price - 0.2,
                "high": price + 1.2,
                "low": price - 0.8,
                "close": price,
                "volume": 1000,
                "is_complete": True,
            }
        )
    return out


def dataset(count: int = 90) -> DatasetSnapshot:
    return DatasetSnapshot(dataset_snapshot_id="ds_test", symbol="TEST", timeframe="15m", bars=bars(count))


def test_event_driven_backtest_uses_next_bar_and_conservative_ambiguity() -> None:
    spec = get_strategy("ema_trend_continuation_v1")
    assert spec is not None
    config = BacktestConfiguration(quantity=10, cost_model=CostModel(fixed_commission=1), execution_model=ExecutionModel(fill_policy=FillPolicy.CONSERVATIVE))
    run = EventDrivenBacktester().run(spec, dataset(), config)
    assert run.result is not None
    assert run.result.orders
    first_order = run.result.orders[0]
    assert first_order.eligible_from > first_order.submitted_at
    assert run.result.metrics.trade_count >= 1
    assert all(trade.exit_reason in {"stop", "target"} for trade in run.result.trades)


def test_metrics_have_sample_warning_and_known_drawdown() -> None:
    spec = get_strategy("ema_trend_continuation_v1")
    assert spec is not None
    run = EventDrivenBacktester().run(spec, dataset(), BacktestConfiguration())
    metrics = run.result.metrics
    assert metrics.maximum_drawdown <= 0
    assert "fewer than 10 trades" in ";".join(metrics.warnings)


def test_optimization_is_seed_reproducible_and_keeps_all_trials() -> None:
    spec = get_strategy("ema_trend_continuation_v1")
    assert spec is not None
    space = ParameterSpace(parameters=[ParameterDefinition(name="rr", path="targets.0.value", type="float", choices=[1.5, 2.0])])
    first = run_optimization(spec, dataset(), BacktestConfiguration(), space, method="random", max_trials=2, random_seed=7)
    second = run_optimization(spec, dataset(), BacktestConfiguration(), space, method="random", max_trials=2, random_seed=7)
    assert [t.parameter_set_hash for t in first.trials] == [t.parameter_set_hash for t in second.trials]
    assert len(first.trials) == 2


def test_validation_scorecard_candidate_and_lineage() -> None:
    spec = get_strategy("ema_trend_continuation_v1")
    assert spec is not None
    ds = dataset()
    config = BacktestConfiguration()
    backtest = EventDrivenBacktester().run(spec, ds, config)
    optimization = run_optimization(spec, ds, config, ParameterSpace(), max_trials=1)
    validation = run_validation_job(spec, ds, config, WalkForwardPlan(train_bars=40, validation_bars=20, step_bars=20))
    scorecard = build_scorecard(backtest, optimization, validation, ResearchService().config.promotion)
    candidate = build_candidate(scorecard, optimization, validation, {}, {"symbol": "TEST"})
    assert scorecard.lineage.strategy_hash == backtest.lineage.strategy_hash
    assert candidate.scorecard_id == scorecard.scorecard_id
    assert candidate.promotion_status in {"qualified", "rejected"}


def test_complete_workflow_and_api() -> None:
    request = WorkflowRequest(
        bars=bars(),
        parameter_space=ParameterSpace(parameters=[ParameterDefinition(name="rr", path="targets.0.value", type="float", choices=[1.5, 2.0])]),
        walk_forward=WalkForwardPlan(train_bars=40, validation_bars=20, step_bars=20),
    )
    result = ResearchService().run_workflow(request)
    assert result.manifest["strategy_hash"] == result.backtest.lineage.strategy_hash
    assert result.candidate.promotion_status != "approved"

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    response = client.post("/api/research/strategy/workflow", json=request.model_dump(mode="json"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["manifest"]["dataset_hash"]
    assert payload["candidate"]["promotion_status"] in {"qualified", "rejected"}
