from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from backend.economic_intelligence.event_mapping import is_central_bank_event, normalize_event_name
from backend.economic_intelligence.normalization import normalize_timestamp_to_utc, parse_numeric_value
from backend.economic_intelligence.orm import (
    EconomicEventDefinitionCacheLogORM,
    EconomicEventDefinitionORM,
    EconomicEventORM,
    EconomicEventRevisionORM,
    EconomicEventSnapshotORM,
    EconomicNewsClassificationORM,
    EconomicNewsItemORM,
    EconomicProviderRunORM,
    EconomicProviderStateORM,
    EconomicTradeContextSnapshotORM,
)
from backend.shared.db import SessionLocal


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _comparable(value: Any) -> str:
    """Timezone-normalizing equality helper: SQLite (used in tests) doesn't preserve
    tzinfo on DateTime(timezone=True) round-trips, which would otherwise make a re-fetched
    naive datetime compare unequal to a freshly-parsed aware datetime for the same instant."""
    if isinstance(value, datetime):
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return aware.astimezone(timezone.utc).isoformat()
    return str(value)


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json(val) for key, val in value.items()}
    return value


def _dump(row: Any) -> dict[str, Any]:
    return {col.name: _json(getattr(row, col.name)) for col in row.__table__.columns}


def upsert_event(event: dict[str, Any], *, provider: str = "ff_calendar_json") -> dict[str, Any]:
    """Upsert a normalized Forex Factory calendar row.

    Writes an unconditional snapshot (hash-deduped) and a per-changed-field revision row.
    Never overwrites point-in-time history.
    """
    provider_event_id = str(event.get("provider_event_id") or "")
    raw_name = str(event.get("raw_name") or event.get("event_name") or "Unknown event")
    normalized_name = normalize_event_name(raw_name)
    currency = str(event.get("currency") or "").upper()
    scheduled_at, source_tz = normalize_timestamp_to_utc(event.get("scheduled_at"), source_timezone=event.get("source_timezone"))
    scheduled_at = scheduled_at or utcnow()
    actual_parsed = parse_numeric_value(event.get("actual_raw"))
    forecast_parsed = parse_numeric_value(event.get("forecast_raw"))
    previous_parsed = parse_numeric_value(event.get("previous_raw"))
    payload = {
        "provider_event_id": provider_event_id,
        "normalized_name": normalized_name,
        "raw_name": raw_name,
        "currency": currency,
        "scheduled_at_utc": scheduled_at.isoformat(),
        "actual_raw": event.get("actual_raw"),
        "forecast_raw": event.get("forecast_raw"),
        "previous_raw": event.get("previous_raw"),
        "impact": str(event.get("impact") or "unknown").lower(),
    }
    payload_hash = _hash(payload)
    internal_id = _hash({"provider": provider, "id": provider_event_id})[:40]
    now = utcnow()
    with SessionLocal() as db:
        existing = db.get(EconomicEventORM, internal_id)
        changed_fields: list[tuple[str, Any, Any]] = []
        if existing is None:
            row = EconomicEventORM(id=internal_id)
            row.provider = provider
            row.provider_event_id = provider_event_id
            row.first_seen_at = now
            created = True
        else:
            row = existing
            created = False
            for field, new_value in (
                ("scheduled_at_utc", scheduled_at),
                ("forecast_raw", event.get("forecast_raw")),
                ("previous_raw", event.get("previous_raw")),
                ("actual_raw", event.get("actual_raw")),
            ):
                old_value = getattr(row, field)
                if _comparable(old_value) != _comparable(new_value):
                    changed_fields.append((field, old_value, new_value))
        row.normalized_name = normalized_name
        row.raw_name = raw_name
        row.currency = currency
        row.impact = str(event.get("impact") or "unknown").lower()
        row.scheduled_at_utc = scheduled_at
        row.source_timezone = source_tz
        row.raw_scheduled_at = str(event.get("scheduled_at") or "") or None
        row.actual_raw = str(event.get("actual_raw")) if event.get("actual_raw") is not None else None
        row.forecast_raw = str(event.get("forecast_raw")) if event.get("forecast_raw") is not None else None
        row.previous_raw = str(event.get("previous_raw")) if event.get("previous_raw") is not None else None
        row.actual_numeric = actual_parsed.value
        row.forecast_numeric = forecast_parsed.value
        row.previous_numeric = previous_parsed.value
        row.unit = actual_parsed.unit or forecast_parsed.unit or previous_parsed.unit
        row.detail_url = event.get("detail_url")
        row.status = "released" if actual_parsed.value is not None else "scheduled"
        row.is_central_bank_event = is_central_bank_event(raw_name)
        row.payload_hash = payload_hash
        row.last_seen_at = now

        # Lifecycle tracking (0029): SCHEDULED -> FORECAST_AVAILABLE -> ACTUAL_PENDING ->
        # ACTUAL_PUBLISHED -> REVISED -> COMPLETED, plus first-seen/changed timestamps, each
        # set-once inside the changed-field detection already computed above.
        previous_revised = any(field == "previous_raw" for field, *_ in changed_fields)
        # Use the pre-mutation old_value captured in changed_fields, not `existing`/`row`
        # (the same object once existing is not None -- by this point row.actual_raw has
        # already been overwritten to the new value above, so re-reading existing.actual_raw
        # here would see the NEW value and misclassify a first-ever publication as a revision).
        actual_revised = any(field == "actual_raw" and old_value is not None for field, old_value, _new_value in changed_fields)
        if forecast_parsed.value is not None and row.forecast_first_seen_at is None:
            row.forecast_first_seen_at = now
        if actual_parsed.value is not None and row.actual_first_seen_at is None:
            row.actual_first_seen_at = now
        if previous_revised and row.previous_revision_first_seen_at is None:
            row.previous_revision_first_seen_at = now
        if any(field == "scheduled_at_utc" for field, *_ in changed_fields):
            row.scheduled_time_changed_at = now
        if actual_revised:
            row.lifecycle_status = "REVISED"
        elif actual_parsed.value is not None:
            row.lifecycle_status = "ACTUAL_PUBLISHED"
            if row.completed_at is None and now - scheduled_at >= timedelta(hours=1):
                row.lifecycle_status = "COMPLETED"
                row.completed_at = now
        elif now >= scheduled_at:
            row.lifecycle_status = "ACTUAL_PENDING"
        elif forecast_parsed.value is not None:
            row.lifecycle_status = "FORECAST_AVAILABLE"
        else:
            row.lifecycle_status = "SCHEDULED"

        db.merge(row)
        snapshot_exists = (
            db.scalars(select(EconomicEventSnapshotORM).where(EconomicEventSnapshotORM.economic_event_id == internal_id, EconomicEventSnapshotORM.payload_hash == payload_hash)).first()
            is not None
        )
        if not snapshot_exists:
            db.add(
                EconomicEventSnapshotORM(
                    id=_hash({"event": internal_id, "hash": payload_hash})[:40],
                    economic_event_id=internal_id,
                    scheduled_at_utc=scheduled_at,
                    impact=row.impact,
                    actual_raw=row.actual_raw,
                    forecast_raw=row.forecast_raw,
                    previous_raw=row.previous_raw,
                    observed_at=now,
                    payload_hash=payload_hash,
                    raw_payload_json=event,
                )
            )
        for field, old_value, new_value in changed_fields:
            db.add(
                EconomicEventRevisionORM(
                    id=_hash({"event": internal_id, "field": field, "at": now.isoformat()})[:40],
                    economic_event_id=internal_id,
                    field_name=field,
                    old_value=str(old_value) if old_value is not None else None,
                    new_value=str(new_value) if new_value is not None else None,
                    observed_at=now,
                )
            )
        db.commit()
        return {"internal_id": internal_id, "created": created, "revised_fields": [f for f, *_ in changed_fields], "duplicate": snapshot_exists}


