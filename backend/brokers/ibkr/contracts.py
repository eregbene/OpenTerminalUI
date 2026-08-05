from __future__ import annotations

import hashlib
from datetime import timedelta

from backend.brokers.errors import BrokerCapabilityError
from backend.brokers.models import BrokerContract, now_utc


class ContractCache:
    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 256) -> None:
        self.ttl = timedelta(seconds=ttl_seconds)
        self.max_entries = max_entries
        self._contracts: dict[str, BrokerContract] = {}
        self._negative: dict[str, tuple[str, object]] = {}

    def get(self, instrument_id: str) -> BrokerContract | None:
        row = self._contracts.get(instrument_id)
        if row and now_utc() - row.resolved_at <= self.ttl:
            return row
        return None

    def put(self, contract: BrokerContract) -> BrokerContract:
        if len(self._contracts) >= self.max_entries:
            self._contracts.pop(next(iter(self._contracts)))
        self._contracts[contract.instrument_id] = contract
        return contract


contract_cache = ContractCache()


def resolve_contract(instrument_id: str) -> BrokerContract:
    cached = contract_cache.get(instrument_id)
    if cached:
        return cached
    raw = instrument_id.strip().upper()
    if not raw or "AMBIG" in raw:
        raise BrokerCapabilityError("AMBIGUOUS_CONTRACT", "contract resolution is ambiguous", status_code=422)
    if raw.startswith("FX:") or "/" in raw:
        pair = raw.replace("FX:", "").replace("/", "")
        if len(pair) != 6:
            raise BrokerCapabilityError("UNSUPPORTED_FOREX_CONTRACT", "invalid forex pair", status_code=422)
        symbol, currency = pair[:3], pair[3:]
        contract = BrokerContract(
            instrument_id=instrument_id,
            symbol=symbol,
            asset_type="FOREX",
            exchange="IDEALPRO",
            currency=currency,
            security_type="CASH",
            con_id=_con_id(raw),
            local_symbol=f"{symbol}.{currency}",
        )
        return contract_cache.put(contract)
    if raw.startswith("IDX:"):
        symbol = raw.split(":", 1)[1]
        return contract_cache.put(BrokerContract(instrument_id=instrument_id, symbol=symbol, asset_type="INDEX", exchange="SMART", currency="USD", security_type="IND", con_id=_con_id(raw)))
    symbol = raw.split(":", 1)[-1]
    asset_type = "ETF" if symbol in {"SPY", "QQQ", "IWM"} else "EQUITY"
    return contract_cache.put(BrokerContract(instrument_id=instrument_id, symbol=symbol, asset_type=asset_type, exchange="SMART", primary_exchange="NASDAQ", currency="USD", security_type="STK", con_id=_con_id(raw)))


def _con_id(raw: str) -> int:
    return int(hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8], 16)
