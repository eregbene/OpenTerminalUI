from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from backend.market_data.models import AssetClass, Instrument, InstrumentIdentifier


@dataclass
class InstrumentMaster:
    _by_id: dict[str, Instrument] = field(default_factory=dict)
    _provider_symbols: dict[tuple[str, str], str] = field(default_factory=dict)

    def upsert(self, instrument: Instrument) -> Instrument:
        self._by_id[instrument.instrument_id] = instrument
        for provider, symbol in instrument.provider_symbols.items():
            self._provider_symbols[(provider.lower(), symbol.upper())] = instrument.instrument_id
        return instrument

    def get(self, instrument_id: str) -> Instrument | None:
        return self._by_id.get(instrument_id)

    def resolve_provider_symbol(self, provider: str, symbol: str) -> Instrument | None:
        instrument_id = self._provider_symbols.get((provider.lower(), symbol.upper()))
        return self._by_id.get(instrument_id) if instrument_id else None


def canonicalize_symbol(symbol: str, *, asset_class: AssetClass = AssetClass.EQUITY, venue: str | None = None) -> Instrument:
    display = symbol.strip().upper()
    venue_part = (venue or "GLOBAL").upper()
    instrument_id = f"{asset_class.value}:{venue_part}:{display}"
    return Instrument(
        instrument_id=instrument_id,
        display_symbol=display,
        asset_class=asset_class,
        venue=venue_part,
        exchange=venue_part,
        provider_symbols={"canonical": display},
        identifiers=[InstrumentIdentifier(scheme="symbol", value=display)],
        tick_size=Decimal("0.01"),
    )
