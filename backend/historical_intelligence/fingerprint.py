"""Deterministic pattern fingerprints for replayed historical setups (Part 1/2).

Built from the SAME production feature context every live candidate already gets --
StrategyContext (backend.mt5_strategies.context) and summarize_smc_evidence -- so a historical
fingerprint and a live candidate's own evidence are directly comparable without a second,
divergent feature-extraction path. This module never re-derives SMC/regime/trend from raw
candles itself; it only reads fields already computed by replay.py's strategy-reuse pipeline.

Peer grouping (the explicit "do not make fingerprints so specific that every historical setup is
unique" requirement) -- REVISED in fp-v2 after the original 18-dimension hash produced 549 peer
groups from 1,972 fingerprints (avg 3.6/group, far too fragmented for any statistic to reach a
usable sample size). `peer_group_hash` is now computed from a DELIBERATELY SMALL HARD-MATCH set
only -- symbol, direction, anchor strategy, and a COARSE regime bucket (TRENDING/BREAKOUT/
REVERSAL/RANGING/UNKNOWN, collapsing trending_up/trending_down and dropping the fine-grained
regime label from the hash) -- plus two BUCKETED numeric dimensions (ATR regime, stop-distance/
ATR bucket) that are already coarse (4 buckets each). Everything else that used to be hard-match
(session, htf trend labels, every individual SMC presence boolean, FVG/order-block state,
confidence band, planned RR bucket) moves to similarity.py's WEIGHTED scoring instead of the hard
hash -- two setups can now be genuinely comparable ("same peer group") even if they differ on
these finer dimensions, with that difference reflected as a similarity penalty rather than a hard
exclusion. The FULL fingerprint record still stores every field (bucketed and raw) regardless --
only the HASH got coarser, never the data.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from backend.historical_intelligence.orm import FINGERPRINT_VERSION, HISTORICAL_INTELLIGENCE_VERSION
from backend.mt5_strategies.context import StrategyContext, summarize_smc_evidence

# Session windows in UTC hours -- coarse, deliberately simple buckets (not the market_structure
# engine's own session-level detection, which serves a different purpose: identifying specific
# prior session highs/lows for session_breakout, not classifying "what time of day is it now").
_SESSION_WINDOWS = (
    (0, 7, "ASIAN"),
    (7, 12, "LONDON"),
    (12, 16, "NY_OVERLAP"),
    (16, 21, "NY"),
)


def time_of_day_bucket(dt: datetime) -> str:
    hour = dt.astimezone(timezone.utc).hour
    for start, end, label in _SESSION_WINDOWS:
        if start <= hour < end:
            return label
    return "OTHER"


# Collapses backend.adaptive_management.service.detect_regime's fine-grained regime labels
# (trending_up/trending_down/breakout/reversal/ranging/insufficient_data) into the broader
# hard-match bucket used by peer_group_hash -- directionality (trending_up vs trending_down)
# still fully survives in the stored `regime` field and in similarity.py's weighting, it's just
# no longer a hard peer-group split.
_REGIME_BROAD = {
    "trending_up": "TRENDING", "trending_down": "TRENDING",
    "breakout": "BREAKOUT", "reversal": "REVERSAL", "ranging": "RANGING",
}


def _regime_broad(regime: str | None) -> str:
    return _REGIME_BROAD.get(regime or "", "UNKNOWN")


def _percentile_rank(value: float, series: list[float]) -> float | None:
    if not series:
        return None
    below = sum(1 for v in series if v <= value)
    return round(100.0 * below / len(series), 1)


def _bucket_percentile(pct: float | None) -> str | None:
    if pct is None:
        return None
    if pct < 25:
        return "LOW"
    if pct < 75:
        return "NORMAL"
    if pct < 90:
        return "HIGH"
    return "EXTREME"


def _bucket_stop_distance_atr(ratio: float | None) -> str | None:
    if ratio is None:
        return None
    if ratio < 1.2:
        return "TIGHT"
    if ratio < 2.0:
        return "NORMAL"
    if ratio < 3.0:
        return "WIDE"
    return "VERY_WIDE"


def _bucket_rr(rr: float | None) -> str | None:
    if rr is None:
        return None
    if rr < 1.5:
        return "BELOW_MINIMUM"  # should not occur -- every strategy enforces a 1.5 RR floor
    if rr < 2.0:
        return "1.5-2.0"
    if rr < 3.0:
        return "2.0-3.0"
    return "3.0+"


def _fvg_state(ctx: StrategyContext) -> str:
    active = [z for z in ctx.m15_snapshot.imbalances if z.status != "mitigated"]
    if not active:
        return "none"
    directions = {str(z.direction) for z in active}
    if directions == {"bullish"}:
        return "bullish"
    if directions == {"bearish"}:
        return "bearish"
    return "mixed"


def _order_block_state(ctx: StrategyContext) -> str:
    active = [b for b in ctx.m15_snapshot.order_blocks if b.status not in {"mitigated", "invalidated"}]
    if not active:
        return "none"
    directions = {str(b.direction) for b in active}
    if directions == {"bullish"}:
        return "bullish"
    if directions == {"bearish"}:
        return "bearish"
    return "mixed"


def _liquidity_location(ctx: StrategyContext) -> str:
    sweeps = ctx.m15_snapshot.liquidity_sweeps
    if not sweeps:
        return "none"
    latest = max(sweeps, key=lambda s: s.bar_index)
    return str(latest.side)


def _support_resistance_context(ctx: StrategyContext) -> str | None:
    levels = ctx.m15_snapshot.liquidity_levels
    if not levels:
        return None
    price = float(ctx.m15_rows[-1]["close"]) if ctx.m15_rows else None
    if price is None:
        return None
    nearest = min(levels, key=lambda lv: abs(float(lv.level) - price))
    atr = float(ctx.atr_m15) if ctx.atr_m15 else None
    if atr and atr > 0 and abs(float(nearest.level) - price) <= max(float(nearest.tolerance), atr):
        return f"near_{nearest.side}"
    return "away_from_level"


def peer_group_hash(fields: dict[str, Any]) -> str:
    payload = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def build_fingerprint(
    *,
    ctx: StrategyContext,
    strategy_id: str,
    contributing_strategies: list[str],
    strategy_family: str | None,
    strategy_version: str,
    source_quality_tier: str,
    provider: str,
    proxy: bool,
    entry: float,
    stop_loss: float,
    take_profit: float,
    entry_time: datetime,
    confidence_band: str | None = None,
    real_spread: Decimal | None = None,
    economic_event_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Builds the full fingerprint field dict (ready to construct a HistoricalPatternFingerprintORM
    row) plus its `peer_group_hash`. Pure function of an already-built StrategyContext and the
    setup's own geometry -- makes no DB/broker calls, matching replay.py's own purity."""
    smc = summarize_smc_evidence(ctx)

    atr = float(ctx.atr_m15) if ctx.atr_m15 else None
    m15_bars = ctx.m15_rows
    atr_series: list[float] = []
    vol_series: list[float] = []
    vol_ratio = float(atr / float(m15_bars[-1]["close"])) if atr and m15_bars and float(m15_bars[-1]["close"]) else None
    if atr is not None and len(m15_bars) >= 15:
        from backend.market_structure.bar_utils import average_true_range, normalize_bars

        normalized = normalize_bars(m15_bars, symbol=ctx.broker_symbol, timeframe="M15")
        atrs = average_true_range(normalized, 14)  # same length/index alignment as m15_bars
        for bar, a in zip(m15_bars, atrs):
            if a is None:
                continue
            atr_series.append(float(a))
            close = float(bar["close"])
            if close:
                vol_series.append(float(a) / close)
    atr_percentile = _percentile_rank(atr, atr_series) if atr is not None else None
    volatility_percentile = _percentile_rank(vol_ratio, vol_series) if vol_ratio is not None else None

    stop_distance = abs(entry - stop_loss)
    stop_distance_atr_ratio = (stop_distance / atr) if atr and atr > 0 else None
    planned_rr = abs(take_profit - entry) / stop_distance if stop_distance > 0 else None

    is_real_spread = source_quality_tier == "SNAPSHOT" and real_spread is not None
    entry_time = entry_time if entry_time.tzinfo else entry_time.replace(tzinfo=timezone.utc)

    # HARD-MATCH set only (fp-v2) -- symbol/direction/strategy/strategy_version (an exact-match
    # necessity: statistics must never blend setups from different strategy-logic versions) plus
    # a coarse regime bucket and two already-coarse numeric buckets. Session, HTF trend labels,
    # every individual SMC presence boolean, FVG/order-block state, confidence band, and planned
    # RR bucket are all still computed and stored below (in `fields`) but are NO LONGER part of
    # the hash -- they are similarity.py's WEIGHTED dimensions instead, so two setups differing
    # only on those finer points can still be found as comparable neighbors rather than being
    # hard-excluded from each other's peer group entirely.
    peer_fields = {
        "symbol": ctx.symbol.upper(),
        "direction": _direction_from_prices(entry, stop_loss),
        "anchor_strategy": strategy_id,
        "strategy_version": strategy_version,
        "regime_broad": _regime_broad(ctx.regime),
        "atr_regime": _bucket_percentile(atr_percentile),
        "stop_distance_atr_bucket": _bucket_stop_distance_atr(stop_distance_atr_ratio),
    }
    similarity_fields = {
        "session": time_of_day_bucket(entry_time),
        "m15_trend": str(ctx.m15_snapshot.trend.state) if ctx.m15_snapshot.trend else None,
        "h1_trend": ctx.htf_trend_h1,
        "h4_trend": ctx.htf_trend_h4,
        "bos_present": smc["bos_present"],
        "choch_present": smc["choch_present"],
        "mss_present": smc["mss_present"],
        "displacement_present": smc["displacement_present"],
        "liquidity_sweep_present": smc["liquidity_sweep_present"],
        "fvg_state": _fvg_state(ctx),
        "order_block_state": _order_block_state(ctx),
    }

    fields: dict[str, Any] = {
        "historical_intelligence_version": HISTORICAL_INTELLIGENCE_VERSION,
        "strategy_version": strategy_version,
        "fingerprint_version": FINGERPRINT_VERSION,
        "source_quality_tier": source_quality_tier,
        "provider": provider.upper(),
        "proxy": proxy,
        "canonical_symbol": ctx.symbol.upper(),
        "direction": peer_fields["direction"],
        "anchor_strategy": strategy_id,
        "contributing_strategies": contributing_strategies,
        "strategy_family": strategy_family,
        "regime": ctx.regime,
        "regime_broad": peer_fields["regime_broad"],
        "session": similarity_fields["session"],
        "confidence_band": confidence_band,
        "m15_trend": similarity_fields["m15_trend"],
        "h1_trend": ctx.htf_trend_h1,
        "h4_trend": ctx.htf_trend_h4,
        "bos_present": smc["bos_present"],
        "choch_present": smc["choch_present"],
        "mss_present": smc["mss_present"],
        "displacement_present": smc["displacement_present"],
        "liquidity_sweep_present": smc["liquidity_sweep_present"],
        "liquidity_location": _liquidity_location(ctx),
        "fvg_state": similarity_fields["fvg_state"],
        "order_block_state": similarity_fields["order_block_state"],
        "premium_discount_position": smc.get("premium_discount_position"),
        "support_resistance_context": _support_resistance_context(ctx),
        "atr_regime": peer_fields["atr_regime"],
        "atr_percentile": atr_percentile,
        "volatility_regime": _bucket_percentile(volatility_percentile),
        "spread_regime": "real_pending_classification" if is_real_spread else "unknown",
        "stop_distance_atr_bucket": peer_fields["stop_distance_atr_bucket"],
        "planned_rr_bucket": _bucket_rr(planned_rr),
        "day_of_week": entry_time.weekday(),
        "time_of_day_bucket": similarity_fields["session"],
        "economic_event_context": economic_event_context or {},
        "entry": entry,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "entry_time": entry_time,
        "peer_group_hash": peer_group_hash(peer_fields),
    }
    # Real spread regime, once we know we trust it (kept separate from the placeholder above so
    # the peer-group hash computation -- which never sees spread at all -- is unaffected by
    # this classification).
    if is_real_spread:
        spread_f = float(real_spread)
        ratio = (spread_f / atr) if atr and atr > 0 else None
        if ratio is not None:
            fields["spread_regime"] = "TIGHT" if ratio < 0.15 else ("NORMAL" if ratio < 0.35 else "WIDE")
        else:
            fields["spread_regime"] = "unknown"

    return fields


def _direction_from_prices(entry: float, stop_loss: float) -> str:
    return "LONG" if stop_loss < entry else "SHORT"
