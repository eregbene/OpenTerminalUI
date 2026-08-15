"""Regression tests for two real bugs found auditing adaptive_backfill.py during the ForexSB
integration directive:

1. _structure_snapshot_at previously hardcoded provider="MT5" in every bars_as_of() call, so
   reconstructing structure/regime context for a non-MT5-sourced (e.g. FOREXSB) historical trade
   would silently return INSUFFICIENT_HISTORY for any `at` before MT5's own corpus starts,
   discarding structure context for the entire deep ForexSB-sourced corpus. Fixed by threading
   the fingerprint's own `provider` through explicitly.

2. _checkpoint_progress (the resume/restart-safety mechanism) scoped its checkpoint by
   anchor_strategy only, never by canonical_symbol, even though run_adaptive_backfill's own
   fingerprint query filters by both. Calling it per-symbol (required to pipeline the ForexSB
   backfill -- processing one newly-completed symbol at a time instead of waiting for all ten)
   would compute a checkpoint from the GLOBAL max state_time across every symbol, silently
   excluding every fingerprint for a symbol whose real history predates whatever the
   globally-newest adaptive state happens to be. Fixed by scoping the checkpoint query by
   canonical_symbol too, when given."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.historical_intelligence import adaptive_backfill
from backend.historical_intelligence.orm import HistoricalAdaptiveStateORM, HistoricalPatternFingerprintORM
from backend.shared.db import Base


def test_structure_snapshot_at_passes_through_the_given_provider(monkeypatch):
    calls: list[dict] = []

    async def fake_bars_as_of(*, canonical_symbol, broker_symbol, timeframe, at, count, provider):
        calls.append({"timeframe": timeframe, "provider": provider})
        return []  # insufficient history either way -- we only care what provider was requested

    monkeypatch.setattr(adaptive_backfill, "bars_as_of", fake_bars_as_of)

    result = asyncio.run(adaptive_backfill._structure_snapshot_at(
        canonical_symbol="EURUSD", broker_symbol="EURUSD",
        at=datetime(2019, 3, 1, tzinfo=timezone.utc), direction="LONG", provider="FOREXSB",
    ))

    assert result["status"] == "INSUFFICIENT_HISTORY"
    assert len(calls) == 3  # M15, H1, H4
    assert {c["timeframe"] for c in calls} == {"M15", "H1", "H4"}
    assert all(c["provider"] == "FOREXSB" for c in calls), "every bars_as_of call must use the fingerprint's own provider, not a hardcoded MT5 default"


def test_structure_snapshot_at_defaults_to_mt5_when_not_specified(monkeypatch):
    """The default parameter value preserves existing behavior for every real-position (always
    MT5-sourced) caller that doesn't pass provider explicitly."""
    calls: list[str] = []

    async def fake_bars_as_of(*, canonical_symbol, broker_symbol, timeframe, at, count, provider):
        calls.append(provider)
        return []

    monkeypatch.setattr(adaptive_backfill, "bars_as_of", fake_bars_as_of)

    asyncio.run(adaptive_backfill._structure_snapshot_at(
        canonical_symbol="EURUSD", broker_symbol="EURUSD",
        at=datetime(2026, 1, 1, tzinfo=timezone.utc), direction="LONG",
    ))

    assert calls == ["MT5", "MT5", "MT5"]


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(adaptive_backfill, "SessionLocal", SessionLocal)
    return SessionLocal


