from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.economic_intelligence import persistence, service
from backend.economic_intelligence.config import EconomicIntelligenceConfig
from backend.economic_intelligence.orm import EconomicEventORM, EconomicEventSnapshotORM
from backend.economic_intelligence.service import EconomicIntelligenceService
from backend.shared.db import Base


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(persistence, "SessionLocal", session_factory)
    return session_factory


def _row(**overrides):
    base = {"provider_event_id": "NFP-1", "raw_name": "Non-Farm Payrolls", "currency": "USD", "impact": "high", "scheduled_at": "2026-08-07T12:30:00Z", "actual_raw": None, "forecast_raw": None, "previous_raw": "150K", "detail_url": None}
    base.update(overrides)
    return base


# --- lifecycle timestamps (24-27) --------------------------------------------------------


def test_forecast_first_seen_timestamp_set_once(monkeypatch):
    session_factory = _session_factory(monkeypatch)
    outcome = persistence.upsert_event(_row(forecast_raw=None))
    with session_factory() as db:
        row = db.get(EconomicEventORM, outcome["internal_id"])
    assert row.forecast_first_seen_at is None
    assert row.lifecycle_status == "SCHEDULED"

    persistence.upsert_event(_row(forecast_raw="185K"))
    with session_factory() as db:
        row = db.get(EconomicEventORM, outcome["internal_id"])
    first_seen = row.forecast_first_seen_at
    assert first_seen is not None
    assert row.lifecycle_status == "FORECAST_AVAILABLE"

    persistence.upsert_event(_row(forecast_raw="185K"))  # unchanged -- first-seen must not move
    with session_factory() as db:
        row = db.get(EconomicEventORM, outcome["internal_id"])
    assert row.forecast_first_seen_at == first_seen


def test_actual_first_seen_timestamp_and_lifecycle_transition(monkeypatch):
    session_factory = _session_factory(monkeypatch)
    outcome = persistence.upsert_event(_row(forecast_raw="185K", actual_raw=None))
    persistence.upsert_event(_row(forecast_raw="185K", actual_raw="210K"))
    with session_factory() as db:
        row = db.get(EconomicEventORM, outcome["internal_id"])
    assert row.actual_first_seen_at is not None
    assert row.lifecycle_status in {"ACTUAL_PUBLISHED", "COMPLETED"}


def test_previous_revision_first_seen_timestamp(monkeypatch):
    session_factory = _session_factory(monkeypatch)
    outcome = persistence.upsert_event(_row(previous_raw="150K"))
    persistence.upsert_event(_row(previous_raw="152K"))  # revised previous value
    with session_factory() as db:
        row = db.get(EconomicEventORM, outcome["internal_id"])
    assert row.previous_revision_first_seen_at is not None


def test_scheduled_time_change_timestamp(monkeypatch):
    session_factory = _session_factory(monkeypatch)
    outcome = persistence.upsert_event(_row(scheduled_at="2026-08-07T12:30:00Z"))
    persistence.upsert_event(_row(scheduled_at="2026-08-07T14:00:00Z"))
    with session_factory() as db:
        row = db.get(EconomicEventORM, outcome["internal_id"])
    assert row.scheduled_time_changed_at is not None


# --- unchanged payload dedup (28) ---------------------------------------------------------


def test_unchanged_payload_does_not_create_duplicate_snapshot(monkeypatch):
    session_factory = _session_factory(monkeypatch)
    outcome = persistence.upsert_event(_row())
    persistence.upsert_event(_row())
    persistence.upsert_event(_row())
    with session_factory() as db:
        snapshots = db.query(EconomicEventSnapshotORM).filter(EconomicEventSnapshotORM.economic_event_id == outcome["internal_id"]).all()
    assert len(snapshots) == 1


# --- fast polling activation window (29, 30) ----------------------------------------------


def test_fast_polling_activates_near_high_impact_event(monkeypatch):
    svc = EconomicIntelligenceService(EconomicIntelligenceConfig(ff_release_fast_poll_enabled=True, ff_release_fast_poll_seconds=45, ff_calendar_refresh_seconds=300))
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(service, "query_events", lambda **kwargs: [{"impact": "high", "is_central_bank_event": False}])
    interval = svc._calendar_interval(now)
    assert interval == 45
    assert svc._fast_poll_active is True


def test_fast_polling_stops_outside_release_window(monkeypatch):
    svc = EconomicIntelligenceService(EconomicIntelligenceConfig(ff_release_fast_poll_enabled=True, ff_release_fast_poll_seconds=45, ff_calendar_refresh_seconds=300))
    svc._fast_poll_active = True
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(service, "query_events", lambda **kwargs: [])
    interval = svc._calendar_interval(now)
    assert interval == 300
    assert svc._fast_poll_active is False


def test_fast_polling_disabled_via_config_always_uses_normal_interval(monkeypatch):
    svc = EconomicIntelligenceService(EconomicIntelligenceConfig(ff_release_fast_poll_enabled=False, ff_calendar_refresh_seconds=300))
    monkeypatch.setattr(service, "query_events", lambda **kwargs: [{"impact": "high", "is_central_bank_event": False}])
    assert svc._calendar_interval(datetime.now(timezone.utc)) == 300


# --- timeline route (31) -------------------------------------------------------------------


def test_event_timeline_returns_ordered_snapshots_and_revisions(monkeypatch):
    _session_factory(monkeypatch)
    outcome = persistence.upsert_event(_row(forecast_raw="185K"))
    persistence.upsert_event(_row(forecast_raw="190K"))
    persistence.upsert_event(_row(forecast_raw="195K"))
    svc = EconomicIntelligenceService()
    result = asyncio.run(svc.event_timeline(outcome["internal_id"]))
    assert result["status"] == "ok"
    assert len(result["snapshots"]) == 3
    assert all(result["snapshots"][i]["observed_at"] <= result["snapshots"][i + 1]["observed_at"] for i in range(len(result["snapshots"]) - 1))
    assert len(result["revisions"]) >= 2
    assert all(r["field_name"] == "forecast_raw" for r in result["revisions"])


def test_event_timeline_not_found(monkeypatch):
    _session_factory(monkeypatch)
    svc = EconomicIntelligenceService()
    result = asyncio.run(svc.event_timeline("nonexistent"))
    assert result["status"] == "not_found"
