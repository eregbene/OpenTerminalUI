from __future__ import annotations

from backend.market_structure.models import (
    ConceptStatus,
    Direction,
    ImbalanceZone,
    LiquidityLevel,
    LiquiditySweep,
    MarketStructureSnapshot,
    OrderBlock,
    OverlayObject,
    SessionLevel,
    StructureBreak,
    SwingPoint,
)


def build_overlays(snapshot: MarketStructureSnapshot) -> list[OverlayObject]:
    overlays: list[OverlayObject] = []
    for swing in snapshot.swings[-30:]:
        overlays.append(
            OverlayObject(
                overlay_id=f"ov_{swing.id}",
                type="swing_marker",
                timestamp=swing.start_time,
                price=swing.price,
                direction=swing.direction,
                label="SH" if swing.swing_type == "high" else "SL",
                style_role="swing_high" if swing.swing_type == "high" else "swing_low",
                source_event_id=swing.id,
                status=swing.status,
                tooltip_evidence=[f"confirmed after {swing.metadata.get('confirmation_delay_bars')} right bars"],
            )
        )
    for brk in snapshot.breaks[-20:]:
        overlays.append(
            OverlayObject(
                overlay_id=f"ov_{brk.id}",
                type="event_marker",
                timestamp=brk.confirmation_time,
                price=brk.break_price,
                direction=brk.direction,
                label=brk.break_kind.upper(),
                style_role=brk.break_kind,
                source_event_id=brk.id,
                status=brk.status,
                tooltip_evidence=[brk.explanation],
            )
        )
    for level in snapshot.liquidity_levels[-20:]:
        overlays.append(_level_overlay(level))
    for sweep in snapshot.liquidity_sweeps[-20:]:
        overlays.append(_sweep_overlay(sweep))
    for zone in snapshot.imbalances[-20:]:
        overlays.append(_zone_overlay(zone))
    for block in snapshot.order_blocks[-20:]:
        overlays.append(_block_overlay(block))
    for rng in snapshot.dealing_ranges[-5:]:
        overlays.append(
            OverlayObject(
                overlay_id=f"ov_{rng.id}",
                type="price_zone",
                start_time=rng.start_time,
                end_time=rng.end_time,
                price_low=rng.price_low,
                price_high=rng.price_high,
                direction=rng.direction,
                label="Range",
                style_role="dealing_range",
                source_event_id=rng.id,
                status=rng.status,
                tooltip_evidence=[f"position={rng.normalized_current_position}"],
            )
        )
    for session in snapshot.session_levels[-20:]:
        overlays.append(_session_overlay(session))
    return overlays


def _level_overlay(level: LiquidityLevel) -> OverlayObject:
    return OverlayObject(overlay_id=f"ov_{level.id}", type="horizontal_level", timestamp=level.confirmation_time, price=level.level, direction=level.direction, label="BSL" if level.side == "buy_side" else "SSL", style_role=level.side, source_event_id=level.id, status=level.status, tooltip_evidence=[f"{level.source}, tolerance {level.tolerance}"])


def _sweep_overlay(sweep: LiquiditySweep) -> OverlayObject:
    return OverlayObject(overlay_id=f"ov_{sweep.id}", type="event_marker", timestamp=sweep.confirmation_time, price=sweep.swept_price, direction=sweep.direction, label="Sweep", style_role="liquidity_sweep", source_event_id=sweep.id, status=sweep.status, tooltip_evidence=[f"penetration {sweep.penetration}"])


def _zone_overlay(zone: ImbalanceZone) -> OverlayObject:
    return OverlayObject(overlay_id=f"ov_{zone.id}", type="price_zone", start_time=zone.start_time, end_time=zone.end_time, price_low=zone.price_low, price_high=zone.price_high, direction=zone.direction, label="FVG", style_role="fvg", source_event_id=zone.id, status=zone.status, tooltip_evidence=[f"midpoint {zone.midpoint}", f"status {zone.status}"])


def _block_overlay(block: OrderBlock) -> OverlayObject:
    return OverlayObject(overlay_id=f"ov_{block.id}", type="price_zone", start_time=block.start_time, end_time=block.end_time, price_low=block.price_low, price_high=block.price_high, direction=block.direction, label="OB", style_role="order_block", source_event_id=block.id, status=block.status, tooltip_evidence=[block.rule])


def _session_overlay(session: SessionLevel) -> OverlayObject:
    return OverlayObject(overlay_id=f"ov_{session.id}", type="horizontal_level", start_time=session.start_time, end_time=session.end_time, price=session.level, direction=Direction.NEUTRAL, label=f"{session.session_name} {session.level_name}", style_role="session_level", source_event_id=session.id, status=ConceptStatus.ACTIVE, tooltip_evidence=[f"{session.session_name} {session.level_name}"])
