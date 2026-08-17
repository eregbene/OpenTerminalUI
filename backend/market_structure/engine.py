from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.market_data.models import AssetClass
from backend.market_structure.bar_utils import StructureBar, normalize_bars
from backend.market_structure.configuration import MarketStructureConfig, get_profile
from backend.market_structure.dealing_range import build_dealing_ranges
from backend.market_structure.displacement import detect_displacements
from backend.market_structure.events import build_events
from backend.market_structure.explanations import explain_snapshot
from backend.market_structure.imbalance import detect_fair_value_gaps
from backend.market_structure.liquidity import detect_equal_levels, detect_liquidity_levels, detect_liquidity_sweeps
from backend.market_structure.models import EngineState, FeatureRow, MarketStructureSnapshot, StructureBreakKind, stable_id
from backend.market_structure.scoring import score_snapshot
from backend.market_structure.serialization import build_overlays
from backend.market_structure.sessions import detect_session_levels
from backend.market_structure.structure import detect_structure_breaks
from backend.market_structure.swings import detect_swings
from backend.market_structure.trend import classify_trend
from backend.market_structure.zones import detect_order_blocks


class MarketStructureEngine:
    def __init__(self, config: MarketStructureConfig | None = None) -> None:
        self.config = config or get_profile("balanced")

    def analyze(
        self,
        bars: list[StructureBar],
        *,
        symbol: str,
        timeframe: str,
        instrument_id: str | None = None,
        asset_class: AssetClass = AssetClass.UNKNOWN,
        source_dataset_id: str | None = None,
    ) -> MarketStructureSnapshot:
        warnings: list[str] = []
        if self.config.input.use_completed_bars_only:
            incomplete = [bar for bar in bars if not bar.is_complete]
            if incomplete and not self.config.input.allow_incomplete_last_bar:
                warnings.append(f"ignored {len(incomplete)} incomplete bar(s)")
                bars = [bar for bar in bars if bar.is_complete]
        now = datetime.now(timezone.utc)
        cfg_hash = self.config.configuration_hash()
        snapshot_id = stable_id("mssnap", symbol, timeframe, source_dataset_id or len(bars), cfg_hash)
        swings = detect_swings(bars, self.config, symbol=symbol, timeframe=timeframe, source_dataset_id=source_dataset_id)
        displacements = detect_displacements(bars, self.config, symbol=symbol, timeframe=timeframe, source_dataset_id=source_dataset_id)
        trend = classify_trend(bars, swings, self.config, symbol=symbol, timeframe=timeframe, source_dataset_id=source_dataset_id)
        breaks = detect_structure_breaks(bars, swings, trend, displacements, self.config, symbol=symbol, timeframe=timeframe, source_dataset_id=source_dataset_id)
        liquidity_levels = detect_liquidity_levels(bars, swings, self.config, symbol=symbol, timeframe=timeframe, source_dataset_id=source_dataset_id)
        liquidity_sweeps = detect_liquidity_sweeps(bars, liquidity_levels, self.config, symbol=symbol, timeframe=timeframe, source_dataset_id=source_dataset_id)
        equal_levels = detect_equal_levels(bars, swings, self.config, symbol=symbol, timeframe=timeframe, source_dataset_id=source_dataset_id)
        equal_level_sweeps = detect_liquidity_sweeps(bars, equal_levels, self.config, symbol=symbol, timeframe=timeframe, source_dataset_id=source_dataset_id)
        imbalances = detect_fair_value_gaps(bars, self.config, symbol=symbol, timeframe=timeframe, source_dataset_id=source_dataset_id)
        order_blocks = detect_order_blocks(bars, breaks, self.config, symbol=symbol, timeframe=timeframe, source_dataset_id=source_dataset_id)
        dealing_ranges, premium_discount_zones = build_dealing_ranges(bars, swings, self.config, symbol=symbol, timeframe=timeframe, source_dataset_id=source_dataset_id)
        session_levels = detect_session_levels(bars, self.config, symbol=symbol, timeframe=timeframe, source_dataset_id=source_dataset_id)
        state = EngineState(
            recent_bars=[_bar_payload(bar) for bar in bars[-100:]],
            pending_swing_candidates=[],
            confirmed_swings=swings,
            active_ranges=dealing_ranges,
            active_liquidity_levels=liquidity_levels,
            active_gaps=imbalances,
            active_blocks=order_blocks,
            last_processed_timestamp=bars[-1].close_time if bars else None,
            configuration_hash=cfg_hash,
            source_dataset_id=source_dataset_id,
        )
        snapshot = MarketStructureSnapshot(
            snapshot_id=snapshot_id,
            symbol=symbol,
            instrument_id=instrument_id,
            asset_class=asset_class,
            timeframe=timeframe,
            analysis_timestamp=now,
            configuration_version=self.config.version,
            configuration_hash=cfg_hash,
            configuration=self.config.stable_payload(),
            source_dataset_id=source_dataset_id,
            swings=swings,
            trend=trend,
            breaks=breaks,
            displacements=displacements,
            liquidity_levels=liquidity_levels,
            liquidity_sweeps=liquidity_sweeps,
            equal_levels=equal_levels,
            equal_level_sweeps=equal_level_sweeps,
            imbalances=imbalances,
            order_blocks=order_blocks,
            dealing_ranges=dealing_ranges,
            premium_discount_zones=premium_discount_zones,
            session_levels=session_levels,
            warnings=warnings,
            state=state,
        )
        snapshot.overlays = build_overlays(snapshot)
        snapshot.events = build_events(snapshot)
        snapshot.score = score_snapshot(snapshot, self.config)
        snapshot.explanations = explain_snapshot(snapshot)
        snapshot.features = build_feature_rows(snapshot, bars)
        return snapshot

    def analyze_incremental(
        self,
        state: EngineState | None,
        new_bars: list[StructureBar],
        *,
        symbol: str,
        timeframe: str,
        source_dataset_id: str | None = None,
    ) -> MarketStructureSnapshot:
        existing = normalize_bars(state.recent_bars, symbol=symbol, timeframe=timeframe) if state else []
        merged = {bar.open_time: bar for bar in existing}
        for bar in new_bars:
            merged[bar.open_time] = bar
        return self.analyze(sorted(merged.values(), key=lambda b: b.open_time), symbol=symbol, timeframe=timeframe, source_dataset_id=source_dataset_id)


