from pathlib import Path
import asyncio

from backend.brokers.ibkr.configuration import ibkr_config
from backend.forex_strategies.ibkr_acceptance import Fx5AcceptanceStore, IbkrPaperAcceptanceService


def test_live_account_rejected_when_allowlist_is_not_du(tmp_path: Path, monkeypatch):
    original = list(ibkr_config.account_allow_list)
    monkeypatch.setattr(ibkr_config, "account_allow_list", ["U1234567"])
    try:
        service = IbkrPaperAcceptanceService(Fx5AcceptanceStore(tmp_path))
        record = asyncio.run(service.connect_and_verify())
    finally:
        monkeypatch.setattr(ibkr_config, "account_allow_list", original)

    assert record.accepted is False
    assert "LIVE_ACCOUNT" in record.rejection_reasons or "AMBIGUOUS_ACCOUNT" in record.rejection_reasons
