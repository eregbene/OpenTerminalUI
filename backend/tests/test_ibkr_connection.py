from pathlib import Path
import asyncio

import pytest

from backend.brokers import broker_registry
from backend.brokers.ibkr.client import IBKRPaperAdapter
from backend.brokers.ibkr.configuration import IBKRConfiguration
from backend.forex_strategies.ibkr_acceptance import Fx5AcceptanceStore, IbkrPaperAcceptanceService


def test_ibkr_connection_verifies_paper_account(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    adapter = IBKRPaperAdapter(
        IBKRConfiguration(
            enabled=True,
            mode="FIXTURE",
            simulated=True,
            expected_account="DU1234567",
            account_allow_list=["DU1234567"],
        )
    )
    monkeypatch.setattr(broker_registry, "_adapters", {**broker_registry._adapters, "ibkr": adapter})
    service = IbkrPaperAcceptanceService(Fx5AcceptanceStore(tmp_path))

    record = asyncio.run(service.connect_and_verify())
    status = asyncio.run(service.status())

    assert record.accepted is True
    assert record.account_mode == "PAPER"
    assert record.live_execution_allowed is False
    assert status["connection_state"] == "ACCOUNT_VERIFIED"
    assert status["masked_account_id"] and "***" in status["masked_account_id"]
