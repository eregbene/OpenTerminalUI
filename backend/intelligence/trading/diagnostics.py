from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.intelligence.trading.config import AITradingConfig
from backend.intelligence.trading.consensus import build_consensus
from backend.intelligence.trading.candles import (
    TIMEFRAME_BY_SECONDS,
    audit_canonical_candles,
    canonicalize_candles,
    expected_completed,
    resample_candles,
)
from backend.intelligence.trading.market_context import build_market_context
from backend.intelligence.trading.persistence import get_state, set_state, utcnow
from backend.intelligence.trading.strategies import evaluate_strategies
from backend.services.forex_service import service as forex_service

LATEST_DIAGNOSTICS_KEY = "ai_trading_latest_diagnostics_v1"
REPLAY_JOBS_KEY = "ai_trading_replay_jobs_v1"


def audit_candles(candles: list[dict[str, Any]], *, interval_seconds: int, source: str, now: datetime | None = None) -> dict[str, Any]:
    timeframe = TIMEFRAME_BY_SECONDS.get(interval_seconds, "15m")
    canonical, meta = canonicalize_candles(candles, symbol="UNKNOWN", timeframe=timeframe, source=source, now=now or utcnow())
    return audit_canonical_candles(canonical, timeframe=timeframe, source=source, now=now or utcnow(), canonical_meta=meta)


def build_live_diagnostics(context: dict[str, Any], *, profile: dict[str, Any]) -> dict[str, Any]:
    strategy_outputs = list(context.get("strategy_outputs") or [])
    consensus = context.get("strategy_consensus") or {}
    eligibility = context.get("consensus_eligibility") or {}
    quality = context.get("data_quality") or {}
    closest = _closest_strategy(strategy_outputs)
    consensus_gap = max(0.0, float(profile.get("consensus_threshold") or 0) - float(consensus.get("agreement_score") or 0))
    directional_score = max(float(consensus.get("bull_score") or 0), float(consensus.get("bear_score") or 0))
    directional_gap = max(0.0, float(profile.get("min_directional_score") or 0) - directional_score)
    failed_gate = "openai_gate"
    if quality and quality.get("status") not in {None, "VALID"}:
        failed_gate = "data_quality_gate"
    elif closest and not closest.get("entry_conditions_met"):
        failed_gate = "strategy_gate"
    elif any((row.get("geometry") or {}).get("rejection_codes") for row in strategy_outputs):
        failed_gate = "entry_geometry_gate"
    elif not eligibility.get("eligible"):
        failed_gate = "consensus_gate"
    return {
        "generated_at": utcnow().isoformat(),
        "symbol": context.get("symbol"),
        "timeframe": context.get("timeframe"),
        "profile": profile.get("name"),
        "data_quality": quality,
        "strategy_diagnostics": strategy_outputs,
        "closest_strategy_to_eligibility": closest,
        "closest_consensus_score_to_threshold": consensus.get("agreement_score"),
        "consensus_gap": consensus_gap,
        "directional_score": directional_score,
        "directional_score_gap": directional_gap,
        "entry_geometry_rejections": [
            {"strategy": row.get("strategy"), "codes": (row.get("geometry") or {}).get("rejection_codes")}
            for row in strategy_outputs
            if (row.get("geometry") or {}).get("rejection_codes")
        ],
        "failed_gate": failed_gate,
        "consensus": consensus,
        "consensus_eligibility": eligibility,
    }


def persist_latest_diagnostics(payload: dict[str, Any]) -> None:
    set_state(LATEST_DIAGNOSTICS_KEY, payload)


def latest_diagnostics() -> dict[str, Any]:
    return get_state(LATEST_DIAGNOSTICS_KEY)


