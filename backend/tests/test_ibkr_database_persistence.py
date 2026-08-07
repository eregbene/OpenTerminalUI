from __future__ import annotations

from uuid import uuid4

import pytest

from backend.brokers.ibkr.persistence import IbkrAcceptanceDbStore, broker_tables_available
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite
from backend.forex_strategies.ibkr_acceptance import (
    AccountVerificationRecord,
    BrokerEventRecord,
    BrokerOrderRecord,
    ContractVerificationRecord,
    OperationalIncident,
    ReconciliationRecord,
    _hash,
    utcnow,
)

# Isolated in-memory sqlite per test -- IbkrAcceptanceDbStore (backend/brokers/ibkr/
# persistence.py) does `from backend.shared.db import SessionLocal, engine`, so this
# was previously calling create_all against the shared/application database. See
# backend/shared/test_db_safety.py (added after the 2026-08-07 incident).
_EXTRA_SITES = ("backend.brokers.ibkr.persistence",)


def test_broker_acceptance_tables_are_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    redirect_shared_db_to_isolated_sqlite(monkeypatch, extra_engine_sites=_EXTRA_SITES)
    assert broker_tables_available()


def test_db_store_persists_connection_contract_order_event_recon_and_incident(monkeypatch: pytest.MonkeyPatch) -> None:
    redirect_shared_db_to_isolated_sqlite(monkeypatch, extra_engine_sites=_EXTRA_SITES)
    store = IbkrAcceptanceDbStore()
    suffix = uuid4().hex[:10]
    account = AccountVerificationRecord(
        verification_id=f"test_ibkracct_fx5b_{suffix}",
        account_id_masked="DU***67",
        account_id_hash=_hash("DU1234567"),
        account_type="PAPER",
        account_mode="PAPER",
        trading_environment="PAPER",
        server_host="host.docker.internal",
        server_port=7497,
        client_id=111,
        session_id=f"test_ibkrsession_fx5b_{suffix}",
        connected_at=utcnow(),
        verified_at=utcnow(),
        accepted=True,
    )
    store.upsert_verification(account)
    contract = ContractVerificationRecord(
        canonical_symbol="EURUSD",
        contract_id=f"test_ibkr_contract_eurusd_fx5b_{suffix}",
        base_currency="EUR",
        quote_currency="USD",
        security_type="CASH",
        exchange="IDEALPRO",
        currency="USD",
        local_symbol="EUR.USD",
        con_id=int(suffix[:6], 16),
        minimum_tick="0.00001",
        verified=True,
        verification_source="REAL_IBKR_PAPER",
        usable_for_real_submission=True,
        content_hash=_hash("contract"),
    )
    store.upsert_contract(contract)
    order = BrokerOrderRecord(
        broker_order_record_id=f"test_ibkr_order_fx5b_{suffix}",
        account_id_masked="DU***67",
        candidate_id=f"candidate_fx5b_{suffix}",
        risk_decision_id=f"risk_fx5b_{suffix}",
        oms_intent_id=f"oms_fx5b_{suffix}",
        canonical_symbol="EURUSD",
        contract_id=contract.contract_id,
        client_order_id=f"client_fx5b_{suffix}",
        broker_order_id=f"9001{suffix}",
        permanent_id=f"perm_fx5b_{suffix}",
        order_reference=f"ref_fx5b_{suffix}",
        side="BUY",
        quantity="1",
        submitted_at=utcnow(),
        broker_status="ACKNOWLEDGED",
        internal_status="SUBMITTED",
        content_hash=_hash("order"),
    )
    store.upsert_order(order)
    store.append_event(BrokerEventRecord(event_id=f"event_fx5b_{suffix}", broker_order_record_id=order.broker_order_record_id, event_type="ORDER_ACKNOWLEDGED", sequence=1, payload_hash=_hash("event")))
    store.append_reconciliation(ReconciliationRecord(reconciliation_id=f"recon_fx5b_{suffix}", status="MATCHED", trade_id=f"trade_fx5b_{suffix}"))
    store.append_incident(OperationalIncident(incident_id=f"incident_fx5b_{suffix}", severity="INFO", code="TEST", message="test", blocking=False))
    data = store.load()
    assert data["account_verifications"][account.verification_id]["accepted"] is True
    assert data["contracts"]["EURUSD"]["verification_source"] == "REAL_IBKR_PAPER"
    assert data["contracts"]["EURUSD"]["usable_for_real_submission"] is True
    assert data["broker_orders"][order.broker_order_record_id]["order_reference"] == f"ref_fx5b_{suffix}"
    assert data["events"][-1]["event_type"] == "ORDER_ACKNOWLEDGED"
    assert data["reconciliations"][-1]["status"] == "MATCHED"
    assert data["incidents"][-1]["code"] == "TEST"
