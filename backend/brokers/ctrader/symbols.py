"""Maps cTrader's native symbol metadata (ProtoOASymbol + ProtoOALightSymbol) into the generic,
broker-neutral BrokerSymbolSpec (backend/brokers/models.py, Broker Independence Phase 1) -- the
mechanism that guarantees no cTrader-native volume representation (raw "cents" units) ever
crosses the adapter boundary. Everything above this module (strategies, risk calculator,
Portfolio Manager, Adaptive Trade Manager, Historical Intelligence, confidence engine) only ever
sees canonical lots, exactly like the MT5 adapter's own symbol_spec()."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from backend.brokers.ctrader.exceptions import CTraderUnavailableError
from backend.brokers.ctrader.volume import pip_size_from_position, raw_volume_to_lots
from backend.brokers.models import BrokerSymbolSpec


def _attr(obj: Any, name: str) -> Any:
    return getattr(obj, name, None)


def ctrader_symbol_to_spec(symbol: Any, *, instrument_id: str) -> BrokerSymbolSpec:
    """`symbol` is a ProtoOASymbol (full detail response from ProtoOASymbolByIdReq) -- never a
    ProtoOALightSymbol (the lightweight list-all variant lacks lotSize/minVolume/etc, exactly the
    fields this function needs; see symbol_spec()'s two-step resolve in adapter.py). Raises rather
    than guesses when required fields are missing -- same "never guessed" posture MT5Adapter.
    symbol_spec() already holds itself to (Phase 1 precedent)."""
    required = {
        "digits": _attr(symbol, "digits"), "pipPosition": _attr(symbol, "pipPosition"),
        "lotSize": _attr(symbol, "lotSize"), "minVolume": _attr(symbol, "minVolume"),
        "maxVolume": _attr(symbol, "maxVolume"), "stepVolume": _attr(symbol, "stepVolume"),
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise CTraderUnavailableError(f"symbol_spec({instrument_id}): cTrader did not report required field(s): {missing}")

    digits = int(required["digits"])
    lot_size_raw = int(required["lotSize"])
    units_per_lot = _units_per_lot(lot_size_raw)
    return BrokerSymbolSpec(
        instrument_id=instrument_id,
        broker="ctrader",
        pip_position=int(required["pipPosition"]),
        pip_size=pip_size_from_position(int(required["pipPosition"])),
        digits=digits,
        lot_size=units_per_lot,
        volume_min=raw_volume_to_lots(int(required["minVolume"]), lot_size_raw),
        volume_max=raw_volume_to_lots(int(required["maxVolume"]), lot_size_raw),
        volume_step=raw_volume_to_lots(int(required["stepVolume"]), lot_size_raw),
        contract_size=units_per_lot,
        margin_currency=str(_attr(symbol, "depositCurrency") or _attr(symbol, "quoteAssetId") or "USD"),
        # stops_level/freeze_level: MT5-specific broker-enforced minimum-stop-distance concepts.
        # cTrader's equivalent field name has not been verified against the live API yet (Phase 2
        # is read-only and never constructs a stop order) -- left None rather than guessing a
        # field name; revisit when Phase 3/4 needs real stop-distance validation for cTrader.
        stops_level=None,
        freeze_level=None,
        resolved_at=datetime.now(timezone.utc),
    )


def _units_per_lot(lot_size_raw: int) -> Decimal:
    # lotSize is itself "in cents" (units * 100) -- see volume.py's module docstring.
    return Decimal(lot_size_raw) / Decimal(100)
