from __future__ import annotations

from datetime import datetime, timezone

from backend.market_data.instruments import InstrumentMaster, canonicalize_symbol
from backend.market_data.models import AssetClass
from backend.market_data.snapshots import create_snapshot_metadata


def test_instrument_master_resolves_provider_symbols() -> None:
    master = InstrumentMaster()
    instrument = canonicalize_symbol("AAPL", asset_class=AssetClass.EQUITY, venue="NASDAQ")
    instrument.provider_symbols["yahoo"] = "AAPL"
    master.upsert(instrument)
    assert master.resolve_provider_symbol("YAHOO", "aapl") == instrument


def test_snapshot_hash_is_deterministic() -> None:
    kwargs = dict(
        instruments=["AAPL"],
        asset_classes=["equity"],
        timeframes=["1d"],
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 31, tzinfo=timezone.utc),
        provider="fixture",
        adjustment_mode="raw",
        validation_policy="strict",
        rows=[{"t": "2026-01-02", "c": 100}],
    )
    first = create_snapshot_metadata(**kwargs)
    second = create_snapshot_metadata(**kwargs)
    assert first.content_hash == second.content_hash
    assert first.snapshot_id == second.snapshot_id
