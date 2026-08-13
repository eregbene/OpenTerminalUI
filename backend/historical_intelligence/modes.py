"""Historical Intelligence's three-mode activation gate.

OFF          -- default. No historical-intelligence code path is consulted by the live entry
                engine or the Adaptive Trade Manager. Ingestion/replay/statistics can still run
                (they are research/preparation work), but nothing they produce is read by any
                live decision.
SHADOW       -- historical intelligence is computed and attached to every live candidate/
                management decision, logged for comparison against what the engine actually
                did, but is NEVER allowed to change a decision: it cannot reject a trade, resize
                a position, alter confidence/ranking, or mutate a broker order/SL/TP. This is
                temporary validation, not the end state.
DEMO_ACTIVE  -- historical intelligence is permitted to influence live DEMO decisions (candidate
                ranking/deferment, fusion confirmation, entry quality, and Adaptive Manager
                HOLD/BE/SL-protection/trailing/partial-profit/exit choices), gated by sample
                size and statistical reliability -- never a raw "win_rate < X -> reject" rule.
                Still DEMO-only; this module has no opinion on and no path to live-money trading
                (MT5_LIVE_TRADING_ENABLED remains the independent, unrelated live-trading gate).

Promotion OFF -> SHADOW -> DEMO_ACTIVE is a deliberate, manual operator decision (env var), never
automatic -- there is no code path anywhere that flips this based on observed performance.
"""
from __future__ import annotations

import os
from enum import Enum


class HistoricalIntelligenceMode(str, Enum):
    OFF = "OFF"
    SHADOW = "SHADOW"
    DEMO_ACTIVE = "DEMO_ACTIVE"


_VALID = {mode.value for mode in HistoricalIntelligenceMode}


def current_mode() -> HistoricalIntelligenceMode:
    raw = (os.getenv("HISTORICAL_INTELLIGENCE_MODE") or "OFF").strip().upper()
    if raw not in _VALID:
        return HistoricalIntelligenceMode.OFF
    return HistoricalIntelligenceMode(raw)


def is_at_least(required: HistoricalIntelligenceMode) -> bool:
    order = [HistoricalIntelligenceMode.OFF, HistoricalIntelligenceMode.SHADOW, HistoricalIntelligenceMode.DEMO_ACTIVE]
    return order.index(current_mode()) >= order.index(required)


def shadow_enabled() -> bool:
    """True in SHADOW or DEMO_ACTIVE -- i.e. whenever historical intelligence should be
    COMPUTED and LOGGED, regardless of whether it's also allowed to influence a decision."""
    return is_at_least(HistoricalIntelligenceMode.SHADOW)


def demo_active_enabled() -> bool:
    """True only in DEMO_ACTIVE -- the single gate every future live-influencing call site
    (candidate ranking/deferment, Adaptive Manager action selection) must check before letting
    historical intelligence change a decision rather than merely log one."""
    return is_at_least(HistoricalIntelligenceMode.DEMO_ACTIVE)
