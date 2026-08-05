from pathlib import Path
import asyncio

from backend.forex_strategies.ibkr_acceptance import Fx5AcceptanceStore, IbkrPaperAcceptanceService


def test_duplicate_submission_reference_is_blocked(tmp_path: Path):
    service = IbkrPaperAcceptanceService(Fx5AcceptanceStore(tmp_path))
    asyncio.run(service.connect_and_verify())
    asyncio.run(service.verify_contract("EURUSD"))
    service.record_order(candidate_id="cand1", oms_intent_id="intent1", account_id="DU1234567", symbol="EURUSD", side="BUY", quantity="1000")

    reasons = service.pre_submit_guard(candidate_id="cand1", oms_intent_id="intent1", account_id="DU1234567", symbol="EURUSD", order_type="MARKET", quantity="1000")

    assert "DUPLICATE_ORDER_REFERENCE" in reasons


def test_unknown_submission_requires_reconciliation(tmp_path: Path):
    service = IbkrPaperAcceptanceService(Fx5AcceptanceStore(tmp_path))
    service.record_unknown_submission(candidate_id="cand1", oms_intent_id="intent1", account_id="DU1234567", symbol="EURUSD", side="BUY", quantity="1000")

    result = service.reconcile()

    assert result.blocking is True
    assert result.status == "UNKNOWN"
