from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class EconomicEventOut(BaseModel):
    id: str
    normalized_name: str
    raw_name: str
    currency: str
    impact: str
    scheduled_at_utc: datetime | str
    actual_raw: str | None = None
    forecast_raw: str | None = None
    previous_raw: str | None = None
    actual_numeric: float | None = None
    forecast_numeric: float | None = None
    previous_numeric: float | None = None
    status: str
    is_central_bank_event: bool = False


class EconomicNewsItemOut(BaseModel):
    """Metadata only -- intentionally has no full-article-body field, so a route can never
    leak complete third-party article content even if the caller passes extra data."""

    id: str
    headline: str
    forex_factory_url: str | None = None
    published_at_utc: datetime | str | None = None
    source_name: str | None = None
    category: str | None = None
    related_currencies_json: list[str] = []
    provider_impact: str | None = None
    preview: str | None = None


class SyncRequest(BaseModel):
    jobs: list[str] | None = None


class BackfillRequest(BaseModel):
    start_month: str
    end_month: str
    resume_from: str | None = None


class EvaluateRequest(BaseModel):
    symbol: str
    direction: str = "LONG"


class HealthResponse(BaseModel):
    items: list[dict[str, Any]]
    keys_exposed: bool = False


class ShadowReplayRequest(BaseModel):
    snapshot_id: str


class DryRunSyntheticProviderState(BaseModel):
    calendar_state: str | None = None  # HEALTHY|STALE|DEGRADED|UNAVAILABLE|SCHEMA_CHANGED
    news_state: str | None = None


class DryRunSyntheticEvent(BaseModel):
    currency: str
    impact: str = "high"
    minutes_from_now: float = 10.0
    is_central_bank_event: bool = False
    raw_name: str = "Synthetic Event"


class DryRunSyntheticNews(BaseModel):
    risk_level: str = "low"
    confidence: float = 0.9
    urgency: str = "low"


class DryRunEvaluateRequest(BaseModel):
    symbol: str
    direction: str = "LONG"
    strategy: str | None = None
    volume: float | None = None
    entry: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    decision_time: datetime | None = None
    synthetic_provider_state: DryRunSyntheticProviderState | None = None
    synthetic_events: list[DryRunSyntheticEvent] | None = None
    synthetic_news: DryRunSyntheticNews | None = None
    synthetic_openai_advisory: dict[str, Any] | None = None
    spread: float | None = None