def test_checkpoint_progress_scoped_per_symbol_not_globally(monkeypatch):
    """A symbol with OLDER real history (e.g. a ForexSB-sourced 2019 fingerprint) must not be
    checkpointed-out by a DIFFERENT symbol's much-newer adaptive states -- the real bug this
    fixes, confirmed to actually fire against this deployment's own data (157,174 existing
    states reaching state_time 2026-08-13 would have floored out every 2018-2022 ForexSB
    fingerprint for any symbol if the checkpoint weren't scoped per-symbol)."""
    SessionLocal = _session_factory(monkeypatch)
    old_time = datetime(2019, 3, 1, tzinfo=timezone.utc)
    new_time = datetime(2026, 8, 13, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add(HistoricalAdaptiveStateORM(
            state_id="HAS_gbpusd_new", source_fingerprint_id="HPF_x", strategy_version="v1",
            canonical_symbol="GBPUSD", broker_symbol="GBPUSD", direction="LONG", strategy="mtfai1",
            state_time=new_time, milestone_label="R_0_25", peer_group_hash="h1",
        ))
        db.commit()

    # No prior EURUSD adaptive state exists at all -- checkpoint scoped to EURUSD must be None,
    # never GBPUSD's much-newer state_time.
    checkpoint = adaptive_backfill._checkpoint_progress(None, "EURUSD")
    assert checkpoint is None

    with SessionLocal() as db:
        db.add(HistoricalAdaptiveStateORM(
            state_id="HAS_eurusd_old", source_fingerprint_id="HPF_y", strategy_version="v1",
            canonical_symbol="EURUSD", broker_symbol="EURUSD", direction="LONG", strategy="mtfai1",
            state_time=old_time, milestone_label="R_0_25", peer_group_hash="h2",
        ))
        db.commit()

    def _utc(dt):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    checkpoint = adaptive_backfill._checkpoint_progress(None, "EURUSD")
    assert _utc(checkpoint) == old_time  # EURUSD's own checkpoint, NOT GBPUSD's newer one

    global_checkpoint = adaptive_backfill._checkpoint_progress(None, None)
    assert _utc(global_checkpoint) == new_time  # unscoped call still returns the true global max


def _fingerprint(fingerprint_id, *, canonical_symbol, provider, entry_time):
    return HistoricalPatternFingerprintORM(
        fingerprint_id=fingerprint_id, strategy_version="v1", source_quality_tier="SNAPSHOT",
        provider=provider, canonical_symbol=canonical_symbol, direction="LONG", anchor_strategy="mtfai1",
        entry=1.1, stop_loss=1.09, take_profit=1.12, entry_time=entry_time, peer_group_hash="h",
    )


def test_checkpoint_progress_same_symbol_different_provider_not_contaminated(monkeypatch):
    """The remaining bug: _checkpoint_progress is scoped per-symbol (fixed above) but NOT per-
    provider. A symbol like EURUSD genuinely has BOTH live MT5-sourced adaptive states (state_time
    reaching today) AND historical FOREXSB-sourced ones (state_time in 2018-2022) -- without a
    provider-aware checkpoint, calling run_adaptive_backfill(canonical_symbol='EURUSD',
    provider='FOREXSB', resume=True) would compute its checkpoint from EURUSD's newest state
    REGARDLESS of source, floor entry_time against today's live MT5 watermark, and silently
    exclude every 2018-2022 ForexSB fingerprint -- the same class of bug already fixed for
    cross-symbol contamination, just one dimension narrower (same symbol, different provider)."""
    SessionLocal = _session_factory(monkeypatch)
    live_time = datetime(2026, 8, 13, tzinfo=timezone.utc)
    historical_time = datetime(2019, 3, 1, tzinfo=timezone.utc)

    with SessionLocal() as db:
        db.add(_fingerprint("HPF_mt5_live", canonical_symbol="EURUSD", provider="MT5", entry_time=live_time))
        db.add(_fingerprint("HPF_forexsb_hist", canonical_symbol="EURUSD", provider="FOREXSB", entry_time=historical_time))
        db.commit()
        db.add(HistoricalAdaptiveStateORM(
            state_id="HAS_eurusd_mt5_live", source_fingerprint_id="HPF_mt5_live", strategy_version="v1",
            canonical_symbol="EURUSD", broker_symbol="EURUSD", direction="LONG", strategy="mtfai1",
            state_time=live_time, milestone_label="R_0_25", peer_group_hash="h1",
        ))
        db.commit()

    def _utc(dt):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    # A FOREXSB-scoped checkpoint for EURUSD must ignore the MT5-sourced live state entirely --
    # no FOREXSB adaptive state exists yet, so the checkpoint must be None, never live_time.
    checkpoint = adaptive_backfill._checkpoint_progress(None, "EURUSD", provider="FOREXSB")
    assert checkpoint is None, "a FOREXSB-scoped checkpoint must not inherit the live MT5 watermark for the same symbol"

    # An MT5-scoped (or unscoped) checkpoint for EURUSD correctly still sees the live state.
    mt5_checkpoint = adaptive_backfill._checkpoint_progress(None, "EURUSD", provider="MT5")
    assert _utc(mt5_checkpoint) == live_time

    unscoped_checkpoint = adaptive_backfill._checkpoint_progress(None, "EURUSD")
    assert _utc(unscoped_checkpoint) == live_time  # no provider filter requested -> true max, unchanged behavior

    # Now add the FOREXSB-sourced historical state -- the FOREXSB-scoped checkpoint must find
    # THAT one, not the MT5 live one.
    with SessionLocal() as db:
        db.add(HistoricalAdaptiveStateORM(
            state_id="HAS_eurusd_forexsb_hist", source_fingerprint_id="HPF_forexsb_hist", strategy_version="v1",
            canonical_symbol="EURUSD", broker_symbol="EURUSD", direction="LONG", strategy="mtfai1",
            state_time=historical_time, milestone_label="R_0_25", peer_group_hash="h2",
        ))
        db.commit()

    checkpoint = adaptive_backfill._checkpoint_progress(None, "EURUSD", provider="FOREXSB")
    assert _utc(checkpoint) == historical_time, "FOREXSB checkpoint must resolve to the FOREXSB-sourced state, not be contaminated by the newer MT5 one"


def test_run_adaptive_backfill_provider_filter_scopes_fingerprint_scan(monkeypatch):
    """provider, when given to run_adaptive_backfill, must scope the FINGERPRINT scan itself
    (not just the checkpoint) -- otherwise a 'FOREXSB-only' run still walks every MT5 fingerprint
    for the symbol too, defeating the purpose of scoping."""
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        db.add(_fingerprint("HPF_mt5_1", canonical_symbol="EURUSD", provider="MT5", entry_time=datetime(2026, 1, 1, tzinfo=timezone.utc)))
        db.add(_fingerprint("HPF_forexsb_1", canonical_symbol="EURUSD", provider="FOREXSB", entry_time=datetime(2019, 1, 1, tzinfo=timezone.utc)))
        db.commit()

    async def fake_backfill_trade(fp, outcome, *, db, compute_structure):
        return 0

    monkeypatch.setattr(adaptive_backfill, "backfill_trade", fake_backfill_trade)

    result = asyncio.run(adaptive_backfill.run_adaptive_backfill(
        canonical_symbol="EURUSD", provider="FOREXSB", resume=False, compute_structure=False,
    ))
    # No outcomes exist for either fingerprint, so trades_processed counts every fingerprint the
    # scan actually visited -- must be 1 (FOREXSB only), not 2 (both providers).
    assert result["trades_processed"] == 1
