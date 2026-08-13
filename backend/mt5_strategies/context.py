"""Shared, pure (no broker I/O) feature context for the MT5 multi-strategy layer.

Market Data -> features -> strategies is the first two stages of the target architecture
(Part 0's pipeline diagram). Candle fetching stays in backend/brokers/mt5/autonomous.py
(async, adapter-bound); everything here is a deterministic function of already-fetched data,
matching the existing design language of backend/brokers/mt5/confidence.py.

Reuses:
  - backend.market_structure.engine.analyze_bars for M15 SMC/ICT structure (Part 7 -- BOS/
    CHoCH/MSS/displacement/liquidity/FVG/order-blocks/dealing-ranges/session-levels), and for
    H1/H4 trend classification (avoids a second trend-detection implementation).
  - backend.adaptive_management.service.detect_regime for market regime (Part 5 -- "choose/
    normalize one existing regime system", not a fifth one).
  - backend.market_structure.bar_utils.average_true_range for ATR, consistent with the SMC
    engine's own math rather than yet another ATR variant.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from backend.adaptive_management.service import detect_regime
from backend.market_structure.bar_utils import average_true_range, normalize_bars
from backend.market_structure.engine import analyze_bars
from backend.market_structure.models import MarketStructureSnapshot, TrendLabel


@dataclass(frozen=True)
class StrategyContext:
    symbol: str
    broker_symbol: str
    generated_at: datetime
    regime: str
    regime_confidence: float
    htf_trend_h4: str
    htf_trend_h1: str
    m15_snapshot: MarketStructureSnapshot
    m15_rows: list[dict[str, Any]]
    h1_rows: list[dict[str, Any]]
    h4_rows: list[dict[str, Any]]
    atr_m15: Decimal | None
    bid: Decimal
    ask: Decimal
    spread: Decimal
    # Broker minimum stop distance in price units (symbol_info.trade_stops_level *
    # symbol_info.point), None when unknown -- see build_strategy_context's `symbol_info` param
    # and families.py::_dynamic_stop. Never guessed; only ever set from real broker metadata.
    broker_min_stop_distance: Decimal | None = None
    # Never read by any strategy (only htf_trend_h1/h4's derived label above is) -- carried here
    # purely so the caller (autonomous.py::_screen, running in the main event loop) can write a
    # freshly-computed H1/H4 snapshot back to the Redis deterministic-computation cache after
    # this context returns from its asyncio.to_thread() worker. Redis calls never happen inside
    # this module or inside the thread pool -- see redis_layer.py's module docstring for why.
    h1_snapshot: MarketStructureSnapshot | None = None
    h4_snapshot: MarketStructureSnapshot | None = None


def quick_regime(m15_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The cheap half of context building, split out from build_strategy_context (Part 3):
    backend.adaptive_management.service.detect_regime is pure candle-list math -- it never
    calls analyze_bars -- so regime (and therefore which strategy families are even
    regime-compatible, see models.regime_compatible) can be known BEFORE paying for the three
    analyze_bars() SMC passes. Callers use this to skip the expensive build entirely when no
    strategy family would be evaluated anyway."""
    return detect_regime(m15_rows)


def cheap_prefilter(*, already_ineligible: bool, m15_rows: list[dict[str, Any]], spread: Decimal | None) -> tuple[bool, str]:
    """Strategy-neutral first-pass screen (Part 3): cheap, no analyze_bars, no per-strategy
    logic of any kind -- in particular this NEVER looks at MTFAI1's own trend/SMA direction,
    so mean-reversion, liquidity-sweep, and other non-trend strategies are never pre-excluded
    by a trend-only gate. Only decides whether the full, expensive multi-timeframe SMC context
    is worth building at all for this instrument this cycle."""
    if already_ineligible:
        return False, "ALREADY_INELIGIBLE"
    if len(m15_rows) < 20:
        return False, "INSUFFICIENT_HISTORY"
    window = m15_rows[-20:]
    try:
        highs = [float(row["high"]) for row in window]
        lows = [float(row["low"]) for row in window]
    except (KeyError, TypeError, ValueError):
        return False, "MALFORMED_CANDLES"
    price_range = max(highs) - min(lows)
    if price_range <= 0:
        return False, "DEAD_MARKET"
    range_proxy = sum(h - l for h, l in zip(highs, lows)) / len(window)
    if range_proxy <= 0:
        return False, "DEAD_MARKET"
    if spread is not None:
        spread_f = float(spread)
        if spread_f > 0 and (spread_f / range_proxy) > 0.5:
            return False, "SPREAD_TOO_WIDE"
    return True, ""