def event_snapshots(economic_event_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        stmt = select(EconomicEventSnapshotORM).where(EconomicEventSnapshotORM.economic_event_id == economic_event_id).order_by(EconomicEventSnapshotORM.observed_at.asc()).limit(max(1, min(2000, limit)))
        return [_dump(row) for row in db.scalars(stmt).all()]


def event_revisions(economic_event_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        stmt = select(EconomicEventRevisionORM).where(EconomicEventRevisionORM.economic_event_id == economic_event_id).order_by(EconomicEventRevisionORM.observed_at.asc()).limit(max(1, min(2000, limit)))
        return [_dump(row) for row in db.scalars(stmt).all()]


def latest_snapshot_as_of(economic_event_id: str, as_of: datetime) -> dict[str, Any] | None:
    """Point-in-time-correct lookup: the latest snapshot whose observed_at <= as_of.

    Every historical-replay/backtest caller must go through this helper (not the live
    EconomicEventORM row) to avoid look-ahead bias from later revisions.
    """
    as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=timezone.utc)
    with SessionLocal() as db:
        row = (
            db.scalars(
                select(EconomicEventSnapshotORM)
                .where(EconomicEventSnapshotORM.economic_event_id == economic_event_id, EconomicEventSnapshotORM.observed_at <= as_of)
                .order_by(EconomicEventSnapshotORM.observed_at.desc())
                .limit(1)
            ).first()
        )
        return _dump(row) if row else None


def query_events(*, currencies: list[str] | None = None, start: datetime | None = None, end: datetime | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        stmt = select(EconomicEventORM).order_by(EconomicEventORM.scheduled_at_utc.asc()).limit(max(1, min(1000, limit)))
        if currencies:
            stmt = stmt.where(EconomicEventORM.currency.in_([c.upper() for c in currencies]))
        if start:
            stmt = stmt.where(EconomicEventORM.scheduled_at_utc >= start)
        if end:
            stmt = stmt.where(EconomicEventORM.scheduled_at_utc <= end)
        return [_dump(row) for row in db.scalars(stmt).all()]


def get_event(event_id: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        row = db.get(EconomicEventORM, event_id)
        return _dump(row) if row else None


def historical_surprises(normalized_name: str, currency: str, *, before: datetime | None = None, limit: int = 60) -> list[float]:
    with SessionLocal() as db:
        stmt = (
            select(EconomicEventORM)
            .where(EconomicEventORM.normalized_name == normalized_name, EconomicEventORM.currency == currency.upper(), EconomicEventORM.actual_numeric.is_not(None), EconomicEventORM.forecast_numeric.is_not(None))
            .order_by(EconomicEventORM.scheduled_at_utc.desc())
            .limit(limit)
        )
        if before:
            stmt = stmt.where(EconomicEventORM.scheduled_at_utc < before)
        rows = db.scalars(stmt).all()
        return [float(row.actual_numeric) - float(row.forecast_numeric) for row in rows if row.actual_numeric is not None and row.forecast_numeric is not None]


def upsert_definition(definition: dict[str, Any]) -> dict[str, Any]:
    normalized_name = normalize_event_name(definition.get("normalized_name") or definition.get("event_name") or "")
    if not normalized_name:
        return {"status": "skipped_empty_name"}
    source_hash = _hash({k: v for k, v in definition.items() if k != "normalized_name"})
    now = utcnow()
    with SessionLocal() as db:
        existing = db.query(EconomicEventDefinitionORM).filter(EconomicEventDefinitionORM.normalized_name == normalized_name).first()
        if existing and existing.source_hash == source_hash:
            return {"status": "unchanged", "normalized_name": normalized_name}
        row = existing or EconomicEventDefinitionORM(id=_hash({"name": normalized_name})[:40], normalized_name=normalized_name, first_seen_at=now)
        row.source_name = definition.get("source_name")
        row.source_url = definition.get("source_url")
        row.description = definition.get("description")
        row.why_traders_care = definition.get("why_traders_care")
        row.usual_effect = definition.get("usual_effect")
        row.frequency = definition.get("frequency")
        row.next_release = definition.get("next_release")
        row.derived_via = definition.get("derived_via")
        row.higher_is_positive = definition.get("higher_is_positive")
        row.affected_assets_json = definition.get("affected_assets") or []
        row.default_blackout_before_minutes = definition.get("default_blackout_before_minutes")
        row.default_blackout_after_minutes = definition.get("default_blackout_after_minutes")
        row.last_refreshed_at = now
        row.source_hash = source_hash
        db.merge(row)
        db.commit()
        return {"status": "created" if existing is None else "updated", "normalized_name": normalized_name}


def definition_for(normalized_name: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        row = db.query(EconomicEventDefinitionORM).filter(EconomicEventDefinitionORM.normalized_name == normalized_name).first()
        return _dump(row) if row else None


def is_definition_stale(normalized_name: str, *, stale_after_days: int) -> bool:
    definition = definition_for(normalized_name)
    if definition is None:
        return True
    last_refreshed = _dt(definition.get("last_refreshed_at"))
    if last_refreshed is None:
        return True
    return utcnow() - last_refreshed > timedelta(days=stale_after_days)


def log_definition_cache_event(normalized_name: str, outcome: str, *, reason: str | None = None) -> None:
    """outcome: hit|miss|refreshed|failed."""
    now = utcnow()
    with SessionLocal() as db:
        db.add(EconomicEventDefinitionCacheLogORM(id=_hash({"name": normalized_name, "outcome": outcome, "at": now.isoformat()})[:40], normalized_name=normalized_name, outcome=outcome, reason=reason, observed_at=now))
        db.commit()


def definition_cache_log(*, normalized_name: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        stmt = select(EconomicEventDefinitionCacheLogORM).order_by(EconomicEventDefinitionCacheLogORM.observed_at.desc()).limit(max(1, min(1000, limit)))
        if normalized_name:
            stmt = stmt.where(EconomicEventDefinitionCacheLogORM.normalized_name == normalized_name)
        return [_dump(row) for row in db.scalars(stmt).all()]


def upsert_news_item(item: dict[str, Any]) -> dict[str, Any]:
    """Dedup precedence: stable story ID -> canonical URL -> content hash -> normalized headline+time."""
    headline = str(item.get("headline") or "").strip()
    normalized_headline = " ".join(headline.lower().split())
    canonical_url = str(item.get("forex_factory_url") or item.get("source_url") or "").split("?")[0].lower()
    story_id = str(item.get("provider_story_id") or "")
    published = _dt(item.get("published_at_utc")) or utcnow()
    fingerprint_basis = story_id or canonical_url or f"{normalized_headline}|{published.isoformat()}"
    content_hash = _hash({"basis": fingerprint_basis, "headline": normalized_headline})
    now = utcnow()
    with SessionLocal() as db:
        existing = db.query(EconomicNewsItemORM).filter(EconomicNewsItemORM.content_hash == content_hash).first()
        row = existing or EconomicNewsItemORM(id=_hash({"hash": content_hash})[:40], first_seen_at=now)
        row.provider_story_id = story_id or None
        row.headline = headline
        row.normalized_headline = normalized_headline
        row.published_at_utc = published
        row.source_name = item.get("source_name")
        row.source_url = item.get("source_url")
        row.forex_factory_url = item.get("forex_factory_url")
        row.category = item.get("category")
        row.related_currencies_json = item.get("related_currencies") or []
        row.provider_impact = item.get("provider_impact")
        row.preview = item.get("preview")
        row.content_hash = content_hash
        row.last_seen_at = now
        db.merge(row)
        db.commit()
        return {"id": row.id, "created": existing is None, "duplicate": existing is not None}


def query_news(*, currencies: list[str] | None = None, max_age_minutes: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        stmt = select(EconomicNewsItemORM).order_by(EconomicNewsItemORM.published_at_utc.desc()).limit(max(1, min(500, limit)))
        if max_age_minutes:
            stmt = stmt.where(EconomicNewsItemORM.published_at_utc >= utcnow() - timedelta(minutes=max_age_minutes))
        rows = db.scalars(stmt).all()
        if currencies:
            wanted = {c.upper() for c in currencies}
            rows = [row for row in rows if wanted.intersection(set(row.related_currencies_json or []))]
        return [_dump(row) for row in rows]


def save_news_classification(news_item_id: str, classification: dict[str, Any]) -> None:
    with SessionLocal() as db:
        db.add(
            EconomicNewsClassificationORM(
                id=_hash({"news": news_item_id, "at": utcnow().isoformat()})[:40],
                news_item_id=news_item_id,
                model_provider=classification.get("model_provider") or "openai",
                model_name=classification.get("model_name"),
                affected_currencies_json=classification.get("affected_currencies") or [],
                affected_symbols_json=classification.get("affected_symbols") or [],
                directional_bias_json=classification.get("directional_bias") or {},
                urgency=classification.get("urgency"),
                risk_level=classification.get("risk_level"),
                confidence=classification.get("confidence"),
                recommended_constraints_json=classification.get("recommended_constraints") or [],
                raw_model_response_json=classification.get("raw") or {},
            )
        )
        db.commit()


def save_trade_context_snapshot(snapshot: dict[str, Any]) -> str:
    snapshot_id = _hash({"cycle": snapshot.get("trading_cycle_id"), "symbol": snapshot.get("symbol"), "at": utcnow().isoformat()})[:40]
    with SessionLocal() as db:
        db.add(
            EconomicTradeContextSnapshotORM(
                id=snapshot_id,
                trading_cycle_id=snapshot.get("trading_cycle_id"),
                strategy_signal_id=snapshot.get("strategy_signal_id"),
                symbol=str(snapshot.get("symbol") or "").upper(),
                direction=snapshot.get("direction"),
                calendar_context_json=snapshot.get("calendar_context") or {},
                news_context_json=snapshot.get("news_context") or {},
                openai_context_json=snapshot.get("openai_context") or {},
                deterministic_decision=str(snapshot.get("deterministic_decision") or "ALLOW"),
                reason_codes_json=snapshot.get("reason_codes") or [],
                economic_guard_mode=snapshot.get("economic_guard_mode"),
                shadow_decision=snapshot.get("shadow_decision"),
                effective_decision=snapshot.get("effective_decision"),
                execution_changed_by_economic=bool(snapshot.get("execution_changed_by_economic")),
                nearest_event_id=snapshot.get("nearest_event_id"),
                minutes_to_event=snapshot.get("minutes_to_event"),
                provider_freshness_json=snapshot.get("provider_freshness") or {},
                spread_at_evaluation=snapshot.get("spread_at_evaluation"),
                linked_execution_id=snapshot.get("linked_execution_id"),
            )
        )
        db.commit()
    return snapshot_id


def get_trade_context_snapshot(snapshot_id: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        row = db.get(EconomicTradeContextSnapshotORM, snapshot_id)
        return _dump(row) if row else None


def query_shadow_decisions(*, symbol: str | None = None, mode: str | None = None, start: datetime | None = None, end: datetime | None = None, limit: int = 200) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        stmt = select(EconomicTradeContextSnapshotORM).order_by(EconomicTradeContextSnapshotORM.created_at.desc()).limit(max(1, min(2000, limit)))
        if symbol:
            stmt = stmt.where(EconomicTradeContextSnapshotORM.symbol == symbol.upper())
        if mode:
            stmt = stmt.where(EconomicTradeContextSnapshotORM.economic_guard_mode == mode)
        if start:
            stmt = stmt.where(EconomicTradeContextSnapshotORM.created_at >= start)
        if end:
            stmt = stmt.where(EconomicTradeContextSnapshotORM.created_at <= end)
        return [_dump(row) for row in db.scalars(stmt).all()]


def unlinked_shadow_decisions(*, limit: int = 100) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        stmt = (
            select(EconomicTradeContextSnapshotORM)
            .where(EconomicTradeContextSnapshotORM.linked_execution_id.is_not(None), EconomicTradeContextSnapshotORM.order_outcome.is_(None))
            .order_by(EconomicTradeContextSnapshotORM.created_at.desc())
            .limit(max(1, min(500, limit)))
        )
        return [_dump(row) for row in db.scalars(stmt).all()]


def record_order_outcome(snapshot_id: str, outcome: dict[str, Any]) -> bool:
    with SessionLocal() as db:
        row = db.get(EconomicTradeContextSnapshotORM, snapshot_id)
        if row is None:
            return False
        row.order_outcome = outcome
        db.commit()
        return True


def link_snapshot_execution(snapshot_id: str, execution_idempotency_key: str) -> bool:
    with SessionLocal() as db:
        row = db.get(EconomicTradeContextSnapshotORM, snapshot_id)
        if row is None:
            return False
        row.linked_execution_id = execution_idempotency_key
        db.commit()
        return True


def record_provider_run(provider_name: str, *, started_at: datetime, finished_at: datetime, status: str, records_received: int = 0, records_inserted: int = 0, records_updated: int = 0, latency_ms: float | None = None, failure_reason: str | None = None, metadata: dict[str, Any] | None = None) -> None:
    freshness = (finished_at - started_at).total_seconds()
    with SessionLocal() as db:
        db.add(
            EconomicProviderRunORM(
                id=_hash({"provider": provider_name, "started_at": started_at.isoformat()})[:40],
                provider_name=provider_name,
                started_at=started_at,
                finished_at=finished_at,
                status=status,
                records_received=records_received,
                records_inserted=records_inserted,
                records_updated=records_updated,
                latency_ms=latency_ms,
                freshness_seconds=freshness,
                failure_reason=failure_reason,
                metadata_json=metadata or {},
            )
        )
        db.commit()


def save_provider_state(provider: str, **fields: Any) -> None:
    with SessionLocal() as db:
        row = db.get(EconomicProviderStateORM, provider)
        if row is None:
            row = EconomicProviderStateORM(provider=provider)
            db.add(row)
        for key, value in fields.items():
            if hasattr(row, key):
                setattr(row, key, value)
        row.updated_at = utcnow()
        db.commit()


def provider_state(provider: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        row = db.get(EconomicProviderStateORM, provider)
        return _dump(row) if row else None


def all_provider_states() -> list[dict[str, Any]]:
    with SessionLocal() as db:
        return [_dump(row) for row in db.scalars(select(EconomicProviderStateORM)).all()]


def backfill_checkpoint(provider_name: str) -> dict[str, Any] | None:
    with SessionLocal() as db:
        row = (
            db.scalars(select(EconomicProviderRunORM).where(EconomicProviderRunORM.provider_name == provider_name, EconomicProviderRunORM.status == "checkpoint").order_by(EconomicProviderRunORM.started_at.desc()).limit(1)).first()
        )
        return _dump(row) if row else None
