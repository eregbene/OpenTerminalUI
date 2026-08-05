from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import sqrt
from statistics import mean, pstdev
from typing import Any

from backend.intelligence.trading.memory import list_trade_memory
from backend.intelligence.trading.persistence import get_state, set_state, utcnow
from backend.intelligence.trading.strategies import STRATEGIES

STATE_KEY = "research_strategy_performance_v1"
MIN_WEIGHT_TRADES = 30
WINDOW_DAYS = (30, 60, 90)
REGIME_BUCKETS = ("TRENDING", "RANGING", "HIGH_VOLATILITY", "LOW_VOLATILITY", "BULLISH", "BEARISH")
SESSION_BUCKETS = ("LONDON", "NEW_YORK", "ASIA", "OVERLAP")


@dataclass(frozen=True)
class HealthCriteria:
    elite_pf: float = 1.8
    good_pf: float = 1.35
    poor_pf: float = 0.85
    min_trades: int = 30
    max_drawdown_watch: float = -8.0


class StrategyPerformanceService:
    def __init__(self, criteria: HealthCriteria | None = None) -> None:
        self.criteria = criteria or HealthCriteria()

    def refresh_from_paper_trades(self) -> dict[str, Any]:
        records = self._records_from_memory()
        payload = self._build_payload(records)
        set_state(STATE_KEY, payload)
        return payload

    def snapshot(self, *, refresh: bool = True) -> dict[str, Any]:
        state = get_state(STATE_KEY)
        if refresh or not state:
            return self.refresh_from_paper_trades()
        return state

    def strategies(self) -> list[dict[str, Any]]:
        return list(self.snapshot().get("strategies") or [])

    def strategy(self, strategy_id: str) -> dict[str, Any] | None:
        wanted = _normalize_strategy(strategy_id)
        for row in self.strategies():
            if row["strategy_id"] == wanted:
                return row
        return None

    def leaderboard(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {
            "items": snap.get("leaderboard", []),
            "adaptive_weights": snap.get("adaptive_weights", {}),
            "health_counts": snap.get("health_counts", {}),
            "sample_size_protection": snap.get("sample_size_protection", {}),
            "updated_at": snap.get("updated_at"),
        }

    def performance(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {
            "lifetime": snap.get("portfolio_metrics", {}),
            "rolling": snap.get("rolling_portfolio_metrics", {}),
            "monthly_returns": snap.get("monthly_returns", {}),
            "yearly_returns": snap.get("yearly_returns", {}),
            "distributions": snap.get("distributions", {}),
            "updated_at": snap.get("updated_at"),
        }

    def equity(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {
            "equity_curve": snap.get("equity_curve", []),
            "drawdown_curve": snap.get("drawdown_curve", []),
            "rolling_expectancy": snap.get("rolling_expectancy", []),
            "rolling_sharpe": snap.get("rolling_sharpe", []),
            "updated_at": snap.get("updated_at"),
        }

    def calibration(self) -> dict[str, Any]:
        snap = self.snapshot()
        return snap.get("calibration", {})

    def adaptive_weights(self) -> dict[str, float]:
        snap = self.snapshot(refresh=False)
        if not snap:
            snap = self.refresh_from_paper_trades()
        return {str(k): float(v) for k, v in (snap.get("adaptive_weights") or {}).items()}

    def first_analysis(self) -> dict[str, Any]:
        snap = self.snapshot()
        leaderboard = list(snap.get("leaderboard") or [])
        regimes = list(snap.get("regime_analysis") or [])
        return {
            "top_strategies": leaderboard[:3],
            "weakest_strategies": leaderboard[-3:],
            "best_market_regime": regimes[0] if regimes else None,
            "worst_regime": regimes[-1] if regimes else None,
            "recommended_adaptive_weights": snap.get("adaptive_weights", {}),
            "confidence_calibration": snap.get("calibration", {}),
            "research_recommendations": snap.get("recommendations", []),
            "data_sources": snap.get("data_sources", []),
            "updated_at": snap.get("updated_at"),
        }

    def _build_payload(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        strategy_ids = [_normalize_strategy(strategy.name) for strategy in STRATEGIES]
        by_strategy = {sid: [row for row in records if _normalize_strategy(row.get("strategy")) == sid] for sid in strategy_ids}
        strategies = [self._strategy_row(strategy.name, by_strategy[_normalize_strategy(strategy.name)], records) for strategy in STRATEGIES]
        weights = _normalized_weights(strategies)
        leaderboard = sorted(strategies, key=lambda row: (row["rank_score"], row["metrics"]["trades"]), reverse=True)
        for idx, row in enumerate(leaderboard, start=1):
            row["rank"] = idx
        equity_curve, drawdown_curve = _equity_curves(records)
        payload = {
            "schema_version": "strategy-performance-v1",
            "updated_at": utcnow().isoformat(),
            "activation_date": _min_timestamp(records),
            "dataset_hash": _dataset_hash(records),
            "data_sources": ["ai_trade_memory_v1", "deterministic_strategy_registry"],
            "strategies": strategies,
            "leaderboard": leaderboard,
            "adaptive_weights": weights,
            "portfolio_metrics": _metrics(records),
            "rolling_portfolio_metrics": {str(days): _metrics(_within_days(records, days)) for days in WINDOW_DAYS},
            "regime_analysis": _group_analysis(records, "market_regime", REGIME_BUCKETS),
            "session_analysis": _group_analysis(records, "session", SESSION_BUCKETS),
            "equity_curve": equity_curve,
            "drawdown_curve": drawdown_curve,
            "rolling_expectancy": _rolling_metric(records, "expectancy"),
            "rolling_sharpe": _rolling_metric(records, "sharpe"),
            "monthly_returns": _period_returns(records, "month"),
            "yearly_returns": _period_returns(records, "year"),
            "distributions": _distributions(records),
            "calibration": _calibration(records),
            "health_counts": _count_by(strategies, "health"),
            "sample_size_protection": {
                "minimum_trades_for_adaptive_weighting": MIN_WEIGHT_TRADES,
                "active": any(row["metrics"]["trades"] < MIN_WEIGHT_TRADES for row in strategies),
                "effect": "weights remain near neutral until statistically significant evidence accumulates",
            },
            "recommendations": _recommendations(strategies, records),
            "paper_learning": {
                "enabled": True,
                "source": "completed paper/shadow trade memory",
                "broker_calls": 0,
                "openai_calls": 0,
            },
        }
        return payload

    def _strategy_row(self, name: str, rows: list[dict[str, Any]], all_rows: list[dict[str, Any]]) -> dict[str, Any]:
        strategy_id = _normalize_strategy(name)
        metrics = _metrics(rows)
        rolling = {str(days): _metrics(_within_days(rows, days)) for days in WINDOW_DAYS}
        degradation = _degradation(rows)
        health = _health(metrics, degradation, self.criteria)
        return {
            "strategy_id": strategy_id,
            "name": name,
            "version": "deterministic-v1",
            "activation_date": _min_timestamp(rows) or _min_timestamp(all_rows),
            "dataset_hash": _dataset_hash(rows),
            "health": health,
            "rank_score": _rank_score(metrics, degradation),
            "metrics": metrics,
            "rolling": rolling,
            "regimes": _group_analysis(rows, "market_regime", REGIME_BUCKETS),
            "sessions": _group_analysis(rows, "session", SESSION_BUCKETS),
            "calibration": _calibration(rows),
            "degradation": degradation,
            "attribution": _attribution(rows),
        }

    def _records_from_memory(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in list_trade_memory(500):
            pnl = item.get("pnl")
            if pnl is None:
                pnl = item.get("theoretical_pnl")
            if pnl is None:
                continue
            contributors = _contributing_strategies(item)
            share = 1.0 / max(1, len(contributors))
            for strategy_name in contributors:
                rows.append(
                    {
                        "id": f"{item.get('id')}:{_normalize_strategy(strategy_name)}",
                        "trade_id": item.get("id"),
                        "strategy": strategy_name,
                        "contribution": share,
                        "timestamp": item.get("exit_timestamp") or item.get("created_at") or item.get("decision_timestamp"),
                        "pnl": _float(pnl) * share,
                        "r": _float(item.get("reward") or item.get("r_multiple") or pnl) * share,
                        "confidence": _float(item.get("confidence"), 0.0),
                        "consensus_score": _float((item.get("strategy_consensus") or {}).get("agreement_score") or item.get("consensus_score") or item.get("signal_strength"), 0.0),
                        "commission": _float(item.get("commission"), 0.0) * share,
                        "slippage": _float(item.get("slippage"), 0.0),
                        "spread": _float(item.get("spread_pips") or item.get("spread"), 0.0),
                        "mae": _float(item.get("mae"), 0.0),
                        "mfe": _float(item.get("mfe"), 0.0),
                        "holding_minutes": _holding_minutes(item),
                        "market_regime": _bucket_regime(item.get("market_regime")),
                        "session": _bucket_session(item.get("session") or item.get("market_session")),
                        "direction": str(item.get("decision") or item.get("direction") or "UNKNOWN").upper(),
                        "contributors": item.get("strategy_outputs") or item.get("contributors") or [],
                        "indicators": item.get("indicators") or {},
                        "ai_reasoning": item.get("ai_reasoning") or item.get("reasoning_summary"),
                    }
                )
        rows.sort(key=lambda row: _parse_time(row.get("timestamp")) or datetime.min.replace(tzinfo=timezone.utc))
        return rows


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = [_float(row.get("pnl")) for row in rows]
    r_values = [_float(row.get("r")) for row in rows]
    wins = [value for value in pnl if value > 0]
    losses = [value for value in pnl if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    returns = r_values if any(r_values) else pnl
    dd = _max_drawdown(pnl)
    downside = [min(0.0, row) for row in returns]
    pf = gross_profit / gross_loss if gross_loss else (gross_profit if gross_profit else 0.0)
    return {
        "trades": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(rows) if rows else 0.0,
        "average_r": mean(r_values) if r_values else 0.0,
        "expectancy": mean(pnl) if pnl else 0.0,
        "profit_factor": pf,
        "sharpe": _ratio(returns),
        "sortino": _ratio(returns, downside_only=True),
        "calmar": (sum(pnl) / abs(dd)) if dd < 0 else 0.0,
        "maximum_drawdown": dd,
        "recovery_factor": (sum(pnl) / abs(dd)) if dd < 0 else 0.0,
        "ulcer_index": _ulcer_index(pnl),
        "average_mae": mean([_float(row.get("mae")) for row in rows]) if rows else 0.0,
        "average_mfe": mean([_float(row.get("mfe")) for row in rows]) if rows else 0.0,
        "average_holding_time": mean([_float(row.get("holding_minutes")) for row in rows]) if rows else 0.0,
        "average_commission": mean([_float(row.get("commission")) for row in rows]) if rows else 0.0,
        "average_slippage": mean([_float(row.get("slippage")) for row in rows]) if rows else 0.0,
        "average_spread": mean([_float(row.get("spread")) for row in rows]) if rows else 0.0,
        "average_confidence": mean([_float(row.get("confidence")) for row in rows]) if rows else 0.0,
        "average_consensus_score": mean([_float(row.get("consensus_score")) for row in rows]) if rows else 0.0,
    }


def _rank_score(metrics: dict[str, Any], degradation: dict[str, Any]) -> float:
    sample = min(1.0, _float(metrics.get("trades")) / MIN_WEIGHT_TRADES)
    score = 1.0
    score += min(_float(metrics.get("profit_factor")), 3.0) * 0.35
    score += max(-1.0, min(1.0, _float(metrics.get("expectancy")))) * 0.3
    score += max(-1.0, _float(metrics.get("maximum_drawdown")) / 10.0) * 0.2
    score += _float(metrics.get("win_rate")) * 0.15
    if degradation.get("active"):
        score *= 0.85
    return round(score * max(0.25, sample), 4)


def _normalized_weights(strategies: list[dict[str, Any]]) -> dict[str, float]:
    raw: dict[str, float] = {}
    for row in strategies:
        trades = int(row["metrics"]["trades"])
        if trades < MIN_WEIGHT_TRADES:
            raw[row["strategy_id"]] = 1.0
            continue
        score = max(0.25, _float(row["rank_score"]))
        raw[row["strategy_id"]] = min(1.5, max(0.5, score))
    avg = mean(raw.values()) if raw else 1.0
    return {key: round(value / avg, 4) for key, value in raw.items()}


def _health(metrics: dict[str, Any], degradation: dict[str, Any], criteria: HealthCriteria) -> str:
    trades = int(metrics.get("trades") or 0)
    pf = _float(metrics.get("profit_factor"))
    expectancy = _float(metrics.get("expectancy"))
    drawdown = _float(metrics.get("maximum_drawdown"))
    if trades < criteria.min_trades:
        return "NEUTRAL"
    if degradation.get("active"):
        return "WATCHLIST"
    if pf >= criteria.elite_pf and expectancy > 0 and drawdown > criteria.max_drawdown_watch:
        return "ELITE"
    if pf >= criteria.good_pf and expectancy >= 0:
        return "GOOD"
    if pf <= criteria.poor_pf or expectancy < 0:
        return "POOR"
    return "NEUTRAL"


def _calibration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    bins = []
    total_error = 0.0
    brier_values: list[float] = []
    for idx in range(10):
        low = idx / 10
        high = (idx + 1) / 10
        bucket = [row for row in rows if low <= _float(row.get("confidence")) < high or (idx == 9 and _float(row.get("confidence")) == 1.0)]
        if not bucket:
            bins.append({"bin": f"{low:.1f}-{high:.1f}", "count": 0, "average_confidence": 0.0, "actual_success": 0.0})
            continue
        avg_conf = mean([_float(row.get("confidence")) for row in bucket])
        success = len([row for row in bucket if _float(row.get("pnl")) > 0]) / len(bucket)
        total_error += abs(avg_conf - success) * (len(bucket) / max(1, len(rows)))
        brier_values.extend([(_float(row.get("confidence")) - (1.0 if _float(row.get("pnl")) > 0 else 0.0)) ** 2 for row in bucket])
        bins.append({"bin": f"{low:.1f}-{high:.1f}", "count": len(bucket), "average_confidence": round(avg_conf, 4), "actual_success": round(success, 4)})
    return {
        "expected_calibration_error": round(total_error, 4),
        "brier_score": round(mean(brier_values), 4) if brier_values else 0.0,
        "reliability": bins,
        "status": "INSUFFICIENT_SAMPLE" if len(rows) < MIN_WEIGHT_TRADES else "CALCULATED",
    }


def _group_analysis(rows: list[dict[str, Any]], key: str, defaults: tuple[str, ...]) -> list[dict[str, Any]]:
    names = set(defaults) | {str(row.get(key) or "UNKNOWN").upper() for row in rows}
    result = []
    for name in sorted(names):
        bucket = [row for row in rows if str(row.get(key) or "UNKNOWN").upper() == name]
        result.append({"name": name, "metrics": _metrics(bucket)})
    return sorted(result, key=lambda row: (row["metrics"]["profit_factor"], row["metrics"]["expectancy"]), reverse=True)


def _equity_curves(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    equity = 0.0
    peak = 0.0
    curve = []
    drawdown = []
    for row in rows:
        equity += _float(row.get("pnl"))
        peak = max(peak, equity)
        dd = equity - peak
        ts = str(row.get("timestamp") or "")
        curve.append({"timestamp": ts, "equity": round(equity, 4), "trade_id": row.get("id")})
        drawdown.append({"timestamp": ts, "drawdown": round(dd, 4), "trade_id": row.get("id")})
    return curve, drawdown


def _rolling_metric(rows: list[dict[str, Any]], metric: str, window: int = 20) -> list[dict[str, Any]]:
    result = []
    for idx in range(len(rows)):
        bucket = rows[max(0, idx - window + 1) : idx + 1]
        metrics = _metrics(bucket)
        result.append({"timestamp": rows[idx].get("timestamp"), metric: metrics.get(metric, 0.0)})
    return result


def _period_returns(rows: list[dict[str, Any]], mode: str) -> dict[str, float]:
    result: dict[str, float] = {}
    for row in rows:
        parsed = _parse_time(row.get("timestamp"))
        if not parsed:
            key = "unknown"
        elif mode == "year":
            key = f"{parsed.year}"
        else:
            key = f"{parsed.year}-{parsed.month:02d}"
        result[key] = round(result.get(key, 0.0) + _float(row.get("pnl")), 4)
    return result


def _distributions(rows: list[dict[str, Any]]) -> dict[str, list[float]]:
    return {
        "r": [_float(row.get("r")) for row in rows],
        "holding_time": [_float(row.get("holding_minutes")) for row in rows],
        "mae": [_float(row.get("mae")) for row in rows],
        "mfe": [_float(row.get("mfe")) for row in rows],
    }


def _attribution(rows: list[dict[str, Any]]) -> dict[str, Any]:
    contributions: dict[str, float] = {}
    indicators: dict[str, int] = {}
    for row in rows:
        contributors = row.get("contributors") or []
        if isinstance(contributors, list) and contributors:
            share = 1.0 / len(contributors)
            for item in contributors:
                name = _normalize_strategy(item.get("strategy") if isinstance(item, dict) else item)
                contributions[name] = round(contributions.get(name, 0.0) + share, 4)
        for name in (row.get("indicators") or {}).keys():
            indicators[name] = indicators.get(name, 0) + 1
    return {"strategy_contribution": contributions, "indicator_frequency": indicators}


def _contributing_strategies(item: dict[str, Any]) -> list[str]:
    contributors = item.get("strategy_outputs") or item.get("contributors") or []
    names: list[str] = []
    if isinstance(contributors, list):
        for row in contributors:
            if isinstance(row, dict):
                decision = str(row.get("decision") or row.get("direction") or "").upper()
                if decision == "NO_TRADE" or row.get("valid") is False:
                    continue
                name = row.get("strategy")
            else:
                name = row
            if name:
                names.append(str(name))
    return names or [str(item.get("strategy") or "institutional_consensus")]


def _degradation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if len(rows) < 10:
        return {"active": False, "reason": "INSUFFICIENT_SAMPLE"}
    recent = _metrics(rows[-5:])
    prior = _metrics(rows[:-5])
    active = recent["expectancy"] < prior["expectancy"] and recent["profit_factor"] < prior["profit_factor"]
    return {
        "active": bool(active),
        "reason": "RECENT_DEGRADATION" if active else "STABLE",
        "recent_expectancy": recent["expectancy"],
        "prior_expectancy": prior["expectancy"],
    }


def _recommendations(strategies: list[dict[str, Any]], rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["No completed paper trades are available yet; keep weights neutral until evidence accumulates."]
    weak = [row["name"] for row in strategies if row["health"] in {"WATCHLIST", "POOR"}]
    if weak:
        return [f"Review {', '.join(weak[:3])} before increasing paper allocation."]
    return ["Continue collecting paper outcomes; adaptive weights are research-only until sample-size protection clears."]


def _records_by_day(rows: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    cutoff = utcnow() - timedelta(days=days)
    return [row for row in rows if (_parse_time(row.get("timestamp")) or cutoff) >= cutoff]


def _within_days(rows: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    return _records_by_day(rows, days)


def _max_drawdown(pnl: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in pnl:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return round(drawdown, 4)


def _ulcer_index(pnl: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    values = []
    for value in pnl:
        equity += value
        peak = max(peak, equity)
        values.append((equity - peak) ** 2)
    return round(sqrt(mean(values)), 4) if values else 0.0


def _ratio(values: list[float], *, downside_only: bool = False) -> float:
    if len(values) < 2:
        return 0.0
    sample = [min(0.0, row) for row in values] if downside_only else values
    dev = pstdev(sample)
    return round((mean(values) / dev) * sqrt(252), 4) if dev else 0.0


def _holding_minutes(item: dict[str, Any]) -> float:
    start = _parse_time(item.get("entry_timestamp") or item.get("created_at"))
    end = _parse_time(item.get("exit_timestamp"))
    if start and end:
        return max((end - start).total_seconds() / 60, 0.0)
    return _float(item.get("holding_minutes"), 0.0)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _min_timestamp(rows: list[dict[str, Any]]) -> str | None:
    values = [_parse_time(row.get("timestamp")) for row in rows]
    values = [row for row in values if row is not None]
    return min(values).isoformat() if values else None


def _dataset_hash(rows: list[dict[str, Any]]) -> str:
    import hashlib

    raw = "|".join(str(row.get("id")) + str(row.get("pnl")) for row in rows)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "UNKNOWN")
        result[value] = result.get(value, 0) + 1
    return result


def _normalize_strategy(value: Any) -> str:
    return str(value or "unknown").strip().lower().replace(" ", "_").replace("/", "_")


def _bucket_regime(value: Any) -> str:
    text = str(value or "UNKNOWN").upper()
    if "TREND" in text:
        return "TRENDING"
    if "RANGE" in text:
        return "RANGING"
    if "HIGH" in text and "VOL" in text:
        return "HIGH_VOLATILITY"
    if "LOW" in text and "VOL" in text:
        return "LOW_VOLATILITY"
    if "BULL" in text:
        return "BULLISH"
    if "BEAR" in text:
        return "BEARISH"
    return text


def _bucket_session(value: Any) -> str:
    text = str(value or "UNKNOWN").upper().replace(" ", "_")
    if "NEW" in text or "NY" in text:
        return "NEW_YORK"
    if "LONDON" in text:
        return "LONDON"
    if "ASIA" in text:
        return "ASIA"
    if "OVERLAP" in text:
        return "OVERLAP"
    return text


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


strategy_performance_service = StrategyPerformanceService()
