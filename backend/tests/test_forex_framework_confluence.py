from __future__ import annotations

from datetime import datetime, timezone

from backend.forex_frameworks.models import FrameworkBias, FrameworkSignal, SignalStatus
from backend.forex_frameworks.service import framework_service


def _signal(framework_id: str, bias: FrameworkBias, confidence: float) -> FrameworkSignal:
    return FrameworkSignal(
        framework_id=framework_id,
        framework_name=framework_id,
        framework_version="1.0.0",
        symbol="EURUSD",
        timeframe="1h",
        analysis_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        bias=bias,
        signal_type="test",
        confidence=confidence,
        quality=0.8,
        market_regime="trend",
        supporting_evidence=[f"{framework_id} supports {bias.value}"],
        status=SignalStatus.VALID,
    )


def test_framework_comparison_counts_agreement_and_conflict() -> None:
    signals = [
        _signal("trend_following", FrameworkBias.BULLISH, 0.8),
        _signal("momentum", FrameworkBias.BULLISH, 0.6),
        _signal("mean_reversion", FrameworkBias.BEARISH, 0.5),
        _signal("vsa", FrameworkBias.UNKNOWN, 0.0),
    ]

    comparison = framework_service.compare(signals)
    thesis = framework_service.thesis(comparison)

    assert comparison.bullish_framework_count == 2
    assert comparison.bearish_framework_count == 1
    assert comparison.unknown_framework_count == 1
    assert comparison.conflict_ratio > 0
    assert comparison.overall_framework_bias == FrameworkBias.BULLISH
    assert thesis.label == "analytical_candidate"
    assert thesis.read_only is True