def analyze_bars(
    rows: list[dict[str, Any]],
    *,
    symbol: str,
    timeframe: str,
    config: MarketStructureConfig | None = None,
    source_dataset_id: str | None = None,
) -> MarketStructureSnapshot:
    bars = normalize_bars(rows, symbol=symbol, timeframe=timeframe)
    return MarketStructureEngine(config).analyze(bars, symbol=symbol, timeframe=timeframe, source_dataset_id=source_dataset_id)


def build_feature_rows(snapshot: MarketStructureSnapshot, bars: list[StructureBar]) -> list[FeatureRow]:
    out: list[FeatureRow] = []
    breaks = sorted([brk for brk in snapshot.breaks if brk.confirmation_time], key=lambda brk: brk.confirmation_time)
    break_cursor = 0
    last_bos = None
    choch_seen = False
    best_displacement = 0.0
    displacements = sorted([event for event in snapshot.displacements if event.confirmation_time], key=lambda event: event.confirmation_time)
    disp_cursor = 0
    for bar in bars:
        while break_cursor < len(breaks) and breaks[break_cursor].confirmation_time <= bar.close_time:
            brk = breaks[break_cursor]
            if brk.break_kind == StructureBreakKind.BOS:
                last_bos = brk
            if brk.break_kind == StructureBreakKind.CHOCH:
                choch_seen = True
            break_cursor += 1
        while disp_cursor < len(displacements) and displacements[disp_cursor].confirmation_time <= bar.close_time:
            best_displacement = max(best_displacement, displacements[disp_cursor].quality_score or 0.0)
            disp_cursor += 1
        bars_since_bos = bar.index - last_bos.bar_index if last_bos else None
        position = snapshot.dealing_ranges[-1].normalized_current_position if snapshot.dealing_ranges else None
        out.append(
            FeatureRow(
                timestamp=bar.close_time,
                trend_state=snapshot.trend.state if snapshot.trend else "unknown",
                last_bos_direction=last_bos.direction if last_bos else "unknown",
                bars_since_bos=bars_since_bos,
                choch_active=choch_seen,
                displacement_score=best_displacement,
                dealing_range_position=position,
            )
        )
    return out


def _bar_payload(bar: StructureBar) -> dict[str, Any]:
    return {
        "timestamp": bar.open_time.isoformat(),
        "open_time": bar.open_time.isoformat(),
        "close_time": bar.close_time.isoformat(),
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume) if bar.volume is not None else None,
        "is_complete": bar.is_complete,
    }
