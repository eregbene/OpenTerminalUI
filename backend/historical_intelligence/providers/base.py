"""Provider-neutral historical bar contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class HistoricalBar:
    """Provider-neutral OHLCV bar -- the common shape every concrete provider normalizes to
    before ingestion.py persists it into the shared mt5_canonical_candles table. `time` is the
    bar's OPEN timestamp, UTC, timezone-aware."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int = 0
    spread: int = 0
    real_volume: int = 0
    complete: bool = True


class HistoricalDataProvider(ABC):
    """Contract every concrete historical data source implements. Nothing outside this
    package's providers/ directory may assume a specific provider -- ingestion.py, quality.py,
    and replay.py all operate against this interface only."""

    name: str

    @abstractmethod
    async def fetch_bars(
        self,
        *,
        canonical_symbol: str,
        broker_symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> list[HistoricalBar]:
        """Returns bars with `start <= time < end`, sorted ascending by time, deduplicated.
        Never raises for "no data in range" (returns an empty list); may raise for a genuine
        transport/provider failure so the caller's ingestion-run record can capture it."""
        raise NotImplementedError

    def provider_symbol(self, canonical_symbol: str) -> str:
        """The identifier this provider actually queries for `canonical_symbol` -- may differ
        from the canonical symbol (e.g. Yahoo's "EURUSD=X" for "EURUSD", or "GC=F" for
        "XAUUSD"). Concrete providers override this; the default is a passthrough."""
        return canonical_symbol

    def is_proxy_for(self, canonical_symbol: str) -> bool:
        """True when this provider's instrument is NOT the same tradable instrument as
        `canonical_symbol` -- e.g. Yahoo's GC=F COMEX gold FUTURES is not broker XAUUSD SPOT.
        Bars from a proxy source are persisted with `proxy=True` and must never be silently
        treated as identical execution-grade data (see PortfolioSnapshotORM-style provenance
        conventions used elsewhere in this codebase)."""
        return False
