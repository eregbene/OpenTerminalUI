from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

HORIZONS_MINUTES = (1, 5, 15, 30, 60)


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


def compute_price_reaction(event_scheduled_at: Any, candles_m1: list[Any]) -> dict[str, Any]:
    """Core price-reaction metrics for an event, using MT5 M1 candles as the sole price
    source (Forex Factory only supplies event context, never execution/backtest prices).

    Returns return-at-horizon (1/5/15/30/60 min) and max favorable/adverse excursion when
    the event time falls within the supplied candle window; otherwise `insufficient_data`.

    Scoping: spread-expansion-duration, first-move continuation/reversal classification,
    and per-strategy performance-around-event are NOT computed here -- deferred follow-on
    work (see plan). This also only evaluates events recent enough to be covered by the
    candle window the caller fetched (MT5's adapter exposes count-based recent candles,
    not arbitrary historical ranges), so older events return insufficient_data honestly
    rather than fabricating a result.
    """
    scheduled = _dt(event_scheduled_at)
    if scheduled is None or not candles_m1:
        return {"status": "insufficient_data", "reason": "missing_event_time_or_candles"}
    rows = sorted(({"time": _dt(getattr(c, "time", None) or c.get("time")), "close": float(getattr(c, "close", None) if hasattr(c, "close") else c.get("close")), "high": float(getattr(c, "high", None) if hasattr(c, "high") else c.get("high")), "low": float(getattr(c, "low", None) if hasattr(c, "low") else c.get("low"))} for c in candles_m1), key=lambda row: row["time"] or datetime.min.replace(tzinfo=timezone.utc))
    rows = [row for row in rows if row["time"] is not None]
    if not rows or scheduled < rows[0]["time"] or scheduled > rows[-1]["time"] + timedelta(minutes=60):
        return {"status": "insufficient_data", "reason": "event_time_outside_candle_window"}
    base_row = min(rows, key=lambda row: abs((row["time"] - scheduled).total_seconds()))
    if abs((base_row["time"] - scheduled).total_seconds()) > 120:
        return {"status": "insufficient_data", "reason": "no_candle_near_event_time"}
    base_price = base_row["close"]
    returns: dict[str, float | None] = {}
    for minutes in HORIZONS_MINUTES:
        target_time = scheduled + timedelta(minutes=minutes)
        candidate = min(rows, key=lambda row: abs((row["time"] - target_time).total_seconds()))
        if abs((candidate["time"] - target_time).total_seconds()) > 90:
            returns[f"return_{minutes}m"] = None
            continue
        returns[f"return_{minutes}m"] = (candidate["close"] - base_price) / base_price if base_price else None
    window_rows = [row for row in rows if base_row["time"] <= row["time"] <= base_row["time"] + timedelta(minutes=60)]
    highs = [row["high"] for row in window_rows]
    lows = [row["low"] for row in window_rows]
    mfe = (max(highs) - base_price) / base_price if highs and base_price else None
    mae = (base_price - min(lows)) / base_price if lows and base_price else None
    return {
        "status": "ok",
        "base_price": base_price,
        "base_time": base_row["time"].isoformat(),
        **returns,
        "max_favorable_excursion": mfe,
        "max_adverse_excursion": mae,
        "candles_in_window": len(window_rows),
    }


async def analyze_event_reaction(event: dict[str, Any], symbol: str, adapter: Any) -> dict[str, Any]:
    """Join a stored Forex Factory event with recent MT5 M1 candles for `symbol`. MT5 is the
    sole price source; Forex Factory supplies only the event context."""
    try:
        candles = await adapter.candles(symbol, "M1", count=1500, completed_only=True)
    except Exception as exc:
        return {"status": "insufficient_data", "reason": f"candle_fetch_failed:{exc.__class__.__name__}"}
    result = compute_price_reaction(event.get("scheduled_at_utc"), candles)
    result["event_id"] = event.get("id")
    result["symbol"] = symbol
    return result
