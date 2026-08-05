from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from backend.decision_context.config import decision_context_config
from backend.decision_context.mapping import affected_symbols, pair_relative_sentiment, symbol_parts
from backend.decision_context.orm import (
    ContextProviderStateORM,
    DecisionContextSnapshotORM,
    EconomicEventORM,
    EconomicEventRevisionORM,
    MacroObservationORM,
    MacroSeriesDefinitionORM,
    NewsClusterORM,
    NewsItemORM,
)
from backend.shared.db import SessionLocal


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def save_provider_state(provider: str, job_type: str, **fields: Any) -> None:
    with SessionLocal() as db:
        row = db.get(ContextProviderStateORM, {"provider": provider, "job_type": job_type})
        if row is None:
            row = ContextProviderStateORM(provider=provider, job_type=job_type)
            db.add(row)
        for key, value in fields.items():
            if hasattr(row, key):
                setattr(row, key, value)
        row.updated_at = utcnow()
        db.commit()


def provider_health() -> list[dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.scalars(select(ContextProviderStateORM)).all()
        return [_dump(row) for row in rows]


def persist_events(events: list[dict[str, Any]], enabled_symbols: list[str]) -> dict[str, int]:
    parts = [symbol_parts(symbol) for symbol in enabled_symbols]
    created = updated = duplicates = 0
    with SessionLocal() as db:
        for event in events:
            raw_hash = _hash(event.get("raw_payload") or event)
            event_id = str(event.get("provider_event_id") or raw_hash[:32])
            value_id = str(event.get("provider_value_id") or "")
            scheduled = _dt(event.get("scheduled_at_utc")) or utcnow()
            internal_id = _hash({"provider": "mt5_calendar", "event_id": event_id, "value_id": value_id, "scheduled": scheduled.isoformat()})[:40]
            affected = affected_symbols({str(event.get("currency") or "").upper()}, parts)
            existing = db.get(EconomicEventORM, internal_id)
            payload = {
                "internal_id": internal_id,
                "provider": "mt5_calendar",
                "provider_event_id": event_id,
                "provider_value_id": value_id or None,
                "calendar_change_id": str(event.get("calendar_change_id") or "") or None,
                "event_name": str(event.get("event_name") or event.get("name") or "Unknown event"),
                "event_code": event.get("event_code"),
                "country": event.get("country"),
                "country_code": event.get("country_code"),
                "currency": str(event.get("currency") or "").upper(),
                "event_category": event.get("event_category"),
                "importance": str(event.get("importance") or "unknown").lower(),
                "scheduled_at_source": _dt(event.get("scheduled_at_source")),
                "source_timezone": event.get("source_timezone"),
                "scheduled_at_utc": scheduled,
                "period": event.get("period"),
                "actual_value": _num(event.get("actual_value")),
                "forecast_value": _num(event.get("forecast_value")),
                "previous_value": _num(event.get("previous_value")),
                "revised_previous_value": _num(event.get("revised_previous_value")),
                "value_unit": event.get("value_unit"),
                "multiplier": _num(event.get("multiplier")),
                "status": str(event.get("status") or "unknown").lower(),
                "affected_symbols": affected,
                "received_at": _dt(event.get("received_at")) or utcnow(),
                "raw_payload_hash": raw_hash,
                "raw_payload": _safe(event.get("raw_payload") or event),
            }
            if existing is None:
                db.add(EconomicEventORM(**payload))
                created += 1
            elif existing.raw_payload_hash != raw_hash:
                db.add(EconomicEventRevisionORM(revision_id=f"{internal_id}:{raw_hash[:16]}", event_internal_id=internal_id, provider="mt5_calendar", actual_value=payload["actual_value"], forecast_value=payload["forecast_value"], previous_value=payload["previous_value"], revised_previous_value=payload["revised_previous_value"], status=payload["status"], raw_payload_hash=raw_hash, raw_payload=payload["raw_payload"]))
                for key, value in payload.items():
                    setattr(existing, key, value)
                updated += 1
            else:
                duplicates += 1
        db.commit()
    return {"created": created, "updated": updated, "duplicates": duplicates}


def persist_news(items: list[dict[str, Any]], enabled_symbols: list[str]) -> dict[str, int]:
    parts = [symbol_parts(symbol) for symbol in enabled_symbols]
    created = updated = duplicates = 0
    with SessionLocal() as db:
        for item in items:
            raw_hash = _hash(item.get("raw_payload") or item)
            fingerprint = _fingerprint(item.get("title"), item.get("article_url"), item.get("publisher"))
            currencies = sorted((item.get("mapping_evidence") or {}).keys())
            symbols = affected_symbols(set(currencies), parts)
            internal_id = _hash({"provider": item.get("provider"), "fingerprint": fingerprint})[:40]
            cluster_id = _hash({"title": _norm_title(item.get("title")), "currencies": currencies})[:40]
            sentiment_score = _num(item.get("provider_sentiment_score"))
            normalized = {currency: sentiment_score for currency in currencies if sentiment_score is not None}
            payload = {
                "internal_id": internal_id,
                "provider": item.get("provider") or "unknown",
                "provider_article_id": item.get("provider_article_id"),
                "publisher": item.get("publisher"),
                "title": str(item.get("title") or ""),
                "permitted_summary": item.get("permitted_summary"),
                "article_url": item.get("article_url"),
                "published_at": _dt(item.get("published_at")),
                "received_at": _dt(item.get("received_at")) or utcnow(),
                "language": item.get("language"),
                "countries": [],
                "currencies": currencies,
                "affected_symbols": symbols,
                "topics": item.get("topics") or [],
                "event_type": item.get("event_type"),
                "provider_sentiment": item.get("provider_sentiment"),
                "provider_sentiment_score": sentiment_score,
                "normalized_currency_sentiment": normalized,
                "normalized_sentiment_score": sentiment_score,
                "relevance_score": 1.0 if currencies else 0.0,
                "impact": item.get("impact") or "unknown",
                "freshness": "fresh",
                "duplicate_group_id": cluster_id,
                "content_fingerprint": fingerprint,
                "raw_payload_hash": raw_hash,
                "raw_payload": _safe(item.get("raw_payload") or item),
            }
            existing = db.get(NewsItemORM, internal_id)
            if existing is None:
                db.add(NewsItemORM(**payload))
                created += 1
            elif existing.raw_payload_hash != raw_hash:
                for key, value in payload.items():
                    setattr(existing, key, value)
                updated += 1
            else:
                duplicates += 1
            cluster = db.get(NewsClusterORM, cluster_id)
            if cluster is None:
                db.add(NewsClusterORM(cluster_id=cluster_id, canonical_headline=payload["title"], earliest_published_at=payload["published_at"], earliest_received_at=payload["received_at"], latest_update_at=payload["received_at"], providers=[payload["provider"]], publishers=[payload["publisher"]] if payload["publisher"] else [], currencies=currencies, affected_symbols=symbols, impact=payload["impact"], normalized_sentiment=normalized, relevance_evidence={"matched_terms": item.get("mapping_evidence") or {}}))
                db.flush()
            else:
                cluster.latest_update_at = max(_dt(cluster.latest_update_at) or payload["received_at"], payload["received_at"])
                cluster.providers = sorted(set(cluster.providers + [payload["provider"]]))
                cluster.publishers = sorted(set(cluster.publishers + ([payload["publisher"]] if payload["publisher"] else [])))
                cluster.duplicate_count += 1 if existing is None else 0
                cluster.currencies = sorted(set(cluster.currencies + currencies))
                cluster.affected_symbols = sorted(set(cluster.affected_symbols + symbols))
        db.commit()
    return {"created": created, "updated": updated, "duplicates": duplicates}


def persist_macro(rows: list[dict[str, Any]]) -> dict[str, int]:
    created = updated = duplicates = 0
    seen_series: set[str] = set()
    with SessionLocal() as db:
        for row in rows:
            if row["series_id"] not in seen_series:
                now = utcnow()
                db.merge(MacroSeriesDefinitionORM(series_id=row["series_id"], currency=row["currency"], name=row["name"], provider="fred", enabled=True, created_at=now, updated_at=now))
                db.flush()
                seen_series.add(row["series_id"])
            obs_date = _dt(row.get("observation_date")) or utcnow()
            observation_id = _hash({"series": row["series_id"], "date": obs_date.isoformat(), "vintage": row.get("vintage_date")})[:40]
            raw_hash = _hash(row.get("raw_payload") or row)
            existing = db.get(MacroObservationORM, observation_id)
            payload = {key: value for key, value in row.items() if key != "name"}
            payload.update({"observation_id": observation_id, "observation_date": obs_date, "raw_payload_hash": raw_hash, "raw_payload": _safe(row.get("raw_payload") or row)})
            if existing is None:
                db.add(MacroObservationORM(**payload))
                created += 1
            elif existing.raw_payload_hash != raw_hash:
                for key, value in payload.items():
                    setattr(existing, key, value)
                updated += 1
            else:
                duplicates += 1
        db.commit()
    return {"created": created, "updated": updated, "duplicates": duplicates}


def query_events(symbol: str | None = None, currency: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    with SessionLocal() as db:
        stmt = select(EconomicEventORM).order_by(EconomicEventORM.scheduled_at_utc.asc()).limit(limit)
        if currency:
            stmt = stmt.where(EconomicEventORM.currency == currency.upper())
        rows = db.scalars(stmt).all()
        if symbol:
            pair = symbol_parts(symbol).canonical_symbol
            rows = [row for row in rows if pair in (row.affected_symbols or [])]
        return [_dump(row) for row in rows]


def build_snapshot(symbol: str, *, at: datetime | None = None, technical_data_timestamp: datetime | None = None) -> dict[str, Any]:
    at = at or utcnow()
    parts = symbol_parts(symbol)
    with SessionLocal() as db:
        provider_rows = db.scalars(select(ContextProviderStateORM)).all()
        events = db.scalars(select(EconomicEventORM).where(EconomicEventORM.currency.in_([parts.base_currency, parts.quote_currency])).order_by(EconomicEventORM.scheduled_at_utc.asc()).limit(50)).all()
        news = db.scalars(select(NewsClusterORM).where(NewsClusterORM.earliest_received_at <= at).order_by(NewsClusterORM.latest_update_at.desc()).limit(50)).all()
        macro = db.scalars(select(MacroObservationORM).where(MacroObservationORM.currency.in_([parts.base_currency, parts.quote_currency]), MacroObservationORM.received_at <= at).order_by(MacroObservationORM.observation_date.desc()).limit(50)).all()
    relevant_events = [_dump(row) for row in events if (_dt(row.received_at) or at) <= at and (_dt(row.scheduled_at_utc) or at) >= at - timedelta(hours=1)]
    event_state, event_warnings, event_blocks, active_window = _event_risk(relevant_events, at)
    clusters = [_dump(row) for row in news if parts.canonical_symbol in (row.affected_symbols or []) or parts.base_currency in (row.currencies or []) or parts.quote_currency in (row.currencies or [])][:10]
    headline_state, headline_warnings, headline_blocks = _headline_risk(clusters)
    sentiment = _currency_sentiment(clusters)
    relative = pair_relative_sentiment(parts.base_currency, parts.quote_currency, sentiment)
    macro_rows = [_dump(row) for row in macro]
    coverage = {row.provider: row.status for row in provider_rows}
    calendar_freshness = _freshness(next((row for row in provider_rows if row.provider == "mt5_calendar"), None), 180)
    warnings = event_warnings + headline_warnings
    blocks = event_blocks + headline_blocks
    config = decision_context_config()
    if calendar_freshness != "fresh":
        reason = f"CONTEXT_CALENDAR_{calendar_freshness.upper()}"
        if config.strict_mode and reason not in blocks:
            blocks.append(reason)
        else:
            warnings.append(f"calendar_{calendar_freshness}")
    combined = "blocked_scheduled_event" if event_blocks else "blocked_high_news_risk" if headline_blocks else "acknowledgement_required" if warnings else "allowed"
    snapshot = {
        "context_id": _hash({"symbol": parts.canonical_symbol, "at": at.isoformat(), "events": [e["internal_id"] for e in relevant_events], "clusters": [c["cluster_id"] for c in clusters]})[:40],
        "symbol": parts.canonical_symbol,
        "base_currency": parts.base_currency,
        "quote_currency": parts.quote_currency,
        "created_at": at,
        "valid_until": at + timedelta(minutes=5),
        "technical_data_timestamp": technical_data_timestamp,
        "mt5_calendar_freshness": calendar_freshness,
        "provider_coverage": coverage,
        "upcoming_relevant_events": relevant_events[:10],
        "active_event_window": active_window,
        "headline_clusters": clusters,
        "base_sentiment": {"currency": parts.base_currency, "score": sentiment.get(parts.base_currency, 0)},
        "quote_sentiment": {"currency": parts.quote_currency, "score": sentiment.get(parts.quote_currency, 0)},
        "relative_sentiment": relative,
        "conflicting_sentiment": bool(relative["bias"] != "neutral" and clusters),
        "base_macro_context": {"currency": parts.base_currency, "observations": [m for m in macro_rows if m["currency"] == parts.base_currency][:5]},
        "quote_macro_context": {"currency": parts.quote_currency, "observations": [m for m in macro_rows if m["currency"] == parts.quote_currency][:5]},
        "scheduled_event_risk": event_state,
        "headline_risk": headline_state,
        "combined_context_risk_state": combined,
        "warnings": warnings,
        "block_reasons": blocks,
        "acknowledgement_requirements": ["CONTEXT_ACK_REQUIRED"] if warnings and not blocks else [],
        "evidence": {"events": [e["internal_id"] for e in relevant_events[:10]], "clusters": [c["cluster_id"] for c in clusters[:10]], "macro_series": sorted({m["series_id"] for m in macro_rows})},
    }
    with SessionLocal() as db:
        if db.get(DecisionContextSnapshotORM, snapshot["context_id"]) is None:
            db.add(DecisionContextSnapshotORM(**snapshot))
            db.commit()
    return _json(snapshot)


def historical_context(symbol: str, timestamp: datetime) -> dict[str, Any]:
    return build_snapshot(symbol, at=timestamp)


def _event_risk(events: list[dict[str, Any]], at: datetime) -> tuple[str, list[str], list[str], dict[str, Any] | None]:
    warnings: list[str] = []
    blocks: list[str] = []
    active: dict[str, Any] | None = None
    for event in events:
        scheduled = _dt(event["scheduled_at_utc"])
        if not scheduled:
            continue
        minutes = (scheduled - at).total_seconds() / 60
        importance = event.get("importance")
        if importance == "high" and -15 <= minutes <= 30:
            reason = "HIGH_IMPACT_EVENT_WINDOW"
            blocks.append(reason)
            active = {"reason": reason, "event": event, "minutes_to_event": minutes}
        elif importance == "medium" and -10 <= minutes <= 20:
            warnings.append("MEDIUM_IMPACT_EVENT_ACK_REQUIRED")
    if blocks:
        return "high_impact_pre_event_block", warnings, blocks, active
    if warnings:
        return "upcoming_medium_impact", warnings, blocks, active
    return "calendar_normal", warnings, blocks, active


def _headline_risk(clusters: list[dict[str, Any]]) -> tuple[str, list[str], list[str]]:
    if any(row.get("impact") == "high" and (row.get("duplicate_count") or 1) >= 2 for row in clusters):
        return "high_news_risk", [], ["HIGH_NEWS_RISK"]
    if any(row.get("impact") in {"high", "medium"} for row in clusters):
        return "elevated_news_risk", ["ELEVATED_NEWS_RISK_ACK_REQUIRED"], []
    return "normal", [], []


def _currency_sentiment(clusters: list[dict[str, Any]]) -> dict[str, float]:
    scores: dict[str, list[float]] = {}
    for cluster in clusters:
        for currency, score in (cluster.get("normalized_sentiment") or {}).items():
            if score is not None:
                scores.setdefault(currency, []).append(float(score))
    return {currency: sum(values) / len(values) for currency, values in scores.items() if values}


def _freshness(row: ContextProviderStateORM | None, stale_after_seconds: int) -> str:
    if row is None:
        return "unavailable"
    if row.status not in {"ok", "degraded"}:
        return row.status
    if not row.last_successful_sync:
        return "unavailable"
    last = row.last_successful_sync if row.last_successful_sync.tzinfo else row.last_successful_sync.replace(tzinfo=timezone.utc)
    return "fresh" if utcnow() - last <= timedelta(seconds=stale_after_seconds) else "stale"


def _dump(row: Any) -> dict[str, Any]:
    return {col.name: _json(getattr(row, col.name)) for col in row.__table__.columns}


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json(val) for key, val in value.items()}
    return value


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


def _num(value: Any) -> float | None:
    try:
        if value in {None, "", "."}:
            return None
        return float(value)
    except Exception:
        return None


def _hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _fingerprint(title: Any, url: Any, publisher: Any) -> str:
    return _hash({"title": _norm_title(title), "url": str(url or "").lower().split("?")[0], "publisher": str(publisher or "").lower()})


def _norm_title(title: Any) -> str:
    return " ".join(str(title or "").lower().split())


def _safe(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {str(key): ("***REDACTED***" if any(part in str(key).lower() for part in ("key", "token", "secret", "password")) else _safe(value)) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_safe(item) for item in payload]
    return payload
