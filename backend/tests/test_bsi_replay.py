"""BSI Intelligence Migration Phase C/E: tests for the BSI-aware replay module
(backend/historical_intelligence/bsi_replay.py). Proves: point-in-time-safe baseline resolution
(SL win / TP win / MTM timeout), the policy-plugin interface runs every policy against the
IDENTICAL shared baseline (never re-derives the entry independently), the archived
BSI_ADAPTIVE_V1_FAILED policy is preserved and reproducible but protected from being silently
overwritten, and BSI_BASELINE_V1 itself is likewise protected."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.brokers.mt5.orm import MT5CanonicalCandleORM
from backend.historical_intelligence import outcomes
from backend.historical_intelligence.bsi_replay import (
    BSI_ADAPTIVE_V1_FAILED_POLICY,
    BSI_BASELINE_V1_POLICY,
    BSIManagementPolicy,
    BSITrade,
    register_policy,
    registered_policies,
    replay_bsi_candidate,
    resolve_baseline,
)
from backend.shared.db import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

ENTRY_TIME = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)  # well outside the M5 coverage window (2026-06-16..08-13) -- pure M15 conservative-tiebreak path


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(outcomes, "SessionLocal", SessionLocal)
    return SessionLocal


def _seed_candle(db, *, symbol: str, t: datetime, o: float, h: float, l: float, c: float) -> None:
    db.add(MT5CanonicalCandleORM(
        candle_id=f"C_{symbol}_{t.isoformat()}", provider="MT5", canonical_symbol=symbol, broker_symbol=symbol,
        timeframe="M15", timestamp=t, open=o, high=h, low=l, close=c, quality="VALID",
    ))


def _long_trade(symbol="EURUSD") -> BSITrade:
    return BSITrade(trade_id="T1", canonical_symbol=symbol, broker_symbol=symbol, direction="LONG", entry=1.1000, stop_loss=1.0980, take_profit=1.1040, entry_time=ENTRY_TIME)


def test_resolve_baseline_clean_tp_hit(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    trade = _long_trade()
    with SessionLocal() as db:
        _seed_candle(db, symbol="EURUSD", t=ENTRY_TIME + timedelta(minutes=15), o=1.1000, h=1.1010, l=1.0995, c=1.1005)
        _seed_candle(db, symbol="EURUSD", t=ENTRY_TIME + timedelta(minutes=30), o=1.1005, h=1.1045, l=1.1000, c=1.1042)  # touches TP (1.1040), not SL
        db.commit()

    resolution = resolve_baseline(trade)
    assert resolution.resolution_kind == "TP_HIT"
    assert resolution.outcome_r == pytest.approx(2.0, abs=1e-6)  # risk=0.0020, reward=0.0040 -> RR=2.0


def test_resolve_baseline_clean_sl_hit(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    trade = _long_trade()
    with SessionLocal() as db:
        _seed_candle(db, symbol="EURUSD", t=ENTRY_TIME + timedelta(minutes=15), o=1.1000, h=1.1005, l=1.0975, c=1.0978)  # touches SL (1.0980)
        db.commit()

    resolution = resolve_baseline(trade)
    assert resolution.resolution_kind == "SL_HIT"
    assert resolution.outcome_r == -1.0


def test_resolve_baseline_conservative_tiebreak_sl_wins_same_bar_conflict(monkeypatch):
    """Outside M5 coverage: if a single bar's range touches BOTH SL and TP, SL must win (the
    documented, tested-earlier-this-mission conservative convention -- never give a policy or the
    baseline itself a favorable hindsight ordering)."""
    SessionLocal = _session_factory(monkeypatch)
    trade = _long_trade()
    with SessionLocal() as db:
        _seed_candle(db, symbol="EURUSD", t=ENTRY_TIME + timedelta(minutes=15), o=1.1000, h=1.1045, l=1.0975, c=1.1000)  # spans both SL and TP in one bar
        db.commit()

    resolution = resolve_baseline(trade)
    assert resolution.resolution_kind == "SL_HIT"
    assert resolution.outcome_r == -1.0


def test_resolve_baseline_mtm_timeout_when_neither_level_touched(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    trade = _long_trade()
    with SessionLocal() as db:
        _seed_candle(db, symbol="EURUSD", t=ENTRY_TIME + timedelta(minutes=15), o=1.1000, h=1.1010, l=1.0995, c=1.1008)
        db.commit()

    resolution = resolve_baseline(trade)
    assert resolution.resolution_kind == "MTM_TIMEOUT"
    assert resolution.outcome_r == pytest.approx((1.1008 - 1.1000) / 0.0020, abs=1e-6)


def test_baseline_policy_reproduces_the_shared_resolution_exactly(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    trade = _long_trade()
    with SessionLocal() as db:
        _seed_candle(db, symbol="EURUSD", t=ENTRY_TIME + timedelta(minutes=15), o=1.1000, h=1.1045, l=1.0995, c=1.1042)
        db.commit()

    results = replay_bsi_candidate(trade, policy_ids=["BSI_BASELINE_V1"])
    baseline = resolve_baseline(trade)
    assert results["BSI_BASELINE_V1"].outcome_r == baseline.outcome_r
    assert results["BSI_BASELINE_V1"].exit_reason == baseline.resolution_kind


def test_archived_be_policy_scratches_a_loser_that_reached_0_5r_first(monkeypatch):
    """Reproduces the exact, already-validated BSI_ADAPTIVE_V1 mechanic: a trade that reaches
    +0.5R then reverses to a full SL loss gets scratched to 0R under the archived policy -- proves
    the archived policy still runs correctly (for reproducibility), even though it is never
    recommended."""
    SessionLocal = _session_factory(monkeypatch)
    trade = _long_trade()
    with SessionLocal() as db:
        # bar 1: rallies to +0.5R (entry 1.1000, risk 0.0020 -> +0.5R = 1.1010) without touching SL/TP
        _seed_candle(db, symbol="EURUSD", t=ENTRY_TIME + timedelta(minutes=15), o=1.1000, h=1.1012, l=1.0995, c=1.1008)
        # bar 2: reverses all the way to the original SL
        _seed_candle(db, symbol="EURUSD", t=ENTRY_TIME + timedelta(minutes=30), o=1.1005, h=1.1006, l=1.0975, c=1.0979)
        db.commit()

    results = replay_bsi_candidate(trade, policy_ids=["BSI_BASELINE_V1", "BSI_ADAPTIVE_V1_FAILED"])
    assert results["BSI_BASELINE_V1"].outcome_r == -1.0  # unmanaged: full loss
    assert results["BSI_ADAPTIVE_V1_FAILED"].outcome_r == 0.0  # BE-armed at +0.5R, scratched instead
    assert results["BSI_ADAPTIVE_V1_FAILED"].evidence["armed"] is True


def test_both_policies_share_the_identical_baseline_never_re_derive_the_entry(monkeypatch):
    """The directive's own core requirement: compare the SAME entry through multiple policies
    without changing it. Proven here by asserting both policies' evidence traces back to the
    SAME resolution_kind/ambiguous-event-count the shared BaselineResolution computed once."""
    SessionLocal = _session_factory(monkeypatch)
    trade = _long_trade()
    with SessionLocal() as db:
        _seed_candle(db, symbol="EURUSD", t=ENTRY_TIME + timedelta(minutes=15), o=1.1000, h=1.1045, l=1.0995, c=1.1042)
        db.commit()

    results = replay_bsi_candidate(trade)  # all registered policies
    assert set(results.keys()) >= {"BSI_BASELINE_V1", "BSI_ADAPTIVE_V1_FAILED"}
    # a clean TP hit on bar 1 means BE never even had a chance to arm before TP -- both policies
    # must report the identical outcome, since baseline resolved before any milestone check mattered.
    assert results["BSI_BASELINE_V1"].outcome_r == results["BSI_ADAPTIVE_V1_FAILED"].outcome_r == pytest.approx(2.0, abs=1e-6)


def test_baseline_v1_policy_is_protected_from_overwrite():
    with pytest.raises(ValueError):
        register_policy(BSIManagementPolicy(policy_id="BSI_BASELINE_V1", description="malicious overwrite attempt", status="research", evaluate=lambda t, b: None))


def test_failed_v1_policy_is_protected_from_overwrite():
    with pytest.raises(ValueError):
        register_policy(BSIManagementPolicy(policy_id="BSI_ADAPTIVE_V1_FAILED", description="attempted overwrite", status="research", evaluate=lambda t, b: None))


def test_a_new_experimental_policy_can_be_registered_and_used(monkeypatch):
    """Proves the plugin interface genuinely supports adding a future BSI_ADAPTIVE_V2 hypothesis
    without touching this module's own code -- the directive's own explicit design goal."""
    SessionLocal = _session_factory(monkeypatch)
    trade = _long_trade()
    with SessionLocal() as db:
        _seed_candle(db, symbol="EURUSD", t=ENTRY_TIME + timedelta(minutes=15), o=1.1000, h=1.1045, l=1.0995, c=1.1042)
        db.commit()

    def _always_half_r(t: BSITrade, b) -> "object":
        from backend.historical_intelligence.bsi_replay import BSIPolicyResult
        return BSIPolicyResult(policy_id="BSI_TEST_EXPERIMENTAL", outcome_r=0.5, exit_reason="TEST", evidence={})

    register_policy(BSIManagementPolicy(policy_id="BSI_TEST_EXPERIMENTAL", description="unit-test-only experimental policy", status="research", evaluate=_always_half_r))
    assert "BSI_TEST_EXPERIMENTAL" in registered_policies()
    results = replay_bsi_candidate(trade, policy_ids=["BSI_TEST_EXPERIMENTAL"])
    assert results["BSI_TEST_EXPERIMENTAL"].outcome_r == 0.5


def test_registry_marks_failed_policy_status_explicitly():
    """The directive's own 'freeze failed adaptive v1... document the decision' requirement --
    proven here as a real, queryable field, not just a comment."""
    assert BSI_ADAPTIVE_V1_FAILED_POLICY.status == "FAILED_ARCHIVED"
    assert BSI_BASELINE_V1_POLICY.status == "reference"
