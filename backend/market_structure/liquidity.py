from __future__ import annotations

from decimal import Decimal

from backend.market_structure.bar_utils import StructureBar, average_true_range
from backend.market_structure.configuration import MarketStructureConfig
from backend.market_structure.models import ConceptStatus, Direction, LiquidityLevel, LiquiditySide, LiquiditySweep, SwingPoint, stable_id


def _tolerance(level: Decimal, atr: Decimal | None, config: MarketStructureConfig) -> Decimal:
    vals = [Decimal("0")]
    if config.equal_levels.tolerance_absolute is not None:
        vals.append(Decimal(str(config.equal_levels.tolerance_absolute)))
    if config.equal_levels.tolerance_percent is not None:
        vals.append(abs(level) * Decimal(str(config.equal_levels.tolerance_percent)))
    if atr is not None:
        vals.append(atr * Decimal(str(config.equal_levels.tolerance_atr)))
    return max(vals)


def detect_liquidity_levels(
    bars: list[StructureBar],
    swings: list[SwingPoint],
    config: MarketStructureConfig,
    *,
    symbol: str,
    timeframe: str,
    source_dataset_id: str | None = None,
) -> list[LiquidityLevel]:
    atrs = average_true_range(bars, config.displacement.atr_period)
    levels: list[LiquidityLevel] = []
    for swing in swings:
        atr = atrs[swing.bar_index] if swing.bar_index < len(atrs) else None
        side = LiquiditySide.BUY_SIDE if swing.swing_type == "high" else LiquiditySide.SELL_SIDE
        levels.append(
            LiquidityLevel(
                id=stable_id("liq", symbol, timeframe, swing.id, config.configuration_hash()),
                symbol=symbol,
                timeframe=timeframe,
                start_time=swing.start_time,
                end_time=swing.end_time,
                detected_time=swing.confirmation_time or swing.detected_time,
                confirmation_time=swing.confirmation_time,
                price_low=swing.price,
                price_high=swing.price,
                direction=Direction.BULLISH if side == LiquiditySide.BUY_SIDE else Direction.BEARISH,
                status=ConceptStatus.ACTIVE,
                quality_score=0.65,
                configuration_version=config.version,
                configuration_hash=config.configuration_hash(),
                source_dataset_id=source_dataset_id,
                supporting_bar_indexes=[swing.bar_index],
                supporting_event_ids=[swing.id],
                side=side,
                level=swing.price,
                source="confirmed_swing",
                tolerance=_tolerance(swing.price, atr, config),
            )
        )
    return levels


def detect_equal_levels(
    bars: list[StructureBar],
    swings: list[SwingPoint],
    config: MarketStructureConfig,
    *,
    symbol: str,
    timeframe: str,
    source_dataset_id: str | None = None,
) -> list[LiquidityLevel]:
    """Equal-Highs/Equal-Lows liquidity pools (LuxAlgo's public EQH/EQL concept, independently
    implemented -- see docs/EXTERNAL_INDICATOR_REDUNDANCY_AUDIT.md addendum). Genuinely missing
    from this engine before now: detect_liquidity_levels above treats every swing as its own
    single-touch level (touch_count always defaults to 1, never incremented), and detect_swings
    actively COLLAPSES a new pivot into the prior one when they're too close together rather than
    recognizing repeated near-equal touches as a strengthened liquidity pool -- confirmed by
    config.equal_levels.minimum_touches (declared, `>= 2`) having zero call sites anywhere in this
    codebase before this function. This reuses that same already-declared, previously-dead config
    knob, and the existing LiquidityLevel model's own touch_count/source fields (also previously
    always 1 / "confirmed_swing") -- no new model, no schema change.

    Clustering: chronological single pass per swing_type ("high"/"low" separately -- highs and
    lows never merge into the same pool). A new swing joins the most recent still-open cluster of
    the same type if it falls within that cluster's tolerance band (the SAME _tolerance() used for
    single-touch sweep detection, so an EQH/EQL pool's own tolerance is never wider or narrower
    than what already governs whether a price "reclaimed" or "breached" a single swing); otherwise
    it starts a new cluster. `level` is the running average of all clustered swing prices (not the
    first touch, not the last) -- keeps the pool centered as more touches confirm it, matching how
    LuxAlgo's own indicator re-centers its equal-level line on each new confirming touch.

    No-lookahead: a cluster is only emitted once it reaches config.equal_levels.minimum_touches
    members, with confirmation_time set to the confirming (Nth) swing's own confirmation_time --
    never backdated to the first touch. Before that Nth touch, the pool simply does not exist yet
    from a replay's point of view, exactly like every other concept in this engine."""
    atrs = average_true_range(bars, config.displacement.atr_period)
    min_touches = config.equal_levels.minimum_touches
    # Bounds the active-cluster scan to the same ~100-bar window every live cycle already fetches
    # (replay.py/autonomous.py fetch M15 with count=100) -- keeps live and backfilled/replayed
    # results identical (a live cycle never sees an older swing to cluster against anyway) and
    # avoids the unbounded-list O(n^2) scan detect_liquidity_sweeps already guards against
    # (`active = remaining[-500:]`, same file) over a full-history backfill pass.
    _LOOKBACK_BARS = 100

    class _Cluster:
        __slots__ = ("swings", "sum_price")

        def __init__(self, first: SwingPoint) -> None:
            self.swings: list[SwingPoint] = [first]
            self.sum_price: Decimal = first.price

        @property
        def level(self) -> Decimal:
            return self.sum_price / Decimal(len(self.swings))

    levels: list[LiquidityLevel] = []
    clusters: dict[str, list[_Cluster]] = {"high": [], "low": []}
    emitted_at_count: dict[int, int] = {}  # id(cluster) -> touch count already emitted, avoids re-emitting on every subsequent touch

    for swing in sorted(swings, key=lambda s: s.bar_index):
        if swing.swing_type not in clusters:
            continue
        atr = atrs[swing.bar_index] if swing.bar_index < len(atrs) else None
        active = [c for c in clusters[swing.swing_type] if swing.bar_index - c.swings[-1].bar_index <= _LOOKBACK_BARS]
        clusters[swing.swing_type] = active
        joined: _Cluster | None = None
        for cluster in active:
            tol = _tolerance(cluster.level, atr, config)
            if abs(swing.price - cluster.level) <= tol:
                joined = cluster
                break
        if joined is None:
            clusters[swing.swing_type].append(_Cluster(swing))
            continue
        joined.swings.append(swing)
        joined.sum_price += swing.price
        touch_count = len(joined.swings)
        if touch_count >= min_touches and emitted_at_count.get(id(joined), 0) < touch_count:
            emitted_at_count[id(joined)] = touch_count
            side = LiquiditySide.BUY_SIDE if swing.swing_type == "high" else LiquiditySide.SELL_SIDE
            level_price = joined.level
            member_indexes = [s.bar_index for s in joined.swings]
            levels.append(
                LiquidityLevel(
                    id=stable_id("eql", symbol, timeframe, swing.swing_type, swing.id, touch_count, config.configuration_hash()),
                    symbol=symbol,
                    timeframe=timeframe,
                    start_time=joined.swings[0].start_time,
                    end_time=swing.end_time,
                    detected_time=swing.confirmation_time or swing.detected_time,
                    confirmation_time=swing.confirmation_time,
                    price_low=level_price,
                    price_high=level_price,
                    direction=Direction.BULLISH if side == LiquiditySide.BUY_SIDE else Direction.BEARISH,
                    status=ConceptStatus.ACTIVE,
                    quality_score=min(1.0, 0.6 + 0.1 * touch_count),
                    configuration_version=config.version,
                    configuration_hash=config.configuration_hash(),
                    source_dataset_id=source_dataset_id,
                    supporting_bar_indexes=member_indexes,
                    supporting_event_ids=[s.id for s in joined.swings],
                    side=side,
                    level=level_price,
                    touch_count=touch_count,
                    source="equal_level",
                    tolerance=_tolerance(level_price, atr, config),
                )
            )
    return levels