def build_strategy_context(
    *,
    symbol: str,
    broker_symbol: str,
    m15_rows: list[dict[str, Any]],
    h1_rows: list[dict[str, Any]],
    h4_rows: list[dict[str, Any]],
    bid: Decimal,
    ask: Decimal,
    spread: Decimal,
    now: datetime | None = None,
    regime_info: dict[str, Any] | None = None,
    symbol_info: Any = None,
    m15_snapshot: MarketStructureSnapshot | None = None,
    h1_snapshot: MarketStructureSnapshot | None = None,
    h4_snapshot: MarketStructureSnapshot | None = None,
) -> StrategyContext | None:
    """Returns None (not a partial/degraded context) when there isn't enough history for the
    SMC engine's minimum swing window -- callers must skip strategy evaluation entirely rather
    than let every strategy independently re-derive its own "not enough data" fallback.

    `regime_info`: pass the already-computed quick_regime() result to avoid a second (cheap but
    redundant) detect_regime call when the caller ran the cheap pre-check first; recomputed
    internally when omitted so existing callers are unaffected.

    `symbol_info`: optional, duck-typed (any object with `.trade_stops_level`/`.point`
    attributes, e.g. MT5Symbol) -- when provided, derives `broker_min_stop_distance` so every
    strategy's stop construction can respect the broker's actual minimum stop level instead of
    only an ATR/spread-derived floor. Omitted (None) callers get identical behavior to before
    this parameter existed.

    `m15_snapshot`/`h1_snapshot`/`h4_snapshot`: optional pre-computed analyze_bars() results
    (Part 5's Redis deterministic-computation cache -- see backend.mt5_strategies.redis_layer
    .cached_analyze_bars). analyze_bars() is a pure function of its input rows, so a caller-
    supplied snapshot is byte-identical to what recomputing it here would produce; this function
    itself makes no Redis calls (it runs inside autonomous.py's asyncio.to_thread() worker,
    which must never touch the main event loop's Redis connection -- see redis_layer.py's
    module docstring). Omitted (None, the default) callers get identical behavior to before
    these parameters existed."""
    if len(m15_rows) < 20 or len(h1_rows) < 10 or len(h4_rows) < 10:
        return None
    now = now or datetime.now(timezone.utc)

    m15_snapshot = m15_snapshot if m15_snapshot is not None else analyze_bars(m15_rows, symbol=broker_symbol, timeframe="M15")
    h1_snapshot = h1_snapshot if h1_snapshot is not None else analyze_bars(h1_rows, symbol=broker_symbol, timeframe="H1")
    h4_snapshot = h4_snapshot if h4_snapshot is not None else analyze_bars(h4_rows, symbol=broker_symbol, timeframe="H4")

    regime_info = regime_info if regime_info is not None else detect_regime(m15_rows)

    m15_bars = normalize_bars(m15_rows, symbol=broker_symbol, timeframe="M15")
    atrs = average_true_range(m15_bars, 14)
    atr_m15 = atrs[-1] if atrs else None

    broker_min_stop_distance = None
    if symbol_info is not None:
        stops_level = getattr(symbol_info, "trade_stops_level", None)
        point = getattr(symbol_info, "point", None)
        if stops_level and point:
            try:
                broker_min_stop_distance = Decimal(str(stops_level)) * Decimal(str(point))
            except Exception:
                broker_min_stop_distance = None

    return StrategyContext(
        symbol=symbol,
        broker_symbol=broker_symbol,
        generated_at=now,
        regime=str(regime_info.get("regime") or "insufficient_data"),
        regime_confidence=float(regime_info.get("confidence") or 0.0),
        htf_trend_h4=str(h4_snapshot.trend.state if h4_snapshot.trend else TrendLabel.UNKNOWN),
        htf_trend_h1=str(h1_snapshot.trend.state if h1_snapshot.trend else TrendLabel.UNKNOWN),
        m15_snapshot=m15_snapshot,
        m15_rows=m15_rows,
        h1_rows=h1_rows,
        h4_rows=h4_rows,
        atr_m15=atr_m15,
        bid=bid,
        ask=ask,
        spread=spread,
        broker_min_stop_distance=broker_min_stop_distance,
        h1_snapshot=h1_snapshot,
        h4_snapshot=h4_snapshot,
    )


def summarize_smc_evidence(ctx: StrategyContext) -> dict[str, Any]:
    """Standardized SMC/ICT evidence summary (Part 18) attached to EVERY candidate this
    cycle -- MTFAI1's own row included -- not just the new strategy families, so the
    calibration layer can eventually ask "does BOS/CHoCH/MSS/displacement/liquidity-sweep/FVG/
    order-block presence predict outcome" across the whole candidate pool uniformly. Booleans
    only reflect PRESENCE in the M15 snapshot near the current bar; per-strategy evidence
    (which break id, which sweep) still lives on that strategy's own StrategySignal.evidence."""
    snap = ctx.m15_snapshot
    recent_window = max(0, len(ctx.m15_rows) - 6)
    break_kinds = {b.break_kind for b in snap.breaks if b.bar_index >= recent_window}
    price = float(ctx.m15_rows[-1]["close"]) if ctx.m15_rows else None
    premium_discount_position = None
    if price is not None:
        for zone in snap.premium_discount_zones:
            if zone.price_low is not None and zone.price_high is not None and float(zone.price_low) <= price <= float(zone.price_high):
                premium_discount_position = zone.zone_name
                break
    return {
        "bos_present": "bos" in break_kinds,
        "choch_present": "choch" in break_kinds,
        "mss_present": "mss" in break_kinds,
        "displacement_present": any(d.bar_index >= recent_window for d in snap.displacements),
        "liquidity_sweep_present": any(s.bar_index >= recent_window for s in snap.liquidity_sweeps),
        "fvg_present": any(z.status != "mitigated" for z in snap.imbalances),
        "order_block_present": any(b.status not in {"mitigated", "invalidated"} for b in snap.order_blocks),
        "premium_discount_position": premium_discount_position,
        "session_levels_available": bool(snap.session_levels),
        "htf_direction_h4": ctx.htf_trend_h4,
        "htf_direction_h1": ctx.htf_trend_h1,
    }
