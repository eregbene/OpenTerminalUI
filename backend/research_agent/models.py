from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class ResearchAgentMode(str, Enum):
    MANUAL = "MANUAL"
    ASSISTED = "ASSISTED"
    SCHEDULED = "SCHEDULED"
    AUTONOMOUS_RESEARCH = "AUTONOMOUS_RESEARCH"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"


class PolicyStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class PlanStatus(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    PAUSED_BUDGET = "PAUSED_BUDGET"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class ApprovalState(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class TaskType(str, Enum):
    EVIDENCE_COLLECTION = "evidence_collection"
    DATASET_VALIDATION = "dataset_validation"
    BASELINE_BACKTEST = "baseline_backtest"
    PARAMETER_OPTIMIZATION = "parameter_optimization"
    WALK_FORWARD_VALIDATION = "walk_forward_validation"
    MONTE_CARLO_ANALYSIS = "monte_carlo_analysis"
    SCENARIO_ANALYSIS = "scenario_analysis"
    CANDIDATE_COMPARISON = "candidate_comparison"
    ROBUSTNESS_REVIEW = "robustness_review"
    FAILURE_ANALYSIS = "failure_analysis"
    RESEARCH_REPORT_GENERATION = "research_report_generation"


class HypothesisConfidence(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    UNASSESSED = "UNASSESSED"


class FailureClass(str, Enum):
    DATA_FAILURE = "DATA_FAILURE"
    CONFIGURATION_FAILURE = "CONFIGURATION_FAILURE"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    INSUFFICIENT_TRADES = "INSUFFICIENT_TRADES"
    UNSTABLE_PARAMETERS = "UNSTABLE_PARAMETERS"
    OVERFITTING_RISK = "OVERFITTING_RISK"
    REGIME_DEPENDENCE = "REGIME_DEPENDENCE"
    EXCESSIVE_DRAWDOWN = "EXCESSIVE_DRAWDOWN"
    WEAK_OUT_OF_SAMPLE = "WEAK_OUT_OF_SAMPLE"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    POLICY_REJECTION = "POLICY_REJECTION"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class ResearchPolicy:
    policy_id: str
    owner_user_id: str
    workspace_id: str
    status: PolicyStatus = PolicyStatus.DRAFT
    version: int = 1
    allowed_asset_types: tuple[str, ...] = ("EQUITY",)
    allowed_instruments: tuple[str, ...] = ("TEST", "NSE:RELIANCE", "AAPL")
    allowed_strategy_packs: tuple[str, ...] = ("reference",)
    allowed_research_templates: tuple[str, ...] = ("baseline_validation",)
    allowed_timeframes: tuple[str, ...] = ("15m", "1h", "1d")
    allowed_datasets: tuple[str, ...] = ("internal-demo",)
    maximum_jobs_per_day: int = 5
    maximum_concurrent_jobs: int = 1
    maximum_optimization_trials: int = 10
    maximum_validation_folds: int = 5
    maximum_scenarios: int = 5
    maximum_provider_tokens: int = 20000
    maximum_provider_cost: str = "2.00"
    storage_limit_bytes: int = 2_000_000
    approval_required: bool = True
    prohibited_operations: tuple[str, ...] = ("trading", "candidate_promotion", "deployment", "risk_approval", "broker")
    expiry_date: str | None = None
    created_at: str = field(default_factory=lambda: now_iso())
    activated_at: str | None = None
    revoked_at: str | None = None
    content_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = self.__dict__.copy()
        data["status"] = self.status.value
        return data


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
