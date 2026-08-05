from __future__ import annotations

from backend.market_structure.configuration import MarketStructureConfig
from backend.market_structure.models import MarketStructureSnapshot, QualityScore, ScoreComponent


def score_snapshot(snapshot: MarketStructureSnapshot, config: MarketStructureConfig) -> QualityScore:
    weights = config.scoring
    comps: list[ScoreComponent] = []
    latest_break = snapshot.breaks[-1] if snapshot.breaks else None
    break_value = min(1.0, (latest_break.break_distance_atr or 0.0) / 2.0) if latest_break else 0.0
    comps.append(ScoreComponent(name="break_distance", value=break_value, weight=weights.break_distance_weight, contribution=break_value * weights.break_distance_weight, evidence="latest break distance measured in ATR"))
    disp_value = max((event.quality_score or 0.0) for event in snapshot.displacements[-3:] or []) if snapshot.displacements else 0.0
    comps.append(ScoreComponent(name="displacement", value=disp_value, weight=weights.displacement_weight, contribution=disp_value * weights.displacement_weight, evidence="recent displacement body/range criteria"))
    liq_value = 1.0 if snapshot.liquidity_sweeps else (0.4 if snapshot.liquidity_levels else 0.0)
    comps.append(ScoreComponent(name="liquidity", value=liq_value, weight=weights.liquidity_weight, contribution=liq_value * weights.liquidity_weight, evidence="liquidity reference or sweep detected"))
    imb_value = 1.0 if snapshot.imbalances else 0.0
    comps.append(ScoreComponent(name="imbalance", value=imb_value, weight=weights.imbalance_weight, contribution=imb_value * weights.imbalance_weight, evidence="fair value gap lifecycle present"))
    range_value = 1.0 if snapshot.dealing_ranges else 0.0
    comps.append(ScoreComponent(name="range_location", value=range_value, weight=weights.range_location_weight, contribution=range_value * weights.range_location_weight, evidence="active dealing range with normalized position"))
    total = min(1.0, sum(item.contribution for item in comps))
    return QualityScore(total_score=round(total, 4), score_components=comps)