def detect_liquidity_sweeps(
    bars: list[StructureBar],
    levels: list[LiquidityLevel],
    config: MarketStructureConfig,
    *,
    symbol: str,
    timeframe: str,
    source_dataset_id: str | None = None,
) -> list[LiquiditySweep]:
    sweeps: list[LiquiditySweep] = []
    active: list[LiquidityLevel] = []
    ordered = sorted(levels, key=lambda item: item.confirmation_time or item.detected_time)
    cursor = 0
    for idx, bar in enumerate(bars):
        while cursor < len(ordered):
            level = ordered[cursor]
            if not level.confirmation_time or level.confirmation_time >= bar.close_time:
                break
            active.append(level)
            cursor += 1
        remaining: list[LiquidityLevel] = []
        for level in active:
            if level.side == LiquiditySide.BUY_SIDE:
                breached = bar.high > level.level + level.tolerance
                reclaimed = bar.close < level.level
                accepted = bar.close > level.level + level.tolerance
                penetration = bar.high - level.level
                swept_price = bar.high
                direction = Direction.BEARISH
            else:
                breached = bar.low < level.level - level.tolerance
                reclaimed = bar.close > level.level
                accepted = bar.close < level.level - level.tolerance
                penetration = level.level - bar.low
                swept_price = bar.low
                direction = Direction.BULLISH
            if breached and reclaimed:
                item = LiquiditySweep(
                    id=stable_id("swp", symbol, timeframe, level.id, idx, config.configuration_hash()),
                    symbol=symbol,
                    timeframe=timeframe,
                    start_time=bar.open_time,
                    end_time=bar.close_time,
                    detected_time=bar.close_time,
                    confirmation_time=bar.close_time,
                    price_low=min(level.level, swept_price),
                    price_high=max(level.level, swept_price),
                    direction=direction,
                    status=ConceptStatus.SWEPT,
                    strength=float(penetration / max(level.tolerance, Decimal("0.00000001"))),
                    quality_score=0.75,
                    configuration_version=config.version,
                    configuration_hash=config.configuration_hash(),
                    source_dataset_id=source_dataset_id,
                    supporting_bar_indexes=level.supporting_bar_indexes + [idx],
                    supporting_event_ids=[level.id],
                    level_id=level.id,
                    side=level.side,
                    swept_price=swept_price,
                    reclaim_price=bar.close,
                    penetration=penetration,
                    bar_index=idx,
                )
                sweeps.append(item)
                continue
            if accepted:
                continue
            remaining.append(level)
        active = remaining[-500:]
    return sweeps
