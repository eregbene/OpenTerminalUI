from pathlib import Path
import asyncio

from backend.forex_strategies.ibkr_acceptance import Fx5AcceptanceStore, IbkrPaperAcceptanceService


def test_ibkr_fx_contracts_and_xau_guard(tmp_path: Path):
    service = IbkrPaperAcceptanceService(Fx5AcceptanceStore(tmp_path))

    eurusd = asyncio.run(service.verify_contract("EURUSD"))
    gbpusd = asyncio.run(service.verify_contract("GBPUSD"))
    usdjpy = asyncio.run(service.verify_contract("USDJPY"))
    xauusd = asyncio.run(service.verify_contract("XAUUSD"))

    assert eurusd.verified and eurusd.security_type == "CASH" and eurusd.exchange == "IDEALPRO"
    assert gbpusd.verified
    assert usdjpy.minimum_tick == "0.001"
    assert xauusd.verified is False
    assert "CONTRACT_UNAVAILABLE" in xauusd.rejection_reasons
