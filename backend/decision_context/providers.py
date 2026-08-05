from __future__ import annotations

import hashlib
import json
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.decision_context.config import DecisionContextConfig
from backend.decision_context.mapping import affected_currencies, headline_impact


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def get_json(url: str, timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "BensimTrading/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def gdelt_query(config: DecisionContextConfig) -> list[dict[str, Any]]:
    query = urllib.parse.quote('("central bank" OR inflation OR employment OR GDP OR "interest rates" OR sanctions OR war OR "currency intervention" OR "financial instability")')
    maxrecords = max(1, min(250, config.gdelt_max_results_per_query))
    url = f"https://api.gdeltproject.org/api/v2/doc/doc?query={query}&mode=ArtList&format=json&maxrecords={maxrecords}&sort=HybridRel"
    data = get_json(url, config.gdelt_request_timeout_seconds)
    rows = data.get("articles") or []
    out: list[dict[str, Any]] = []
    now = utcnow()
    for row in rows[:maxrecords]:
        title = str(row.get("title") or "").strip()
        if not title:
            continue
        published = _parse_gdelt_date(row.get("seendate"))
        currencies = affected_currencies(title)
        if not currencies:
            continue
        out.append(
            {
                "provider": "gdelt",
                "provider_article_id": row.get("url") or payload_hash(row)[:32],
                "publisher": row.get("sourceCommonName") or row.get("domain"),
                "title": title,
                "permitted_summary": None,
                "article_url": row.get("url"),
                "published_at": published,
                "received_at": now,
                "language": row.get("language"),
                "topics": [],
                "event_type": "macro_headline",
                "provider_sentiment": None,
                "provider_sentiment_score": None,
                "impact": headline_impact(title),
                "mapping_evidence": currencies,
                "raw_payload": row,
            }
        )
    return out


def alpha_vantage_query(config: DecisionContextConfig) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not config.alpha_vantage_api_key:
        return [], {"status": "authentication_failed", "reason": "missing_api_key"}
    topics = urllib.parse.quote("forex,economy_monetary,economy_macro")
    url = f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&topics={topics}&sort=LATEST&limit=50&apikey={urllib.parse.quote(config.alpha_vantage_api_key)}"
    data = get_json(url, config.alpha_vantage_request_timeout_seconds)
    if "Information" in data or "Note" in data:
        return [], {"status": "rate_limited", "reason": "provider_quota_or_notice"}
    if "Error Message" in data:
        return [], {"status": "authentication_failed", "reason": "provider_error"}
    feed = data.get("feed")
    if not isinstance(feed, list):
        return [], {"status": "malformed_response", "reason": "missing_feed"}
    now = utcnow()
    rows: list[dict[str, Any]] = []
    for item in feed[:50]:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        published = _parse_av_time(item.get("time_published"))
        currencies = affected_currencies(" ".join([title, str(item.get("summary") or "")]))
        if not currencies:
            continue
        rows.append(
            {
                "provider": "alpha_vantage",
                "provider_article_id": item.get("url") or payload_hash(item)[:32],
                "publisher": item.get("source"),
                "title": title,
                "permitted_summary": item.get("summary"),
                "article_url": item.get("url"),
                "published_at": published,
                "received_at": now,
                "language": None,
                "topics": item.get("topics") or [],
                "event_type": "financial_news",
                "provider_sentiment": item.get("overall_sentiment_label"),
                "provider_sentiment_score": _float(item.get("overall_sentiment_score")),
                "impact": headline_impact(title),
                "mapping_evidence": currencies,
                "raw_payload": item,
            }
        )
    return rows, {"status": "ok", "records_received": len(rows)}


FRED_SERIES: dict[str, tuple[tuple[str, str], ...]] = {
    "USD": (("FEDFUNDS", "Effective Federal Funds Rate"), ("CPIAUCSL", "US CPI"), ("UNRATE", "US Unemployment Rate"), ("DGS10", "US 10Y Treasury Yield")),
    "EUR": (("CP0000EZ19M086NEST", "Euro Area CPI"), ("LRHUTTTTEZM156S", "Euro Area Unemployment")),
    "GBP": (("GBRCPIALLMINMEI", "UK CPI"), ("LRHUTTTTGBM156S", "UK Unemployment")),
    "JPY": (("JPNCPIALLMINMEI", "Japan CPI"), ("LRHUTTTTJPM156S", "Japan Unemployment")),
    "AUD": (("AUSCPIALLQINMEI", "Australia CPI"),),
    "CAD": (("CANCPIALLMINMEI", "Canada CPI"),),
    "CHF": (("CHECPIALLMINMEI", "Switzerland CPI"),),
    "NZD": (("NZLCPIALLQINMEI", "New Zealand CPI"),),
}


def fred_observations(config: DecisionContextConfig, currencies: set[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not config.fred_api_key:
        return [], {"status": "authentication_failed", "reason": "missing_api_key"}
    now = utcnow()
    rows: list[dict[str, Any]] = []
    for currency in sorted(currencies):
        for series_id, name in FRED_SERIES.get(currency, ()):
            url = (
                "https://api.stlouisfed.org/fred/series/observations?"
                f"series_id={urllib.parse.quote(series_id)}&api_key={urllib.parse.quote(config.fred_api_key)}"
                "&file_type=json&sort_order=desc&limit=8"
            )
            data = get_json(url, config.fred_request_timeout_seconds)
            observations = data.get("observations")
            if not isinstance(observations, list):
                continue
            values = [_float(obs.get("value")) for obs in observations]
            current = values[0] if values else None
            previous = next((value for value in values[1:] if value is not None), None)
            trend = "insufficient_data" if current is None or previous is None else "rising" if current > previous else "falling" if current < previous else "stable"
            for index, obs in enumerate(observations[:5]):
                rows.append(
                    {
                        "series_id": series_id,
                        "name": name,
                        "currency": currency,
                        "observation_date": _parse_date(obs.get("date")),
                        "value": _float(obs.get("value")),
                        "previous_known_value": values[index + 1] if index + 1 < len(values) else None,
                        "received_at": now,
                        "realtime_start": obs.get("realtime_start"),
                        "realtime_end": obs.get("realtime_end"),
                        "vintage_date": obs.get("realtime_start"),
                        "revision_state": "initial",
                        "trend": trend,
                        "raw_payload": obs,
                    }
                )
    return rows, {"status": "ok", "records_received": len(rows)}


def _parse_gdelt_date(value: Any) -> datetime | None:
    text = str(value or "")
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%d%H%M%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _parse_av_time(value: Any) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_date(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value)).replace(tzinfo=timezone.utc)
    except Exception:
        return utcnow() - timedelta(days=36500)


def _float(value: Any) -> float | None:
    try:
        if value in {None, "", "."}:
            return None
        return float(value)
    except Exception:
        return None
