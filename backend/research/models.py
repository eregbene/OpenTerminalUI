from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def stable_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def stable_id(prefix: str, *parts: object) -> str:
    return f"{prefix}_{stable_hash([str(part) for part in parts])}"


class ResearchStatus(StrEnum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CandidateStatus(StrEnum):
    DRAFT = "draft"
    EVALUATING = "evaluating"
    QUALIFIED = "qualified"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"
    APPROVED = "approved"


class FillPolicy(StrEnum):
    CONSERVATIVE = "conservative"
    OPTIMISTIC = "optimistic"
    STOP_FIRST = "stop_first"
    TARGET_FIRST = "target_first"
    NO_FILL_ON_AMBIGUITY = "no_fill_on_ambiguity"
    LOWER_TIMEFRAME_REQUIRED = "lower_timeframe_required"


class SimulatedOrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"
    MARKET_ON_NEXT_BAR = "market_on_next_bar"
    LIMIT_AT_ZONE = "limit_at_zone"
    STOP_ENTRY = "stop_entry"


class PartitionRole(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"
    FINAL_HOLDOUT = "final_holdout"


class ResearchModel(BaseModel):
    model_config = ConfigDict(use_enum_values=True, populate_by_name=True)


class LineageRecord(ResearchModel):
    strategy_id: str
    strategy_version: str
    strategy_hash: str
    dataset_snapshot_id: str
    dataset_hash: str
    market_structure_config_hash: str = "phase5-internal"
    feature_version: str = "strategy-features-v1"
    backtest_engine_version: str = "research-event-v1"
    execution_model_version: str
    cost_model_version: str
    parameter_set_hash: str
    random_seed: int = 42
    created_at: datetime = Field(default_factory=utc_now)
    created_by: str = "system"
    correlation_id: str

    @field_validator("created_at")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return ensure_utc(value)


class ArtifactReference(ResearchModel):
    artifact_id: str
    artifact_type: str
    path: str
    content_hash: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class DatasetSnapshot(ResearchModel):
    dataset_snapshot_id: str
    symbol: str
    timeframe: str
    bars: list[dict[str, Any]]
    provider: str = "deterministic-fixture"
    adjustment_mode: str = "raw"
    quality_policy: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)

    def dataset_hash(self) -> str:
        return stable_hash({"symbol": self.symbol, "timeframe": self.timeframe, "bars": self.bars, "adjustment_mode": self.adjustment_mode})


class ExecutionModel(ResearchModel):
    execution_model_id: str = "next-bar-conservative-v1"
    version: str = "1.0.0"
    default_order_type: SimulatedOrderType = SimulatedOrderType.MARKET_ON_NEXT_BAR
    fill_policy: FillPolicy = FillPolicy.CONSERVATIVE
    allow_same_bar_fill: bool = False
    entry_delay_bars: int = Field(default=1, ge=1, le=10)


class CostModel(ResearchModel):
    cost_model_id: str = "basic-costs-v1"
    version: str = "1.0.0"
    fixed_commission: float = 0.0
    per_unit_commission: float = 0.0
    percentage_commission: float = 0.0
    spread_bps: float = 0.0
    slippage_bps: float = 0.0
    atr_slippage_multiple: float = 0.0

    def estimate(self, price: float, quantity: float) -> float:
        gross = abs(price * quantity)
        return self.fixed_commission + abs(quantity) * self.per_unit_commission + gross * self.percentage_commission + gross * ((self.spread_bps + self.slippage_bps) / 10_000.0)


class BacktestConfiguration(ResearchModel):
    initial_capital: float = Field(default=100_000.0, gt=0)
    quantity: float = Field(default=1.0, gt=0)
    execution_model: ExecutionModel = Field(default_factory=ExecutionModel)
    cost_model: CostModel = Field(default_factory=CostModel)
    benchmark_symbol: str | None = None
    max_bars: int = Field(default=100_000, ge=10)


class ParameterDefinition(ResearchModel):
    name: str
    path: str
    type: Literal["integer", "float", "decimal", "boolean", "categorical", "time_window", "enumeration"]
    bounds: tuple[float, float] | None = None
    choices: list[Any] = Field(default_factory=list)
    step: float | None = None
    default: Any = None

    @model_validator(mode="after")
    def _valid_space(self) -> "ParameterDefinition":
        if self.type in {"integer", "float", "decimal", "time_window"} and self.bounds is None and not self.choices:
            raise ValueError("numeric parameters require bounds or choices")
        if self.type in {"categorical", "enumeration", "boolean"} and not self.choices:
            if self.type == "boolean":
                self.choices = [False, True]
            else:
                raise ValueError("categorical parameters require choices")
        return self


class ParameterSpace(ResearchModel):
    parameters: list[ParameterDefinition] = Field(default_factory=list)
    max_combinations: int = Field(default=500, ge=1, le=100_000)

    def estimate_size(self) -> int:
        size = 1
        for param in self.parameters:
            size *= len(parameter_values(param))
        return size


def parameter_values(param: ParameterDefinition) -> list[Any]:
    if param.choices:
        return param.choices
    assert param.bounds is not None
    step = param.step or 1
    low, high = param.bounds
    values: list[Any] = []
    current = low
    while current <= high + 1e-12:
        values.append(int(current) if param.type in {"integer", "time_window"} else round(float(current), 10))
        current += step
    return values


class ResearchExperiment(ResearchModel):
    experiment_id: str
    name: str
    objective: str
    hypothesis: str
    strategy_id: str
    strategy_version: str
    dataset_snapshot_id: str
    parameter_space: ParameterSpace = Field(default_factory=ParameterSpace)
    validation_plan: dict[str, Any] = Field(default_factory=dict)
    owner: str = "research"
    status: ResearchStatus = ResearchStatus.DRAFT
    created_at: datetime = Field(default_factory=utc_now)
    immutable: bool = False


class ResearchRun(ResearchModel):
    run_id: str
    job_id: str | None = None
    experiment_id: str | None = None
    status: ResearchStatus = ResearchStatus.QUEUED
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    lineage: LineageRecord
    artifacts: list[ArtifactReference] = Field(default_factory=list)


class SimulatedOrder(ResearchModel):
    simulated_order_id: str
    proposal_id: str
    type: SimulatedOrderType
    side: Literal["buy", "sell"]
    quantity: float
    submitted_at: datetime
    eligible_from: datetime
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: str = "gtc"
    status: str = "submitted"
    filled_quantity: float = 0.0
    average_fill_price: float | None = None
    rejection_reason: str | None = None
    decision_id: str | None = None
    invalidation_price: float | None = None
    target_price: float | None = None


class TradeSimulation(ResearchModel):
    trade_id: str
    decision_id: str
    proposal_id: str
    side: Literal["long", "short"]
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    quantity: float
    gross_pnl: float
    fees: float
    net_pnl: float
    return_pct: float
    exit_reason: str
    ambiguous_fill: bool = False
    evidence_reference: str | None = None


class EquityPoint(ResearchModel):
    timestamp: datetime
    equity: float
    cash: float
    unrealized_pnl: float = 0.0
    exposure: float = 0.0


class DrawdownPeriod(ResearchModel):
    start: datetime
    trough: datetime
    end: datetime | None = None
    drawdown: float
    duration_bars: int


class MetricSet(ResearchModel):
    total_return: float
    annualized_return: float | None = None
    annualized_volatility: float | None = None
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None
    maximum_drawdown: float
    drawdown_duration: int
    win_rate: float | None = None
    loss_rate: float | None = None
    profit_factor: float | None = None
    expectancy: float | None = None
    average_win: float | None = None
    average_loss: float | None = None
    payoff_ratio: float | None = None
    trade_count: int = 0
    exposure_time: float = 0.0
    turnover: float = 0.0
    average_holding_period: float | None = None
    best_trade: float | None = None
    worst_trade: float | None = None
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    warnings: list[str] = Field(default_factory=list)


class BacktestResult(ResearchModel):
    metrics: MetricSet
    trades: list[TradeSimulation]
    equity_curve: list[EquityPoint]
    orders: list[SimulatedOrder]
    warnings: list[str] = Field(default_factory=list)
    attribution: dict[str, Any] = Field(default_factory=dict)


class BacktestRun(ResearchRun):
    configuration: BacktestConfiguration
    result: BacktestResult | None = None


class OptimizationTrial(ResearchModel):
    trial_id: str
    job_id: str
    parameter_set: dict[str, Any]
    parameter_set_hash: str
    status: ResearchStatus
    metrics: MetricSet | None = None
    objective_value: float | None = None
    errors: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class OptimizationJob(ResearchRun):
    method: Literal["grid", "random"] = "grid"
    objective: str = "sharpe_ratio"
    constraints: dict[str, Any] = Field(default_factory=dict)
    parameter_space: ParameterSpace = Field(default_factory=ParameterSpace)
    max_trials: int = Field(default=100, ge=1)
    worker_count: int = Field(default=1, ge=1, le=8)
    trials: list[OptimizationTrial] = Field(default_factory=list)


class OptimizationResult(ResearchModel):
    job_id: str
    ranked_trials: list[OptimizationTrial]
    failed_trials: list[OptimizationTrial]
    pareto_frontier: list[OptimizationTrial] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WalkForwardFold(ResearchModel):
    fold_id: str
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    selected_parameters: dict[str, Any] = Field(default_factory=dict)
    train_metrics: MetricSet | None = None
    validation_metrics: MetricSet | None = None
    degradation: float | None = None


class WalkForwardPlan(ResearchModel):
    train_bars: int = Field(default=40, ge=10)
    validation_bars: int = Field(default=20, ge=5)
    step_bars: int = Field(default=20, ge=1)
    mode: Literal["anchored", "rolling"] = "rolling"
    purge_bars: int = Field(default=0, ge=0)
    embargo_bars: int = Field(default=0, ge=0)


class ValidationScenario(ResearchModel):
    scenario_id: str
    name: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class RobustnessResult(ResearchModel):
    scenario_id: str
    name: str
    metrics: MetricSet
    passed: bool
    warnings: list[str] = Field(default_factory=list)


class ValidationJob(ResearchRun):
    plan: WalkForwardPlan = Field(default_factory=WalkForwardPlan)
    scenarios: list[ValidationScenario] = Field(default_factory=list)
    folds: list[WalkForwardFold] = Field(default_factory=list)
    robustness: list[RobustnessResult] = Field(default_factory=list)


class ValidationResult(ResearchModel):
    validation_job_id: str
    folds: list[WalkForwardFold]
    robustness: list[RobustnessResult]
    passed: bool
    warnings: list[str] = Field(default_factory=list)


class StrategyScorecard(ResearchModel):
    scorecard_id: str
    strategy_id: str
    strategy_version: str
    total_score: float
    components: dict[str, float]
    gates: dict[str, bool]
    passed: bool
    warnings: list[str] = Field(default_factory=list)
    lineage: LineageRecord
    created_at: datetime = Field(default_factory=utc_now)


class ResearchCandidate(ResearchModel):
    candidate_id: str
    strategy_id: str
    strategy_version: str
    selected_parameters: dict[str, Any]
    dataset_scope: dict[str, Any]
    optimization_job_id: str | None = None
    validation_job_id: str | None = None
    scorecard_id: str
    promotion_status: CandidateStatus
    promotion_reasons: list[str] = Field(default_factory=list)
    rejection_reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    approved_at: datetime | None = None
    approved_by: str | None = None
    approval_notes: str | None = None
    stale: bool = False


class ResearchEvent(ResearchModel):
    event_type: str
    event_id: str
    correlation_id: str
    job_id: str | None = None
    strategy_id: str | None = None
    strategy_version: str | None = None
    dataset_snapshot_id: str | None = None
    configuration_hashes: dict[str, str] = Field(default_factory=dict)
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class WorkflowRequest(ResearchModel):
    strategy_id: str = "ema_trend_continuation_v1"
    symbol: str = "TEST"
    timeframe: str = "15m"
    bars: list[dict[str, Any]]
    parameter_space: ParameterSpace = Field(default_factory=ParameterSpace)
    backtest: BacktestConfiguration = Field(default_factory=BacktestConfiguration)
    walk_forward: WalkForwardPlan = Field(default_factory=WalkForwardPlan)
    random_seed: int = 42


class WorkflowResult(ResearchModel):
    experiment: ResearchExperiment
    backtest: BacktestRun
    optimization: OptimizationJob
    validation: ValidationJob
    scorecard: StrategyScorecard
    candidate: ResearchCandidate
    manifest: dict[str, Any]
    events: list[ResearchEvent]
