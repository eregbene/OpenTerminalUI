"""Broker Independence Assessment, Phase 1 (2026-08-22): additive-only interface/schema work
that must produce ZERO behavior change to real MT5 execution, per the phase's own explicit
constraint. Covers all four surfaces touched in this phase:

  1. mt5_candidate_evaluations.broker_provider (migration 0067) -- schema + ORM column.
  2. BrokerOrderAdapter.modify_position / .close_position -- new Protocol methods, MT5Adapter
     conforms via the same MT5ReadOnlyViolation posture submit_order/cancel_order already use
     (no execution behavior added -- see adapter.py's own comments on these two methods).
  3. BrokerSymbolSpec -- new FX symbol-metadata model; MT5Adapter.symbol_spec() is a REAL (not
     stubbed) new read-only implementation, tested against both a healthy fixture (EURUSD, full
     metadata) and a genuinely incomplete one (XAUUSD, the existing FakeMT5 fixture never set
     point/contract/volume fields for it) to prove the "never guess a missing field" rule holds.
  4. BrokerPosition's new required identity fields (canonical_position_id/account_id/broker) and
     new stop_loss/take_profit fields -- MT5Adapter.positions() (previously unused by any real
     caller per the coupling assessment) now populates all of them correctly.

Uses the same fake_adapter()/FakeMT5 fixtures test_mt5_adapter.py already established --
deliberately not reinvented.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.brokers.base import BrokerOrderAdapter, BrokerReadAdapter
from backend.brokers.mt5.exceptions import MT5UnavailableError, MT5ReadOnlyViolation
from backend.brokers.models import (
    BrokerClosePositionCommand,
    BrokerModifyPositionCommand,
    BrokerPosition,
    BrokerSymbolSpec,
)
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.shared.db import Base
from backend.tests.test_mt5_adapter import fake_adapter


# --------------------------------------------------------------------- 1. schema / migration ---
def test_mt5_candidate_evaluations_has_broker_provider_column():
    """Fresh-metadata check (not a live migration replay -- see the standalone migration-replay
    check run separately against the real dev DB): the ORM model itself declares the column with
    the correct type/nullability/default that migration 0067 also creates."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    columns = {c["name"]: c for c in inspector.get_columns("mt5_candidate_evaluations")}
    assert "broker_provider" in columns
    assert columns["broker_provider"]["nullable"] is False


def test_mt5_candidate_evaluation_orm_defaults_broker_provider_to_mt5():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with SessionLocal() as db:
        row = MT5CandidateEvaluationORM(evaluation_id="e1", cycle_id="c1", candidate_id="cand1", symbol="EURUSD", broker_symbol="EURUSD", direction="LONG", overall_confidence=75.0, confidence_band="HIGH")
        db.add(row)
        db.commit()
        db.refresh(row)
        assert row.broker_provider == "MT5"


# ------------------------------------------------------------- 2. Protocol conformance (MT5) ---
def test_mt5_adapter_conforms_to_extended_broker_protocols():
    """isinstance works because both Protocols are @runtime_checkable (added this phase) -- this
    is a structural check (method names present), not a signature check, but it is exactly what
    catches "adapter.py forgot to implement a new Protocol method" at test time instead of at
    Phase 2/3 call-site-rewiring time."""
    adapter = fake_adapter()
    assert isinstance(adapter, BrokerReadAdapter)
    assert isinstance(adapter, BrokerOrderAdapter)


def test_mt5_modify_and_close_position_are_read_only_like_submit_and_cancel():
    """Phase 1 adds these methods to the Protocol and to MT5Adapter, but changes ZERO execution
    behavior -- real SL/TP modification and position closing still live entirely in
    adaptive_management/service.py's own MT5 request-dict construction until Phase 3/4 rewires
    that call site onto this method. Proven here by asserting the exact same MT5ReadOnlyViolation
    posture submit_order/cancel_order already have."""
    adapter = fake_adapter()
    modify_command = BrokerModifyPositionCommand(canonical_position_id="mt5:123456:1", broker_position_id="1", account_id="123456", instrument_id="FX:EURUSD", stop_loss=Decimal("1.09"), take_profit=Decimal("1.12"), idempotency_key="m1")
    close_command = BrokerClosePositionCommand(canonical_position_id="mt5:123456:1", broker_position_id="1", account_id="123456", instrument_id="FX:EURUSD", idempotency_key="c1")

    with pytest.raises(MT5ReadOnlyViolation):
        asyncio.run(adapter.modify_position(modify_command))
    with pytest.raises(MT5ReadOnlyViolation):
        asyncio.run(adapter.close_position(close_command))


