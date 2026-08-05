from pathlib import Path
import asyncio

import pytest

from backend.brokers import broker_registry
from backend.brokers.ibkr.client import IBKRPaperAdapter
from backend.brokers.ibkr.configuration import IBKRConfiguration
from backend.forex_strategies import ibkr_acceptance as acceptance
from backend.forex_strategies.ibkr_acceptance import Fx5AcceptanceStore, IbkrPaperAcceptanceService


def test_ibkr_order_record_and_event_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = IBKRConfiguration(enabled=True, mode="FIXTURE", simulated=True, expected_account="DU1234567", account_allow_list=["DU1234567"])
    adapter = IBKRPaperAdapter(config)
    monkeypatch.setattr(acceptance, "ibkr_config", config)
    monkeypatch.setattr(broker_registry, "_adapters", {**broker_registry._adapters, "ibkr": adapter})
    service = IbkrPaperAcceptanceService(Fx5AcceptanceStore(tmp_path))
    verification = asyncio.run(service.connect_and_verify())
    asyncio.run(service.verify_contract("EURUSD"))

    reasons = service.pre_submit_guard(candidate_id="cand1", oms_intent_id="intent1", account_id="DU1234567", symbol="EURUSD", order_type="MARKET", quantity="1000")
    record = service.record_order(candidate_id="cand1", oms_intent_id="intent1", account_id="DU1234567", symbol="EURUSD", side="BUY", quantity="1000", broker_order_id="1001", permanent_id="perm_1001")

    assert verification.accepted
    assert reasons == []
    assert record.broker_status == "ACKNOWLEDGED"
    assert service.events(record.broker_order_record_id)[0].event_type == "ORDER_ACKNOWLEDGED"
