from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.economic_intelligence import persistence
from backend.economic_intelligence.orm import EconomicEventORM, EconomicEventRevisionORM, EconomicEventSnapshotORM
from backend.shared.db import Base


@pytest.fixture()
def session_local(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[EconomicEventORM.__table__, EconomicEventRevisionORM.__table__, EconomicEventSnapshotORM.__table__])
    from backend.economic_intelligence.orm import EconomicEventDefinitionORM, EconomicNewsClassificationORM, EconomicNewsItemORM, EconomicProviderRunORM, EconomicProviderStateORM, EconomicTradeContextSnapshotORM

    Base.metadata.create_all(engine, tables=[t.__table__ for t in (EconomicEventDefinitionORM, EconomicNewsClassificationORM, EconomicNewsItemORM, EconomicProviderRunORM, EconomicProviderStateORM, EconomicTradeContextSnapshotORM)])
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(persistence, "SessionLocal", session_factory)
    return session_factory


def _row(*, provider_event_id="NFP-2026-08", actual=None, forecast="185K", previous="150K", scheduled="2026-08-07T12:30:00Z", currency="USD", impact="high"):
    return {"provider_event_id": provider_event_id, "raw_name": "Non-Farm Payrolls", "currency": currency, "impact": impact, "scheduled_at": scheduled, "actual_raw": actual, "forecast_raw": forecast, "previous_raw": previous, "detail_url": None}


def test_upsert_event_creates_new_event(session_local):
    outcome = persistence.upsert_event(_row())
    assert outcome["created"] is True
    stored = persistence.get_event(outcome["internal_id"])
    assert stored["currency"] == "USD"
    assert stored["forecast_numeric"] == pytest.approx(185_000.0)


def test_duplicate_ingestion_is_idempotent(session_local):
    first = persistence.upsert_event(_row())
    second = persistence.upsert_event(_row())
    assert second["created"] is False
    assert second["revised_fields"] == []
    assert second["duplicate"] is True
    assert first["internal_id"] == second["internal_id"]


def test_forecast_revision_creates_snapshot_and_revision_row(session_local):
    outcome = persistence.upsert_event(_row(forecast="185K"))
    revised = persistence.upsert_event(_row(forecast="190K"))
    assert "forecast_raw" in revised["revised_fields"]
    with session_local() as db:
        snapshots = db.query(EconomicEventSnapshotORM).filter(EconomicEventSnapshotORM.economic_event_id == outcome["internal_id"]).all()
        revisions = db.query(EconomicEventRevisionORM).filter(EconomicEventRevisionORM.economic_event_id == outcome["internal_id"]).all()
    assert len(snapshots) == 2
    assert any(row.field_name == "forecast_raw" for row in revisions)


def test_actual_first_publication_is_a_revision(session_local):
    outcome = persistence.upsert_event(_row(actual=None))
    published = persistence.upsert_event(_row(actual="210K"))
    assert "actual_raw" in published["revised_fields"]
    stored = persistence.get_event(outcome["internal_id"])
    assert stored["status"] == "released"
    assert stored["actual_numeric"] == pytest.approx(210_000.0)


def test_event_time_revision_creates_revision_row(session_local):
    outcome = persistence.upsert_event(_row(scheduled="2026-08-07T12:30:00Z"))
    revised = persistence.upsert_event(_row(scheduled="2026-08-07T14:00:00Z"))
    assert "scheduled_at_utc" in revised["revised_fields"]
    assert outcome["internal_id"] == revised["internal_id"]


def test_historical_replay_never_uses_snapshots_observed_after_simulated_time(session_local):
    outcome = persistence.upsert_event(_row(forecast="185K"))
    event_id = outcome["internal_id"]
    original_snapshot_time = datetime.now(timezone.utc)
    persistence.upsert_event(_row(forecast="190K"))
    # As-of a time before the revision landed, the point-in-time lookup must return the
    # original snapshot, not the later revision -- this is the no-look-ahead guarantee.
    as_of_result = persistence.latest_snapshot_as_of(event_id, original_snapshot_time)
    assert as_of_result is not None
    assert as_of_result["forecast_raw"] == "185K"


def test_latest_snapshot_as_of_returns_none_before_any_observation(session_local):
    outcome = persistence.upsert_event(_row())
    long_ago = datetime.now(timezone.utc) - timedelta(days=3650)
    assert persistence.latest_snapshot_as_of(outcome["internal_id"], long_ago) is None


def test_upsert_news_item_dedup_by_url(session_local):
    item = {"provider_story_id": None, "headline": "Fed hints at rate cut", "forex_factory_url": "https://www.forexfactory.com/news/12345-fed-hints", "published_at_utc": datetime.now(timezone.utc).isoformat(), "source_name": "Reuters", "category": "central_bank", "related_currencies": ["USD"], "preview": "short preview"}
    first = persistence.upsert_news_item(item)
    second = persistence.upsert_news_item(item)
    assert first["created"] is True
    assert second["created"] is False
    assert second["duplicate"] is True


def test_provider_state_records_health(session_local):
    persistence.save_provider_state("ff_calendar_json", status="HEALTHY", last_successful_sync=datetime.now(timezone.utc), consecutive_failures=0)
    row = persistence.provider_state("ff_calendar_json")
    assert row["status"] == "HEALTHY"
