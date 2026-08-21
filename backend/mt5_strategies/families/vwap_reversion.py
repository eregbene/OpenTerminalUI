"""vwap_reversion -- least trustworthy strategy in the 8-year audit (-0.056R pooled, sign flips
across window sizes). Session-anchored cumulative VWAP with a volume-confirmed deviation-based
reversion signal.

2026-08-21 Phase 3 blueprint, Section 2: fixes a real implementation bug, not a design question.
The pre-fix version's docstring claimed "session-anchored cumulative VWAP" but the cumsum actually
ran over whatever ~100-bar window happened to be fetched, with NO reset at any boundary -- the
"session anchor" never existed in the code, only in the comment. A true VWAP must anchor at a
fixed point; a rolling window with no reset drifts depending on when the fetch happened to start,
which is the audit's own leading hypothesis for the cross-window sign instability. Fixed by
resetting the cumulative sums at the most recent calendar-day boundary present in ctx.m15_rows
(daily-anchored VWAP -- the standard, unambiguous FX convention; sub-session anchoring was
considered but rejected as needing session-window plumbing this fix doesn't otherwise require).
Unconditional fix, no new feature flag -- this corrects what the code always claimed to do, it
does not add new behavior.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pandas as pd

from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families._shared import (
    _dynamic_stop,
    _eqh_eql_touch_count,
    _geometry_metadata,
    _no_signal,
    _signal,
    _spread_within_safety_buffer,
    _squeeze_evidence,
)
from backend.mt5_strategies.models import StrategySignal

_STRATEGY_ID = "vwap_reversion"
_MIN_SESSION_BARS = 5


def _session_anchor_index(rows: list[dict]) -> int:
    """Index of the first bar belonging to the same calendar date (UTC, matching how every M15
    row's `time` field is already stored) as the most recent bar -- the day-open anchor a true
    VWAP resets at. Walks backward from the end rather than grouping the whole list, since only
    the CURRENT day's boundary matters here."""
    if not rows:
        return 0
    last_date = datetime.fromisoformat(str(rows[-1]["time"])).date()
    for i in range(len(rows) - 1, -1, -1):
        row_date = datetime.fromisoformat(str(rows[i]["time"])).date()
        if row_date != last_date:
            return i + 1
    return 0


def evaluate_vwap_reversion(ctx: StrategyContext) -> StrategySignal:
    """Session-anchored cumulative VWAP (close*volume cumsum / volume cumsum, reset at the
    current calendar day's open -- see _session_anchor_index) with a volume-confirmed deviation-
    based reversion signal. NOT the mislabeled intelligence/trading::VWAPStrategy, which the
    original audit found actually uses EMA20."""
    if not _spread_within_safety_buffer(ctx):
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="SPREAD_SAFETY_BUFFER_EXCEEDED")
    if len(ctx.m15_rows) < 30:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="insufficient_history")

    anchor = _session_anchor_index(ctx.m15_rows)
    session_rows = ctx.m15_rows[anchor:]
    if len(session_rows) < _MIN_SESSION_BARS:
        # Too early in the current session for a meaningful anchor -- fail CLOSED (no signal),
        # never silently fall back to the pre-fix whole-window behavior this function replaces.
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="insufficient_session_history")

    closes = pd.Series([float(r["close"]) for r in session_rows])
    volumes = pd.Series([float(r.get("tick_volume") or 0) for r in session_rows]).replace(0, pd.NA)
    if volumes.isna().all():
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="no_volume_data")
    cumulative_vwap = (closes * volumes).cumsum() / volumes.cumsum()
    vwap_now = float(cumulative_vwap.iloc[-1])
    price = float(closes.iloc[-1])
    avg_volume = volumes.fillna(0).rolling(20, min_periods=1).mean().iloc[-1]
    volume_now = float(volumes.fillna(0).iloc[-1])
    deviation_pct = (price - vwap_now) / vwap_now if vwap_now else 0.0
    volume_confirmed = volume_now > float(avg_volume) * 1.3 if avg_volume else False
    if deviation_pct < -0.0015 and volume_confirmed:
        direction = "LONG"
    elif deviation_pct > 0.0015:
        direction = "SHORT"
    else:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="price_not_deviated_from_vwap")
    entry = Decimal(str(price))
    atr = ctx.atr_m15 or Decimal("0.0001")
    stop, stop_reason = _dynamic_stop(ctx, direction, entry, None, atr, min_atr_mult=1.2, max_atr_mult=1.2)
    if stop is None:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=stop_reason)
    target = Decimal(str(vwap_now))
    eqh_eql_side = "sell_side" if direction == "LONG" else "buy_side"
    evidence = {
        "vwap": vwap_now, "deviation_pct": deviation_pct, "volume_confirmed": volume_confirmed,
        "session_anchor_bar_count": len(session_rows),
        "eqh_eql_touch_count": _eqh_eql_touch_count(ctx, side=eqh_eql_side, price=price, atr=float(atr)),
    }
    evidence.update(_squeeze_evidence(ctx))
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", direction=direction, strength=58.0,
                    entry=entry, stop=stop, target=target, evidence=evidence,
                    metadata=_geometry_metadata(ctx, entry, stop, None, atr, 1.2, 1.2))
