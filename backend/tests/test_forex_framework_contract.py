from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.forex_frameworks.models import FrameworkBias, SignalStatus
from backend.forex_frameworks.registry import registry
from backend.forex_frameworks.service import context_from_snapshot, framework_service
from backend.forex_intelligence.service import ForexIntelligenceService


def _bars(count: int = 96) -> list[dict[str, object]]:
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    rows = []
    price = 1.08
    for idx in range(count):
        close = price + 0.00015 + ((idx % 10) - 5) * 0.00004
        rows.append({"timestamp": (start + timedelta(hours=idx)).isoformat(), "open": price, "high": max(price, close) + 0.0004, "low": min(price, close) - 0.0004, "close": close, "volume": 1000 + idx, "is_complete": True})
        price = close
    return rows


def _ctx():
    snapshot = ForexIntelligenceService().analyze_rows(_bars(), symbol="EURUSD", timeframe="1h").model_dump(mode="json")
    candles = [{"t": int(datetime.fromisoformat(row["timestamp"]).timestamp()), "o": row["open"], "h": row["high"], "l": row["low"], "c": row["close"], "v": row["volume"]} for row in _bars()]
    return context_from_snapshot(snapshot, candles)


def test_every_registered_framework_satisfies_signal_contract() -> None:
    ctx = _ctx()
    ids = [definition.framework_id for definition in registry.definitions()]

    for framework_id in ids:
        framework = registry.require(framework_id)
        first = framework.analyze(ctx)
        second = framework.analyze(ctx)

        assert first.model_dump(mode="json", exclude={"generated_at"}) == second.model_dump(mode="json", exclude={"generated_at"})
        assert first.framework_id == framework_id
        assert first.bias in FrameworkBias
        assert first.status in SignalStatus
        assert 0 <= first.confidence <= 1
        assert 0 <= first.quality <= 1
        assert isinstance(first.supporting_evidence, list)
        assert isinstance(first.missing_evidence, list)


def test_unsupported_symbol_does_not_return_confident_signal() -> None:
    ctx = _ctx().model_copy(update={"symbol": "EURJPY"})
    signal = registry.require("trend_following").analyze(ctx)

    assert signal.status == SignalStatus.UNSUPPORTED_INSTRUMENT
    assert signal.confidence == 0


def test_framework_service_execution_order_is_deterministic() -> None:
    ctx = _ctx()
    first = [signal.framework_id for signal in framework_service.analyze(ctx)]
    second = [signal.framework_id for signal in framework_service.analyze(ctx)]

    assert first == second
    assert first[:4] == ["trend_following", "mean_reversion", "momentum", "breakout"]
