from __future__ import annotations

from datetime import datetime, timedelta, timezone
import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.decision_context.config import DecisionContextConfig
from backend.decision_context.mapping import affected_currencies, pair_relative_sentiment, symbol_parts
from backend.decision_context.orm import Base
from backend.decision_context.persistence import build_snapshot, persist_events, persist_macro, persist_news, provider_health, save_provider_state
from backend.decision_context.service import DecisionContextService, _importance, _mt5_value, _normalize_mt5_event


@pytest.fixture()
def context_db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    tables = [table for name, table in Base.metadata.tables.items() if name in {"economic_events", "economic_event_revisions", "news_items", "news_clusters", "macro_series_definitions", "macro_observations", "context_provider_states", "decision_context_snapshots"}]
    Base.metadata.create_all(bind=engine, tables=tables)
    testing_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr("backend.decision_context.persistence.SessionLocal", testing_session)
    return testing_session


def test_currency_mapping_and_pair_relative_directionality():
    parts = symbol_parts("EURUSD.a")
    assert parts.canonical_symbol == "EURUSD"
    assert parts.base_currency == "EUR"
    assert parts.quote_currency == "USD"
    assert affected_currencies("ECB and Fed inflation comments") == {"USD": ["fed"], "EUR": ["ecb"]}
    assert pair_relative_sentiment("EUR", "USD", {"EUR": 0.4, "USD": -0.1})["bias"] == "supports_pair"
    assert pair_relative_sentiment("USD", "JPY", {"USD": -0.2, "JPY": 0.3})["bias"] == "weakens_pair"


def test_mt5_calendar_normalization_handles_missing_values_and_importance():
    row = {"id": 7, "currency": "USD", "name": "US CPI", "importance": 3, "time": 1780000000, "actual": 9223372036854775807}
    event = _normalize_mt5_event(row)

    assert event["provider_event_id"] == 7
    assert event["currency"] == "USD"
    assert event["importance"] == "high"
    assert event["actual_value"] is None
    assert _importance("2") == "medium"
    assert _mt5_value(".") is None


def test_high_impact_event_blocks_and_snapshot_is_immutable(context_db):
    now = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)
    persist_events(
        [
            {
                "provider_event_id": "nfp",
                "event_name": "US nonfarm payrolls",
                "currency": "USD",
                "importance": "high",
                "scheduled_at_utc": now + timedelta(minutes=10),
                "received_at": now - timedelta(minutes=5),
                "raw_payload": {"id": "nfp"},
            }
        ],
        ["EURUSD", "GBPUSD"],
    )
    save_provider_state("mt5_calendar", "mt5_calendar_incremental_refresh", status="ok", last_successful_sync=now)

    snapshot = build_snapshot("EURUSD", at=now)
    again = build_snapshot("EURUSD", at=now)

    assert snapshot["context_id"] == again["context_id"]
    assert snapshot["scheduled_event_risk"] == "high_impact_pre_event_block"
    assert "HIGH_IMPACT_EVENT_WINDOW" in snapshot["block_reasons"]
    assert snapshot["upcoming_relevant_events"][0]["affected_symbols"] == ["EURUSD", "GBPUSD"]


def test_news_and_macro_context_are_timestamp_limited(context_db):
    now = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)
    persist_news(
        [
            {"provider": "gdelt", "title": "Fed emergency intervention warning", "publisher": "Example", "article_url": "https://example.test/a", "published_at": now - timedelta(minutes=5), "received_at": now - timedelta(minutes=4), "impact": "high", "mapping_evidence": {"USD": ["fed", "emergency"]}, "raw_payload": {"title": "a"}},
            {"provider": "gdelt", "title": "Fed emergency intervention warning", "publisher": "Other", "article_url": "https://example.test/b", "published_at": now + timedelta(minutes=5), "received_at": now + timedelta(minutes=5), "impact": "high", "mapping_evidence": {"USD": ["fed", "emergency"]}, "raw_payload": {"title": "b"}},
        ],
        ["EURUSD"],
    )
    persist_macro(
        [
            {"series_id": "FEDFUNDS", "name": "Fed Funds", "currency": "USD", "observation_date": now - timedelta(days=30), "value": 5.5, "previous_known_value": 5.25, "received_at": now - timedelta(days=1), "realtime_start": "2026-08-01", "realtime_end": "2026-08-01", "vintage_date": "2026-08-01", "revision_state": "initial", "trend": "rising", "raw_payload": {"value": "5.5"}},
        ]
    )

    snapshot = build_snapshot("EURUSD", at=now)

    assert len(snapshot["headline_clusters"]) == 1
    assert snapshot["quote_macro_context"]["observations"][0]["trend"] == "rising"


def test_strict_context_risk_blocks_unavailable_calendar(context_db):
    service = DecisionContextService(DecisionContextConfig(strict_mode=True))
    risk = asyncio.run(service.context_risk("EURUSD"))

    assert risk["allowed"] is False
    assert "CONTEXT_CALENDAR_UNAVAILABLE" in risk["block_reasons"]


def test_provider_health_does_not_expose_keys(context_db):
    save_provider_state("alpha_vantage", "alpha_vantage_news_refresh", status="authentication_failed", errors=["bad_key"])
    health = provider_health()

    assert health[0]["provider"] == "alpha_vantage"
    assert "api_key" not in str(health).lower()
