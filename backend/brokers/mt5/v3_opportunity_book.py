from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import math
from typing import Any, Iterable


ACTIVE_OPPORTUNITY_STATUSES = {
    "ACTIVE_THESIS",
    "WAITING_POI",
    "PENDING_POI_TOUCH",
    "POI_TOUCHED",
    "TOUCHED_WAITING_CONFIRMATION",
    "WAITING_CONFIRMATION",
    "CONFIRMED",
    "CONFIRMED_FOR_ENTRY",
    "RANKED",
    "DEFERRED",
    "RESERVED",
}


@dataclass(frozen=True)
class CurrencyExposure:
    base: str
    quote: str
    base_units: float
    quote_units: float

    @property
    def theme(self) -> str:
        legs = sorted(
            ((self.base, self.base_units), (self.quote, self.quote_units)),
            key=lambda row: abs(row[1]),
            reverse=True,
        )
        first, second = legs
        first_side = "long" if first[1] > 0 else "short"
        second_side = "long" if second[1] > 0 else "short"
        return f"{first[0]}-{first_side}/{second[0]}-{second_side}"


def parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def canonical_symbol(symbol: Any) -> str:
    text = str(symbol or "").upper()
    for suffix in (".R", ".RAW", ".PRO", "_I", "_M"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def currency_exposure(symbol: str, direction: str) -> CurrencyExposure:
    symbol = canonical_symbol(symbol)
    direction = str(direction or "").upper()
    sign = 1.0 if direction == "LONG" else -1.0
    if symbol.startswith("XAU"):
        return CurrencyExposure("XAU", "USD", sign, -sign)
    if len(symbol) >= 6:
        return CurrencyExposure(symbol[:3], symbol[3:6], sign, -sign)
    return CurrencyExposure(symbol[:3] or "UNK", "UNK", sign, -sign)


def shared_exposure_theme(symbol: str, direction: str) -> str:
    exposure = currency_exposure(symbol, direction)
    if exposure.quote == "USD":
        side = "short" if exposure.quote_units < 0 else "long"
        if exposure.base == "XAU":
            return f"gold/USD-{side}"
        return f"USD-{side}"
    if exposure.base == "USD":
        side = "long" if exposure.base_units > 0 else "short"
        return f"USD-{side}"
    return exposure.theme


def exposure_vector(opportunities: Iterable[dict[str, Any]]) -> dict[str, float]:
    totals: defaultdict[str, float] = defaultdict(float)
    for row in opportunities:
        exposure = currency_exposure(str(row.get("symbol") or row.get("broker_symbol") or ""), str(row.get("direction") or ""))
        totals[exposure.base] += exposure.base_units
        totals[exposure.quote] += exposure.quote_units
    return {key: round(value, 4) for key, value in sorted(totals.items()) if abs(value) > 1e-9}


def strategy_ids(row: dict[str, Any]) -> list[str]:
    ids = row.get("confluence_strategy_ids") or row.get("v3_confluence_strategy_ids") or []
    if not ids:
        ids = [row.get("strategy_id") or row.get("primary_strategy_id") or row.get("strategy")]
    return sorted({str(item) for item in ids if item})


def opportunity_identity(row: dict[str, Any]) -> dict[str, str]:
    symbol = canonical_symbol(row.get("symbol") or row.get("broker_symbol"))
    direction = str(row.get("direction") or "").upper()
    context_id = str(row.get("bsi_v3_market_context_id") or f"context:{symbol}:{direction}:{row.get('source_timeframe') or row.get('timeframe') or 'NA'}")
    thesis_id = str(row.get("bsi_v3_market_thesis_id") or f"thesis:{symbol}:{direction}:{row.get('source_timeframe') or row.get('timeframe') or 'NA'}")
    poi_id = str(row.get("bsi_v3_poi_cluster_id") or row.get("bsi_v3_poi_id") or f"poi:{symbol}:{direction}:{row.get('fvg_low')}:{row.get('fvg_high')}")
    opportunity_id = str(row.get("bsi_v3_entry_opportunity_id") or row.get("entry_opportunity_id") or f"entry:{thesis_id}:{poi_id}")
    confirmation_id = str(row.get("bsi_v3_confirmation_id") or row.get("confirmation_id") or "")
    return {
        "market_context_id": context_id,
        "market_thesis_id": thesis_id,
        "poi_cluster_id": poi_id,
        "entry_opportunity_id": opportunity_id,
        "confirmation_id": confirmation_id,
    }


def quality_score(row: dict[str, Any], *, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    confidence = float(row.get("current_plan_confidence") or row.get("plan_confidence") or row.get("execution_confidence") or row.get("overall_confidence") or 0.0)
    rr = float(row.get("rr") or row.get("risk_reward") or row.get("initial_reward_risk") or 0.0)
    confluence = max(1, len(strategy_ids(row)))
    created_at = parse_time(row.get("created_at") or row.get("v3_plan_created_at"))
    age_hours = max(0.0, (now - created_at).total_seconds() / 3600.0) if created_at else 0.0
    freshness_penalty = min(6.0, age_hours * 0.25)
    rr_bonus = min(5.0, max(0.0, rr - 1.25) * 2.0)
    confluence_bonus = min(6.0, (confluence - 1) * 3.0)
    return round(max(0.0, min(100.0, confidence + rr_bonus + confluence_bonus - freshness_penalty)), 2)


def canonicalize_opportunities(rows: Iterable[dict[str, Any]], *, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    grouped: dict[str, dict[str, Any]] = {}
    for source in rows:
        identity = opportunity_identity(source)
        key = identity["entry_opportunity_id"]
        current = dict(source)
        current.update(identity)
        current["status"] = str(current.get("status") or current.get("v3_poi_touch_status") or "")
        current["symbol"] = canonical_symbol(current.get("symbol") or current.get("broker_symbol"))
        current["strategies"] = strategy_ids(current)
        current["quality_score"] = quality_score(current, now=now)
        current["exposure_theme"] = currency_exposure(current["symbol"], str(current.get("direction") or "")).theme
        current["shared_exposure_theme"] = shared_exposure_theme(current["symbol"], str(current.get("direction") or ""))
        existing = grouped.get(key)
        if existing is None:
            current["raw_observation_count"] = int(current.get("raw_observation_count") or 1)
            current["account_ids"] = sorted({str(current.get("account_id"))}) if current.get("account_id") else []
            grouped[key] = current
            continue
        existing["raw_observation_count"] = int(existing.get("raw_observation_count") or 1) + int(current.get("raw_observation_count") or 1)
        existing["quality_score"] = max(float(existing.get("quality_score") or 0.0), float(current.get("quality_score") or 0.0))
        existing["current_plan_confidence"] = max(
            float(existing.get("current_plan_confidence") or existing.get("plan_confidence") or 0.0),
            float(current.get("current_plan_confidence") or current.get("plan_confidence") or 0.0),
        )
        existing["strategies"] = sorted(set(existing.get("strategies") or []) | set(current.get("strategies") or []))
        existing["account_ids"] = sorted(set(existing.get("account_ids") or []) | ({str(current.get("account_id"))} if current.get("account_id") else set()))
        if float(current.get("quality_score") or 0.0) > float(existing.get("quality_score") or 0.0):
            for field in ("status", "confirmation_id", "source_timeframe", "created_at", "rr", "risk_reward"):
                if current.get(field):
                    existing[field] = current[field]
    return sorted(grouped.values(), key=lambda row: float(row.get("quality_score") or 0.0), reverse=True)


def select_portfolio_opportunities(opportunities: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    best_by_theme: dict[str, dict[str, Any]] = {}
    for row in sorted(opportunities, key=lambda item: float(item.get("quality_score") or 0.0), reverse=True):
        item = dict(row)
        if str(item.get("status") or "") not in ACTIVE_OPPORTUNITY_STATUSES:
            item["portfolio_decision"] = "IGNORED_INACTIVE"
            selected.append(item)
            continue
        theme = str(item.get("shared_exposure_theme") or item.get("exposure_theme") or "")
        incumbent = best_by_theme.get(theme)
        if incumbent is None:
            item["portfolio_decision"] = "SELECTED"
            best_by_theme[theme] = item
        else:
            gap = float(item.get("quality_score") or 0.0) - float(incumbent.get("quality_score") or 0.0)
            item["portfolio_decision"] = "DEFERRED_CORRELATED_EXPOSURE" if gap < 4.0 else "SELECTED_STRONGER_SAME_THEME"
            if gap >= 4.0:
                incumbent["portfolio_decision"] = "DEFERRED_RANKED_BELOW_STRONGER_SAME_THEME"
                best_by_theme[theme] = item
        selected.append(item)
    selected.sort(key=lambda item: float(item.get("quality_score") or 0.0), reverse=True)
    for index, row in enumerate(selected, start=1):
        row["quality_rank"] = index
    return selected


def load_queue_rows(paths: Iterable[Path], *, base_stem: str = "bsi_v3_live_pending_entry_queue") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, list):
            continue
        account_id = path.stem.removeprefix(f"{base_stem}_")
        for row in payload:
            if isinstance(row, dict):
                enriched = dict(row)
                enriched.setdefault("account_id", account_id)
                rows.append(enriched)
    return rows


def signal_explosion_summary(rows: Iterable[dict[str, Any]], selected: Iterable[dict[str, Any]], accepted_trades: int = 0) -> dict[str, Any]:
    source_rows = list(rows)
    selected_rows = list(selected)
    identities = [opportunity_identity(row) for row in source_rows]
    raw = len(source_rows)
    opportunities = {item["entry_opportunity_id"] for item in identities}
    confirmed = [row for row in selected_rows if str(row.get("status") or "") in {"CONFIRMED", "CONFIRMED_FOR_ENTRY", "RESERVED", "SUBMITTING"}]
    account_ids = {str(row.get("account_id")) for row in source_rows if row.get("account_id")}
    strategy_counts = Counter(strategy for row in source_rows for strategy in strategy_ids(row))
    duplicate_reduction = 0.0 if raw == 0 else round((1.0 - (len(opportunities) / raw)) * 100.0, 2)
    return {
        "raw_observations": raw,
        "unique_contexts": len({item["market_context_id"] for item in identities}),
        "unique_theses": len({item["market_thesis_id"] for item in identities}),
        "unique_poi_clusters": len({item["poi_cluster_id"] for item in identities}),
        "unique_opportunities": len(opportunities),
        "unique_confirmed_opportunities": len({row.get("entry_opportunity_id") for row in confirmed}),
        "portfolio_selected_opportunities": sum(1 for row in selected_rows if str(row.get("portfolio_decision")) == "SELECTED"),
        "accepted_broker_trades": accepted_trades,
        "duplicate_reduction_pct": duplicate_reduction,
        "account_replication_accounts": len(account_ids),
        "strategy_counts": dict(strategy_counts.most_common()),
        "portfolio_exposure": exposure_vector(row for row in selected_rows if str(row.get("portfolio_decision")) == "SELECTED"),
    }


def expectancy_metrics(trades: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in trades if row.get("r") is not None and not math.isnan(float(row.get("r")))]
    if not rows:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "net_r": 0.0, "expectancy": 0.0, "profit_factor": 0.0}
    values = [float(row["r"]) for row in rows]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trades": len(values),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(values) * 100.0, 2),
        "net_r": round(sum(values), 4),
        "expectancy": round(sum(values) / len(values), 4),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss else 0.0,
    }
