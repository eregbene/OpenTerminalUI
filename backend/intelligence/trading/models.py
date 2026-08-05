from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Decision = Literal["LONG", "SHORT", "FLAT", "NO_TRADE"]
EntryType = Literal["MARKET", "LIMIT", "STOP", "NONE"]


class TradeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str
    timeframe: str
    decision: Decision
    entry_type: EntryType
    proposed_entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    confidence: float = Field(ge=0, le=1)
    risk_reward_ratio: float = Field(ge=0)
    risk_percent: float = Field(ge=0)
    strategy: str
    market_regime: str
    reasoning_summary: str
    invalidation_conditions: list[str]
    data_timestamp: datetime
    decision_timestamp: datetime

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        return value.upper().replace("/", "").replace("FX:", "")

    @model_validator(mode="after")
    def validate_geometry(self) -> "TradeDecision":
        if self.decision in {"FLAT", "NO_TRADE"}:
            return self
        if self.entry_type == "NONE":
            raise ValueError("trade decisions require an entry type")
        if self.proposed_entry is None or self.stop_loss is None or self.take_profit is None:
            raise ValueError("trade decisions require entry, stop, and take profit")
        if self.decision == "LONG" and not (self.stop_loss < self.proposed_entry < self.take_profit):
            raise ValueError("LONG geometry invalid")
        if self.decision == "SHORT" and not (self.take_profit < self.proposed_entry < self.stop_loss):
            raise ValueError("SHORT geometry invalid")
        return self


class ProviderTelemetry(BaseModel):
    provider: str
    model: str
    request_id: str | None = None
    latency_ms: float = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0
    hourly_request_count: int = 0
    daily_request_count: int = 0
    response_status: str = "unknown"
    error_category: str | None = None


class ShadowAnalysisResult(BaseModel):
    decision_id: str
    shadow_trade_id: str | None = None
    symbol: str
    timeframe: str
    status: str
    validation_status: str
    risk_status: str
    rejection_reasons: list[str] = Field(default_factory=list)
    decision: TradeDecision | None = None
    provider: ProviderTelemetry | None = None
    shadow_trade_created: bool = False
    context_hash: str | None = None


def decision_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "symbol": {"type": "string"},
            "timeframe": {"type": "string"},
            "decision": {"type": "string", "enum": ["LONG", "SHORT", "FLAT", "NO_TRADE"]},
            "entry_type": {"type": "string", "enum": ["MARKET", "LIMIT", "STOP", "NONE"]},
            "proposed_entry": {"anyOf": [{"type": "number"}, {"type": "null"}]},
            "stop_loss": {"anyOf": [{"type": "number"}, {"type": "null"}]},
            "take_profit": {"anyOf": [{"type": "number"}, {"type": "null"}]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "risk_reward_ratio": {"type": "number", "minimum": 0},
            "risk_percent": {"type": "number", "minimum": 0},
            "strategy": {"type": "string"},
            "market_regime": {"type": "string"},
            "reasoning_summary": {"type": "string"},
            "invalidation_conditions": {"type": "array", "items": {"type": "string"}},
            "data_timestamp": {"type": "string"},
            "decision_timestamp": {"type": "string"},
        },
        "required": [
            "symbol",
            "timeframe",
            "decision",
            "entry_type",
            "proposed_entry",
            "stop_loss",
            "take_profit",
            "confidence",
            "risk_reward_ratio",
            "risk_percent",
            "strategy",
            "market_regime",
            "reasoning_summary",
            "invalidation_conditions",
            "data_timestamp",
            "decision_timestamp",
        ],
    }