# --------------------------------------------------------------- 3. BrokerSymbolSpec / lots ---
def test_mt5_symbol_spec_maps_real_metadata_for_healthy_symbol():
    """EURUSD in the shared FakeMT5 fixture has a full, real symbol_info() response (point,
    trade_contract_size, volume_min/max/step, trade_stops_level/freeze_level, currency_margin) --
    exactly the metadata symbol_spec() needs. Confirms the pip/lot-size derivation and confirms
    every field lands in CANONICAL units (lots), never a broker-native raw volume figure -- there
    is no centilot or other non-lot representation anywhere in this model, by construction."""
    adapter = fake_adapter()
    asyncio.run(adapter.connect())

    spec = asyncio.run(adapter.symbol_spec("EURUSD"))

    assert isinstance(spec, BrokerSymbolSpec)
    assert spec.broker == "mt5"
    assert spec.digits == 5
    assert spec.pip_position == 4  # 5-digit fractional-pip symbol: pip is one order above point
    assert spec.pip_size == Decimal("0.0001")
    assert spec.lot_size == Decimal("100000")
    assert spec.contract_size == Decimal("100000")
    assert spec.volume_min == Decimal("0.01")
    assert spec.volume_max == Decimal("100.0")
    assert spec.volume_step == Decimal("0.01")
    assert spec.margin_currency == "EUR"  # currency_margin=raw[:3] in the fixture
    assert spec.stops_level == Decimal("10")
    assert spec.freeze_level == Decimal("0")


def test_mt5_symbol_spec_raises_rather_than_guesses_when_broker_metadata_incomplete():
    """XAUUSD's FakeMT5 fixture deliberately has NO point/trade_contract_size/volume_min/max/step
    fields set at all (see test_mt5_adapter.py's own symbol_info() -- only bid/ask/spread/digits
    are set for it). symbol_spec() must raise rather than silently default any of these -- the
    same "never guessed" posture broker_min_stop_distance and point_value already hold themselves
    to elsewhere in this codebase (families/_shared.py::_spread_within_safety_buffer)."""
    adapter = fake_adapter()
    asyncio.run(adapter.connect())

    with pytest.raises(MT5UnavailableError):
        asyncio.run(adapter.symbol_spec("XAUUSD"))


# ------------------------------------------------------- 4. BrokerPosition identity + SL/TP ---
def test_mt5_positions_populates_canonical_identity_and_sl_tp():
    """Phase 1 made canonical_position_id/account_id/broker required on BrokerPosition and added
    stop_loss/take_profit -- MT5Adapter.positions() (the Protocol method; distinct from the
    MT5-only mt5_positions() every real caller currently uses instead, per the coupling
    assessment) must still construct valid instances. The fixture's one open position has no
    sl/tp set at all (position_from_raw already handles that as None correctly, unchanged by this
    phase) -- covered here specifically to prove the new fields don't turn "no stop set" into a
    validation error."""
    adapter = fake_adapter()
    asyncio.run(adapter.connect())

    positions = asyncio.run(adapter.positions("123456"))

    assert len(positions) == 1
    pos = positions[0]
    assert isinstance(pos, BrokerPosition)
    assert pos.canonical_position_id == "mt5:123456:1"
    assert pos.broker_position_id == "1"
    assert pos.account_id == "123456"
    assert pos.broker == "mt5"
    assert pos.instrument_id == "FX:EURUSD"
    assert pos.quantity == Decimal("1.0")  # type=0 (buy) -> positive
    assert pos.stop_loss is None
    assert pos.take_profit is None


def test_broker_position_same_ticket_same_account_always_same_canonical_id():
    """The determinism guarantee BrokerPosition's own docstring requires: calling positions()
    twice for the same account/ticket must yield the identical canonical_position_id both times
    -- unlike e.g. BrokerAccountSnapshot.snapshot_id, which is legitimately random-per-call."""
    adapter = fake_adapter()
    asyncio.run(adapter.connect())

    first = asyncio.run(adapter.positions("123456"))
    second = asyncio.run(adapter.positions("123456"))

    assert first[0].canonical_position_id == second[0].canonical_position_id
