"""Phase 3 (Forex/MT5 roadmap) regression tests: broker/internal state reconciliation watchdog.

Covers: clean state stays trustworthy, each discrepancy type is detected and persisted as an
explicit finding, UNKNOWN_BROKER_POSITION (foreign, non-Bensim) is informational only and never
flips trustworthy=False, a broker-fetch failure is itself treated as an untrusted-state finding
(never silently swallowed), is_account_state_trustworthy() fails CLOSED when no pass has ever
run, and account isolation (one account's findings never leak into another's)."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.adaptive_management.orm import AdaptivePositionStateORM
from backend.brokers.mt5 import reconciliation_watchdog as watchdog
from backend.brokers.mt5.models import MT5Position
from backend.brokers.mt5.orm import MT5AccountReconciliationFindingORM, MT5AccountReconciliationStatusORM
from backend.shared.db import Base


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(watchdog, "SessionLocal", SessionLocal)
    return SessionLocal


def _owned_position(*, ticket: int = 1001, volume: str = "0.10", sl: str = "1.0950", tp: str = "1.1100", magic: int = 5601001) -> MT5Position:
    return MT5Position(
        ticket=ticket, symbol="EURUSD", type=0, volume=Decimal(volume), price_open=Decimal("1.1000"), price_current=Decimal("1.1010"),
        sl=Decimal(sl) if sl else None, tp=Decimal(tp) if tp else None, profit=Decimal("0"), magic=magic, comment="BENSIM_AUTO", identifier=ticket,
    )


class _FakeAdapter:
    def __init__(self, positions: list[MT5Position], *, raise_on_fetch: bool = False, bensim_magic: int = 5601001):
        self._positions = positions
        self._raise_on_fetch = raise_on_fetch
        self.config = SimpleNamespace(bensim_magic=bensim_magic)

    async def mt5_positions(self) -> list[MT5Position]:
        if self._raise_on_fetch:
            raise ConnectionError("bridge unreachable")
        return self._positions


def _db_row(*, account_id: str, position_id: str, broker_ticket: str, volume: float = 0.10, sl: float | None = 1.0950, tp: float | None = 1.1100) -> AdaptivePositionStateORM:
    return AdaptivePositionStateORM(
        position_id=position_id, account_id=account_id, symbol="EURUSD", direction="LONG", broker_ticket=broker_ticket,
        entry_price=1.1000, current_volume=volume, current_sl=sl, current_tp=tp,
    )


def test_clean_matching_state_is_trustworthy(monkeypatch):
    _session_factory(monkeypatch)
    pos = _owned_position(ticket=1001)
    monkeypatch.setattr(watchdog, "adapter_for_account", lambda account_id: _FakeAdapter([pos]))
    with watchdog.SessionLocal() as db:
        db.add(_db_row(account_id="demo_10k", position_id="1001", broker_ticket="1001"))
        db.commit()

    result = asyncio.run(watchdog.reconcile_account("demo_10k"))
    assert result["trustworthy"] is True
    assert result["findings"] == []
    assert watchdog.is_account_state_trustworthy("demo_10k") is True


def test_db_position_missing_on_broker_detected(monkeypatch):
    _session_factory(monkeypatch)
    monkeypatch.setattr(watchdog, "adapter_for_account", lambda account_id: _FakeAdapter([]))  # broker reports nothing
    with watchdog.SessionLocal() as db:
        db.add(_db_row(account_id="demo_10k", position_id="1001", broker_ticket="1001"))
        db.commit()

    result = asyncio.run(watchdog.reconcile_account("demo_10k"))
    assert result["trustworthy"] is False
    types = {f["finding_type"] for f in result["findings"]}
    assert watchdog.DB_POSITION_MISSING_ON_BROKER in types
    assert watchdog.is_account_state_trustworthy("demo_10k") is False


def test_broker_position_missing_in_db_detected(monkeypatch):
    _session_factory(monkeypatch)
    pos = _owned_position(ticket=2002)
    monkeypatch.setattr(watchdog, "adapter_for_account", lambda account_id: _FakeAdapter([pos]))  # no DB rows at all

    result = asyncio.run(watchdog.reconcile_account("demo_10k"))
    assert result["trustworthy"] is False
    types = {f["finding_type"] for f in result["findings"]}
    assert watchdog.BROKER_POSITION_MISSING_IN_DB in types


def test_sl_tp_volume_mismatch_detected(monkeypatch):
    _session_factory(monkeypatch)
    pos = _owned_position(ticket=3003, volume="0.20", sl="1.0900", tp="1.1200")
    monkeypatch.setattr(watchdog, "adapter_for_account", lambda account_id: _FakeAdapter([pos]))
    with watchdog.SessionLocal() as db:
        db.add(_db_row(account_id="demo_10k", position_id="3003", broker_ticket="3003", volume=0.10, sl=1.0950, tp=1.1100))
        db.commit()

    result = asyncio.run(watchdog.reconcile_account("demo_10k"))
    types = {f["finding_type"] for f in result["findings"]}
    assert types == {watchdog.SL_MISMATCH, watchdog.TP_MISMATCH, watchdog.VOLUME_MISMATCH}
    assert result["trustworthy"] is False


def test_unknown_broker_position_is_informational_only(monkeypatch):
    _session_factory(monkeypatch)
    foreign = _owned_position(ticket=4004, magic=999999)
    foreign.comment = "manual_trade"
    monkeypatch.setattr(watchdog, "adapter_for_account", lambda account_id: _FakeAdapter([foreign]))

    result = asyncio.run(watchdog.reconcile_account("demo_10k"))
    types = {f["finding_type"] for f in result["findings"]}
    assert types == {watchdog.UNKNOWN_BROKER_POSITION}
    assert result["trustworthy"] is True  # foreign position never untrusts Bensim's own state


def test_broker_fetch_failure_is_treated_as_untrusted(monkeypatch):
    _session_factory(monkeypatch)
    monkeypatch.setattr(watchdog, "adapter_for_account", lambda account_id: _FakeAdapter([], raise_on_fetch=True))

    result = asyncio.run(watchdog.reconcile_account("demo_10k"))
    assert result["trustworthy"] is False
    assert result["reason"] == "BROKER_UNREACHABLE"
    assert watchdog.is_account_state_trustworthy("demo_10k") is False


def test_is_trustworthy_fails_closed_when_never_checked(monkeypatch):
    _session_factory(monkeypatch)
    assert watchdog.is_account_state_trustworthy("never_checked_account") is False


def test_account_isolation_one_untrustworthy_account_never_affects_another(monkeypatch):
    _session_factory(monkeypatch)
    adapters = {"demo_10k": _FakeAdapter([]), "ftmo_demo_25k": _FakeAdapter([_owned_position(ticket=5005)])}
    monkeypatch.setattr(watchdog, "adapter_for_account", lambda account_id: adapters[account_id])
    with watchdog.SessionLocal() as db:
        db.add(_db_row(account_id="demo_10k", position_id="1001", broker_ticket="1001"))  # will be flagged missing-on-broker
        db.add(_db_row(account_id="ftmo_demo_25k", position_id="ftmo_demo_25k:5005", broker_ticket="5005"))  # clean match
        db.commit()

    results = asyncio.run(watchdog.reconcile_all_accounts(["demo_10k", "ftmo_demo_25k"]))
    assert results["demo_10k"]["trustworthy"] is False
    assert results["ftmo_demo_25k"]["trustworthy"] is True
    assert watchdog.is_account_state_trustworthy("demo_10k") is False
    assert watchdog.is_account_state_trustworthy("ftmo_demo_25k") is True


def test_findings_are_persisted_append_only(monkeypatch):
    _session_factory(monkeypatch)
    monkeypatch.setattr(watchdog, "adapter_for_account", lambda account_id: _FakeAdapter([]))
    with watchdog.SessionLocal() as db:
        db.add(_db_row(account_id="demo_10k", position_id="1001", broker_ticket="1001"))
        db.commit()

    asyncio.run(watchdog.reconcile_account("demo_10k"))
    asyncio.run(watchdog.reconcile_account("demo_10k"))

    with watchdog.SessionLocal() as db:
        findings = db.query(MT5AccountReconciliationFindingORM).filter(MT5AccountReconciliationFindingORM.account_id == "demo_10k").all()
        status = db.get(MT5AccountReconciliationStatusORM, "demo_10k")
    assert len(findings) == 2  # append-only: two passes, one finding each, never overwritten
    assert status.finding_count == 1  # status row reflects only the LATEST pass
