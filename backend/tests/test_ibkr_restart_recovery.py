from pathlib import Path

from backend.forex_strategies.ibkr_acceptance import Fx5AcceptanceStore, IbkrPaperAcceptanceService


def test_restart_recovery_blocks_approval_until_complete(tmp_path: Path):
    service = IbkrPaperAcceptanceService(Fx5AcceptanceStore(tmp_path))

    started = service.recovery_start()
    reasons = service.pre_submit_guard(candidate_id="cand1", oms_intent_id="intent1", account_id="DU1234567", symbol="EURUSD", order_type="MARKET", quantity="1000")
    completed = service.recovery_complete()

    assert started["blocking"] is True
    assert "RECOVERY_IN_PROGRESS" in reasons
    assert completed["status"] in {"RECOVERY_COMPLETE", "RECOVERY_FAILED"}