def _closest_strategy(strategy_outputs: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not strategy_outputs:
        return None
    return sorted(
        strategy_outputs,
        key=lambda row: (
            bool(row.get("entry_conditions_met")),
            float(row.get("confidence") or 0),
            float(row.get("calculated_risk_reward") or row.get("risk_reward") or 0),
        ),
        reverse=True,
    )[0]


async def run_replay(*, symbol: str, days: int, config: AITradingConfig) -> dict[str, Any]:
    normalized = symbol.upper().replace("/", "").replace("FX:", "")
    job_id = f"replay_{normalized}_{hashlib.sha256(f'{normalized}:{days}:{utcnow().isoformat()}'.encode()).hexdigest()[:12]}"
    try:
        chart = await forex_service.get_pair_chart(normalized, interval="15m", range_str=f"{max(days, 7)}d")
    except Exception as exc:
        chart = {"candles": [], "source_symbol": "forex_service", "error": exc.__class__.__name__}
    candles = list(chart.get("candles") or [])
    cutoff = utcnow() - timedelta(days=days)
    rows = [row for row in candles if datetime.fromtimestamp(int(row["t"]), tz=timezone.utc) >= cutoff]
    if not rows:
        rows = candles[-min(len(candles), days * 96) :]
    source = str(chart.get("source_symbol") or "forex_service")
    report = _replay_rows(rows, source=source, symbol=normalized, config=config)
    if chart.get("error"):
        report["data_quality"]["status"] = "INVALID"
        report["data_quality"].setdefault("reasons", []).append("NO_DATA")
        report["data_quality"]["provider_error"] = chart["error"]
    comparison = _build_comparison(report, _latest_replay_report())
    payload = {
        "job_id": job_id,
        "label": "HISTORICAL_REPLAY_SHADOW_CANDIDATE",
        "created_at": utcnow().isoformat(),
        "symbol": normalized,
        "days": days,
        "read_only": True,
        "provider_calls": 0,
        "ibkr_calls": 0,
        "report": report,
        "comparison": comparison,
    }
    _save_replay(job_id, payload)
    return payload


def get_replay(job_id: str) -> dict[str, Any] | None:
    return (get_state(REPLAY_JOBS_KEY).get("jobs") or {}).get(job_id)


def replay_history(limit: int = 25) -> list[dict[str, Any]]:
    jobs = get_state(REPLAY_JOBS_KEY).get("jobs") or {}
    rows = sorted(jobs.values(), key=lambda row: row.get("created_at") or "", reverse=True)
    return rows[: max(1, min(limit, 100))]


def _replay_rows(rows: list[dict[str, Any]], *, source: str, symbol: str, config: AITradingConfig) -> dict[str, Any]:
    canonical, canonical_meta = canonicalize_candles(rows, symbol=symbol, timeframe="15m", source=source, now=utcnow())
    rows = [candle.as_chart_row() for candle in canonical]
    resampled_1h, resample_1h_meta = resample_candles(canonical, target_timeframe="1h")
    resampled_4h, resample_4h_meta = resample_candles(canonical, target_timeframe="4h")
    higher_trend_by_ts = _higher_trend_by_timestamp(resampled_1h, symbol=symbol)
    production = config.production_profile.model_dump()
    validation = config.validation_profile.model_dump()
    analytics = {
        "candles_evaluated": 0,
        "valid_data_quality_candles": 0,
        "strategy_signals_by_strategy": {},
        "eligible_signals_by_strategy": {},
        "long_candidates": 0,
        "short_candidates": 0,
        "production_consensus_passing_candidates": 0,
        "validation_consensus_passing_candidates": 0,
        "hypothetical_openai_calls": 0,
        "rejection_count_by_reason": {},
        "average_bull_score": 0.0,
        "average_bear_score": 0.0,
        "average_agreement_score": 0.0,
        "maximum_agreement_score": 0.0,
        "maximum_directional_score": 0.0,
        "average_eligible_strategy_count": 0.0,
        "maximum_eligible_strategy_count": 0,
        "strategy_conflict_frequency": {},
        "regime_distribution": {},
        "candidate_frequency_per_day": 0.0,
        "median_time_between_candidates_minutes": None,
        "entry_conditions_met_candles": 0,
        "validation_not_production": 0,
        "production_passing": 0,
        "strategies_never_eligible": [],
        "missing_data_frequency": 0,
    }
    candidates: list[dict[str, Any]] = []
    candidate_times: list[datetime] = []
    sums = {"bull": 0.0, "bear": 0.0, "agreement": 0.0, "eligible": 0.0}
    warmup = 220 if len(rows) >= 220 else 60
    for idx in range(warmup, len(rows) + 1):
        window = rows[:idx]
        quality = audit_candles(window[-warmup:], interval_seconds=900, source=source)
        analytics["candles_evaluated"] += 1
        if quality["status"] == "VALID":
            analytics["valid_data_quality_candles"] += 1
        else:
            analytics["missing_data_frequency"] += 1
        current_ts = int(window[-1]["t"])
        htf = higher_trend_by_ts.get(current_ts, "unknown")
        ctx = build_market_context(window[-warmup:], symbol=symbol, timeframe="15m", spread=0.8, higher_timeframe_trend=htf)
        outputs = evaluate_strategies(ctx)
        consensus = build_consensus(outputs).model_dump()
        prod_ok = _profile_ok(consensus, production)
        val_ok = _profile_ok(consensus, validation)
        for row in outputs:
            analytics["strategy_signals_by_strategy"].setdefault(row.strategy, 0)
            analytics["eligible_signals_by_strategy"].setdefault(row.strategy, 0)
            if row.decision in {"LONG", "SHORT"}:
                analytics["strategy_signals_by_strategy"][row.strategy] += 1
            if row.entry_conditions_met:
                analytics["eligible_signals_by_strategy"][row.strategy] += 1
            for code in row.rejection_codes or []:
                analytics["rejection_count_by_reason"][code] = analytics["rejection_count_by_reason"].get(code, 0) + 1
        if consensus["recommended_direction"] == "LONG":
            analytics["long_candidates"] += 1
        if consensus["recommended_direction"] == "SHORT":
            analytics["short_candidates"] += 1
        analytics["production_consensus_passing_candidates"] += int(prod_ok)
        analytics["validation_consensus_passing_candidates"] += int(val_ok)
        analytics["hypothetical_openai_calls"] += int(val_ok)
        analytics["entry_conditions_met_candles"] += int(any(row.entry_conditions_met for row in outputs))
        analytics["validation_not_production"] += int(val_ok and not prod_ok)
        analytics["production_passing"] += int(prod_ok)
        for name in consensus.get("conflicting_strategies") or []:
            analytics["strategy_conflict_frequency"][name] = analytics["strategy_conflict_frequency"].get(name, 0) + 1
        analytics["regime_distribution"][ctx.market_regime] = analytics["regime_distribution"].get(ctx.market_regime, 0) + 1
        sums["bull"] += float(consensus.get("bull_score") or 0)
        sums["bear"] += float(consensus.get("bear_score") or 0)
        sums["agreement"] += float(consensus.get("agreement_score") or 0)
        sums["eligible"] += float(consensus.get("eligible_strategy_count") or 0)
        analytics["maximum_agreement_score"] = max(analytics["maximum_agreement_score"], float(consensus.get("agreement_score") or 0))
        analytics["maximum_directional_score"] = max(analytics["maximum_directional_score"], max(float(consensus.get("bull_score") or 0), float(consensus.get("bear_score") or 0)))
        analytics["maximum_eligible_strategy_count"] = max(analytics["maximum_eligible_strategy_count"], int(consensus.get("eligible_strategy_count") or 0))
        if val_ok:
            ts = datetime.fromtimestamp(int(window[-1]["t"]), tz=timezone.utc)
            candidate_times.append(ts)
            candidates.append(_shadow_candidate(ts, symbol, outputs, consensus, validation))
    count = max(analytics["candles_evaluated"], 1)
    analytics["average_bull_score"] = sums["bull"] / count
    analytics["average_bear_score"] = sums["bear"] / count
    analytics["average_agreement_score"] = sums["agreement"] / count
    analytics["average_eligible_strategy_count"] = sums["eligible"] / count
    analytics["candidate_frequency_per_day"] = analytics["validation_consensus_passing_candidates"] / max(1, (rows[-1]["t"] - rows[0]["t"]) / 86400) if len(rows) > 1 else 0
    deltas = [(b - a).total_seconds() / 60 for a, b in zip(candidate_times, candidate_times[1:])]
    analytics["median_time_between_candidates_minutes"] = sorted(deltas)[len(deltas) // 2] if deltas else None
    analytics["strategies_never_eligible"] = [name for name, count_ in analytics["eligible_signals_by_strategy"].items() if count_ == 0]
    return {
        "period": {"start": datetime.fromtimestamp(int(rows[0]["t"]), tz=timezone.utc).isoformat() if rows else None, "end": datetime.fromtimestamp(int(rows[-1]["t"]), tz=timezone.utc).isoformat() if rows else None},
        "data_quality": audit_candles(rows, interval_seconds=900, source=source) if rows else {"status": "INVALID", "reasons": ["REQUIRED_DATA_MISSING"]},
        "canonical_data": {
            "schema": "canonical_candles_v1",
            "15m": audit_canonical_candles(canonical, timeframe="15m", source=source, now=utcnow(), canonical_meta=canonical_meta),
            "1h": {"candle_count": len(resampled_1h), "resampling": resample_1h_meta, "source_provenance": {"timeframe": "1h", "source": source, "method": "4x15m_ohlc"}},
            "4h": {"candle_count": len(resampled_4h), "resampling": resample_4h_meta, "source_provenance": {"timeframe": "4h", "source": source, "method": "16x15m_ohlc"}},
            "5m": {"status": "OPTIONAL_INPUT_UNAVAILABLE", "source_provenance": {"timeframe": "5m", "source": None, "method": "not_resampled_from_15m"}},
        },
        "production_thresholds": production,
        "validation_thresholds": validation,
        "analytics": analytics,
        "shadow_candidates": candidates[:100],
    }


def _profile_ok(consensus: dict[str, Any], profile: dict[str, Any]) -> bool:
    direction_score = max(float(consensus.get("bull_score") or 0), float(consensus.get("bear_score") or 0))
    return (
        consensus.get("recommended_direction") in {"LONG", "SHORT"}
        and float(consensus.get("agreement_score") or 0) >= float(profile["consensus_threshold"])
        and int(consensus.get("eligible_strategy_count") or 0) >= int(profile["min_eligible_strategies"])
        and direction_score >= float(profile["min_directional_score"])
        and len(consensus.get("conflicting_strategies") or []) <= int(profile["max_conflicting_strategies"])
    )


def _shadow_candidate(ts: datetime, symbol: str, outputs: list[Any], consensus: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    best = next((row for row in outputs if row.decision == consensus.get("recommended_direction")), None)
    prompt_payload = {"symbol": symbol, "timestamp": ts.isoformat(), "profile": profile["name"], "consensus": consensus}
    return {
        "label": "HISTORICAL_REPLAY_SHADOW_CANDIDATE",
        "timestamp": ts.isoformat(),
        "market_context_id": hashlib.sha256(json.dumps(prompt_payload, sort_keys=True).encode()).hexdigest()[:16],
        "profile": profile["name"],
        "strategy_outputs": [row.model_dump() for row in outputs],
        "consensus": consensus,
        "proposed_entry": getattr(best, "entry", None),
        "stop": getattr(best, "stop", None),
        "target": getattr(best, "target", None),
        "risk_reward": getattr(best, "risk_reward", None),
        "expected_compact_prompt_size": len(json.dumps(prompt_payload, default=str)),
    }


def _save_replay(job_id: str, payload: dict[str, Any]) -> None:
    state = get_state(REPLAY_JOBS_KEY)
    jobs = dict(state.get("jobs") or {})
    jobs[job_id] = payload
    set_state(REPLAY_JOBS_KEY, {"jobs": jobs, "updated_at": utcnow().isoformat()})


def _expected_completed(interval_seconds: int, now: datetime) -> datetime:
    return expected_completed(TIMEFRAME_BY_SECONDS.get(interval_seconds, "15m"), now)


def _higher_trend_by_timestamp(candles_1h: list[Any], *, symbol: str) -> dict[int, str]:
    trends: dict[int, str] = {}
    rows = [candle.as_chart_row() for candle in candles_1h]
    for idx in range(60, len(rows) + 1):
        ctx = build_market_context(rows[:idx], symbol=symbol, timeframe="1h", spread=0.8)
        close_ts = int(rows[idx - 1]["t"]) + 3600
        for child_ts in range(close_ts, close_ts + 3600, 900):
            trends[child_ts] = "bullish" if ctx.ema20 and ctx.ema50 and ctx.ema20 > ctx.ema50 else ("bearish" if ctx.ema20 and ctx.ema50 and ctx.ema20 < ctx.ema50 else "neutral")
    return trends


def _latest_replay_report() -> dict[str, Any] | None:
    rows = replay_history(1)
    return (rows[0].get("report") if rows else None) or None


def _build_comparison(current: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    cur = current.get("analytics") or {}
    prev = (previous or {}).get("analytics") or {}
    keys = [
        "candles_evaluated",
        "validation_consensus_passing_candidates",
        "production_consensus_passing_candidates",
        "maximum_agreement_score",
        "maximum_directional_score",
        "maximum_eligible_strategy_count",
    ]
    return {
        "baseline": "previous_replay" if previous else "none",
        "read_only": True,
        "metrics": {key: {"before": prev.get(key), "after": cur.get(key), "delta": (cur.get(key) - prev.get(key)) if isinstance(cur.get(key), (int, float)) and isinstance(prev.get(key), (int, float)) else None} for key in keys},
        "strategy_signals_by_strategy": {
            name: {"before": (prev.get("strategy_signals_by_strategy") or {}).get(name, 0), "after": count}
            for name, count in (cur.get("strategy_signals_by_strategy") or {}).items()
        },
    }
