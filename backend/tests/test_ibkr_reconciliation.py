from pathlib import Path

import pytest

from backend.brokers.ibkr.configuration import IBKRConfiguration
from backend.forex_strategies import ibkr_acceptance as acceptance
from backend.forex_strategies.ibkr_acceptance import Fx5AcceptanceStore, IbkrPaperAcceptanceService


def test_reconciliation_reports_missing_commission_nonblocking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(acceptance, "ibkr_config", IBKRConfiguration(enabled=True, mode="FIXTURE", simulated=True, expected_account="DU1234567", account_allow_list=["DU1234567"]))
    service = IbkrPaperAcceptanceService(Fx5AcceptanceStore(tmp_path))
    service.record_order(candidate_id="cand1", oms_intent_id="intent1", account_id="DU1234567", symbol="EURUSD", side="BUY", quantity="1000")

    result = service.reconcile()

    assert result.status == "MISSING_COMMISSION"
    assert any(item["status"] == "MISSING_COMMISSION" for item in result.mismatches)
