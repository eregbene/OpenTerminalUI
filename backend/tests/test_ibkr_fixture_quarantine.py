from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, inspect, text

from backend.brokers.ibkr.orm import BrokerContractORM, BrokerEventORM, BrokerExecutionORM, BrokerOrderORM
from backend.scripts.quarantine_stale_ibkr_fixture_records import run
from backend.shared.db import Base, SessionLocal, engine
from backend.forex_strategies.ibkr_acceptance import Fx5AcceptanceStore, IbkrPaperAcceptanceService


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_test_schema() -> None:
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    additions = {
        "broker_contracts": {
            "record_source": "VARCHAR(32) DEFAULT 'UNKNOWN_LEGACY' NOT NULL",
            "is_quarantined": "BOOLEAN DEFAULT 0 NOT NULL",
            "quarantined_at": "DATETIME",
            "quarantine_reason": "VARCHAR(160)",
            "test_run_id": "VARCHAR(120)",
        },
        "broker_orders": {
            "record_source": "VARCHAR(32) DEFAULT 'UNKNOWN_LEGACY' NOT NULL",
            "is_quarantined": "BOOLEAN DEFAULT 0 NOT NULL",
            "quarantined_at": "DATETIME",
            "quarantine_reason": "VARCHAR(160)",
            "test_run_id": "VARCHAR(120)",
            "broker_session_id": "VARCHAR(120)",
        },
        "broker_events": {
            "record_source": "VARCHAR(32) DEFAULT 'UNKNOWN_LEGACY' NOT NULL",
            "is_quarantined": "BOOLEAN DEFAULT 0 NOT NULL",
            "quarantined_at": "DATETIME",
            "quarantine_reason": "VARCHAR(160)",
            "test_run_id": "VARCHAR(120)",
        },
        "broker_executions": {
            "record_source": "VARCHAR(32) DEFAULT 'UNKNOWN_LEGACY' NOT NULL",
            "is_quarantined": "BOOLEAN DEFAULT 0 NOT NULL",
            "quarantined_at": "DATETIME",
            "quarantine_reason": "VARCHAR(160)",
            "test_run_id": "VARCHAR(120)",
        },
        "broker_reconciliations": {"scope_metadata": "JSON DEFAULT '{}' NOT NULL"},
    }
    with engine.begin() as conn:
        for table, columns in additions.items():
            existing = {str(column["name"]) for column in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


@pytest.fixture()
def fixture_lineage():
    _ensure_test_schema()
    suffix = uuid4().hex[:10]
    con_id = int(suffix[:8], 16)
    contract_id = f"test_ibkr_contract_eurusd_fx5b_{suffix}"
    order_id = f"test_ibkr_order_fx5b_{suffix}"
    with SessionLocal() as session:
        session.add(
            BrokerContractORM(
                id=contract_id,
                broker="IBKR",
                environment="PAPER",
                canonical_symbol="EURUSD",
                con_id=con_id,
                security_type="CASH",
                exchange="IDEALPRO",
                currency="USD",
                minimum_tick=Decimal("0.00001"),
                quantity_increment=Decimal("1"),
                market_rule_ids=[],
                contract_payload={"test_run_id": suffix},
                verified=True,
                verified_at=_now(),
                verification_source="REAL_IBKR_PAPER",
                record_source="FIXTURE_IBKR",
                test_run_id=suffix,
                content_hash=suffix,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        session.add(
            BrokerOrderORM(
                id=order_id,
                broker="IBKR",
                environment="PAPER",
                masked_account_id="DU***67",
                canonical_symbol="EURUSD",
                contract_id=contract_id,
                client_order_id=f"client_fx5b_{suffix}",
                broker_order_id=f"9001{suffix}",
                permanent_id=f"perm_fx5b_{suffix}",
                order_reference=f"ref_fx5b_{suffix}",
                side="BUY",
                order_type="MARKET",
                quantity=Decimal("1"),
                time_in_force="DAY",
                internal_status="SUBMITTED",
                broker_status="ACKNOWLEDGED",
                filled_quantity=Decimal("0"),
                remaining_quantity=Decimal("1"),
                commission=None,
                commission_currency="USD",
                record_source="FIXTURE_IBKR",
                test_run_id=suffix,
                content_hash=suffix,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        session.commit()
    with SessionLocal() as session:
        session.add(
            BrokerEventORM(
                id=f"event_fx5b_{suffix}",
                broker_order_id=order_id,
                event_type="ORDER_ACKNOWLEDGED",
                received_at=_now(),
                sequence=1,
                payload={},
                payload_hash=suffix,
                record_source="FIXTURE_IBKR",
                test_run_id=suffix,
                created_at=_now(),
            )
        )
        session.commit()
    try:
        yield order_id
    finally:
        with SessionLocal() as session:
            session.execute(delete(BrokerEventORM).where(BrokerEventORM.broker_order_id == order_id))
            session.execute(delete(BrokerExecutionORM).where(BrokerExecutionORM.broker_order_id == order_id))
            session.execute(delete(BrokerOrderORM).where(BrokerOrderORM.id == order_id))
            session.execute(delete(BrokerContractORM).where(BrokerContractORM.id == contract_id))
            session.commit()


def test_quarantine_dry_run_does_not_mutate(fixture_lineage: str) -> None:
    payload = run(fixture_lineage, apply=False, reason="STALE_FX5B_TEST_FIXTURE")
    assert payload["eligible"] is True
    assert payload["target"]["classification"] == "FIXTURE_IBKR"
    with SessionLocal() as session:
        order = session.get(BrokerOrderORM, fixture_lineage)
        assert order is not None
        assert order.is_quarantined is False


def test_quarantine_apply_is_idempotent(fixture_lineage: str) -> None:
    first = run(fixture_lineage, apply=True, reason="STALE_FX5B_TEST_FIXTURE")
    second = run(fixture_lineage, apply=True, reason="STALE_FX5B_TEST_FIXTURE")
    assert first["applied"] is True
    assert second["applied"] is True
    with SessionLocal() as session:
        order = session.get(BrokerOrderORM, fixture_lineage)
        assert order is not None
        assert order.record_source == "FIXTURE_IBKR"
        assert order.is_quarantined is True
        assert order.quarantine_reason == "STALE_FX5B_TEST_FIXTURE"
        audit_events = session.query(BrokerEventORM).filter(BrokerEventORM.broker_order_id == fixture_lineage, BrokerEventORM.event_type == "FIXTURE_RECORD_QUARANTINED").count()
        assert audit_events == 1


def test_quarantine_rejects_wildcards() -> None:
    with pytest.raises(SystemExit):
        run("test_*", apply=False, reason="STALE_FX5B_TEST_FIXTURE")


def test_quarantine_rejects_real_record() -> None:
    _ensure_test_schema()
    suffix = uuid4().hex[:10]
    order_id = f"real_ibkr_order_{suffix}"
    with SessionLocal() as session:
        session.add(
            BrokerOrderORM(
                id=order_id,
                broker="IBKR",
                environment="PAPER",
                masked_account_id="DU***02",
                canonical_symbol="EURUSD",
                client_order_id=f"real_client_{suffix}",
                broker_order_id=f"2002{suffix}",
                permanent_id=f"real_perm_{suffix}",
                order_reference=f"real_ref_{suffix}",
                side="BUY",
                order_type="MARKET",
                quantity=Decimal("1"),
                time_in_force="DAY",
                internal_status="SUBMITTED",
                broker_status="Submitted",
                filled_quantity=Decimal("0"),
                remaining_quantity=Decimal("1"),
                record_source="REAL_IBKR",
                content_hash=suffix,
                created_at=_now(),
                updated_at=_now(),
            )
        )
        session.commit()
    try:
        with pytest.raises(SystemExit):
            run(order_id, apply=True, reason="STALE_FX5B_TEST_FIXTURE")
    finally:
        with SessionLocal() as session:
            session.execute(delete(BrokerEventORM).where(BrokerEventORM.broker_order_id == order_id))
            session.execute(delete(BrokerOrderORM).where(BrokerOrderORM.id == order_id))
            session.commit()


def test_real_reconciliation_excludes_quarantined_fixture_and_keeps_real_commission_rule(tmp_path) -> None:
    service = IbkrPaperAcceptanceService(Fx5AcceptanceStore(tmp_path))
    fixture_order = {
        "broker_order_record_id": "fixture",
        "record_source": "FIXTURE_IBKR",
        "is_quarantined": True,
        "commission_status": "PENDING",
        "internal_status": "SUBMITTED",
    }
    real_order = {
        "broker_order_record_id": "real",
        "record_source": "REAL_IBKR",
        "is_quarantined": False,
        "commission_status": "PENDING",
        "internal_status": "SUBMITTED",
    }
    service.store.save(
        {
            "account_verifications": {},
            "contracts": {},
            "broker_orders": {"fixture": fixture_order, "real": real_order},
            "events": [],
            "reconciliations": [],
            "incidents": [],
            "recovery": {"status": "IDLE", "last_started_at": None, "last_completed_at": None, "blocking": False},
        }
    )
    result = service.reconcile()
    assert result.status == "MISSING_COMMISSION"
    assert result.scope_metadata["included_local_record_count"] == 1
    assert result.scope_metadata["excluded_fixture_count"] == 1
    assert result.scope_metadata["excluded_quarantined_count"] == 1
    assert result.mismatches[0]["entity_id"] == "real"
