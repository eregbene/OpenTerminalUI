from __future__ import annotations

from dataclasses import dataclass

from backend.economic_intelligence.event_mapping import higher_is_positive

MIN_SAMPLES_FOR_Z_SCORE = 8


@dataclass(frozen=True)
class SurpriseResult:
    raw_surprise: float | None
    revision: float | None
    surprise_z_score: float | None
    direction: str | None  # "positive" | "negative" | "neutral" | None (not rule-based)
    sample_size: int


def raw_surprise(actual: float | None, forecast: float | None) -> float | None:
    if actual is None or forecast is None:
        return None
    return actual - forecast


def revision(current_previous: float | None, originally_observed_previous: float | None) -> float | None:
    if current_previous is None or originally_observed_previous is None:
        return None
    return current_previous - originally_observed_previous


def surprise_z_score(surprise: float | None, historical_surprises: list[float]) -> float | None:
    """Only computed when there are enough historical samples. Never manufactures a z-score."""
    if surprise is None or len(historical_surprises) < MIN_SAMPLES_FOR_Z_SCORE:
        return None
    mean = sum(historical_surprises) / len(historical_surprises)
    variance = sum((value - mean) ** 2 for value in historical_surprises) / len(historical_surprises)
    stddev = variance**0.5
    if stddev <= 0:
        return None
    return (surprise - mean) / stddev


def evaluate_surprise(
    *,
    normalized_name: str,
    actual: float | None,
    forecast: float | None,
    current_previous: float | None,
    originally_observed_previous: float | None,
    historical_surprises: list[float],
) -> SurpriseResult:
    surprise = raw_surprise(actual, forecast)
    rev = revision(current_previous, originally_observed_previous)
    z_score = surprise_z_score(surprise, historical_surprises)
    positive_rule = higher_is_positive(normalized_name)
    direction: str | None = None
    if surprise is not None and positive_rule is not None:
        if abs(surprise) < 1e-9:
            direction = "neutral"
        else:
            beats_forecast = surprise > 0
            direction = "positive" if beats_forecast == positive_rule else "negative"
    return SurpriseResult(surprise, rev, z_score, direction, len(historical_surprises))
