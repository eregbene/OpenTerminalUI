from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.intelligence.trading.persistence import get_state, set_state, utcnow

MEMORY_KEY = "ai_trade_memory_v1"
REVIEWS_KEY = "ai_trade_reviews_v1"


def persist_trade_memory(payload: dict[str, Any]) -> dict[str, Any]:
    state = get_state(MEMORY_KEY)
    items = list(state.get("items") or [])
    record = {"id": payload.get("id") or f"memory_{len(items) + 1}", "created_at": utcnow().isoformat(), **payload}
    items.insert(0, record)
    set_state(MEMORY_KEY, {"items": items[:500], "updated_at": utcnow().isoformat()})
    return record


def list_trade_memory(limit: int = 50) -> list[dict[str, Any]]:
    return list((get_state(MEMORY_KEY).get("items") or [])[:limit])


def persist_trade_review(trade: dict[str, Any]) -> dict[str, Any]:
    state = get_state(REVIEWS_KEY)
    items = list(state.get("items") or [])
    pnl = float(trade.get("pnl") or trade.get("theoretical_pnl") or 0)
    review = {
        "id": f"review_{trade.get('id') or len(items) + 1}",
        "trade_id": trade.get("id"),
        "created_at": utcnow().isoformat(),
        "why_trade_taken": trade.get("ai_reasoning") or trade.get("reasoning_summary") or "Deterministic consensus and CIO review supported the decision.",
        "why_winner_loser": "winner" if pnl > 0 else ("loser" if pnl < 0 else "flat or still unresolved"),
        "mistakes": [],
        "strategy_worked": trade.get("strategy") or "unknown",
        "strategy_failed": None,
        "market_regime": trade.get("market_regime") or "unknown",
        "lessons": ["Compare consensus strength with realized outcome before increasing autonomy."],
    }
    items.insert(0, review)
    set_state(REVIEWS_KEY, {"items": items[:500], "updated_at": utcnow().isoformat()})
    return review


def list_trade_reviews(limit: int = 50) -> list[dict[str, Any]]:
    return list((get_state(REVIEWS_KEY).get("items") or [])[:limit])


def performance_metrics(memory: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = memory if memory is not None else list_trade_memory(500)
    closed = [row for row in rows if row.get("exit") is not None or row.get("pnl") is not None]
    pnls = [float(row.get("pnl") or 0) for row in closed]
    wins = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    return {
        "trade_count": len(rows),
        "closed_trade_count": len(closed),
        "win_rate": len(wins) / len(closed) if closed else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss else (gross_win if gross_win else 0.0),
        "expectancy": sum(pnls) / len(pnls) if pnls else 0.0,
        "sharpe": 0.0,
        "sortino": 0.0,
        "drawdown": _max_drawdown(pnls),
        "average_r": sum(float(row.get("reward") or 0) for row in rows) / len(rows) if rows else 0.0,
        "average_duration": _average_duration(rows),
        "win_rate_by_strategy": _group_win_rate(closed, "strategy"),
        "win_rate_by_session": _group_win_rate(closed, "session"),
        "win_rate_by_weekday": _group_win_rate(closed, "weekday"),
        "win_rate_by_volatility": _group_win_rate(closed, "volatility"),
        "win_rate_by_regime": _group_win_rate(closed, "market_regime"),
    }


def _max_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def _average_duration(rows: list[dict[str, Any]]) -> float:
    durations: list[float] = []
    for row in rows:
        started = row.get("entry_timestamp")
        ended = row.get("exit_timestamp")
        if not started or not ended:
            continue
        try:
            durations.append((datetime.fromisoformat(str(ended)) - datetime.fromisoformat(str(started))).total_seconds() / 60)
        except ValueError:
            continue
    return sum(durations) / len(durations) if durations else 0.0


def _group_win_rate(rows: list[dict[str, Any]], key: str) -> dict[str, float]:
    groups: dict[str, list[float]] = {}
    for row in rows:
        groups.setdefault(str(row.get(key) or "unknown"), []).append(float(row.get("pnl") or 0))
    return {name: len([pnl for pnl in values if pnl > 0]) / len(values) for name, values in groups.items() if values}
