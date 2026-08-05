"""Canonical market-data foundation for Bensim Trading.

This package is intentionally incremental. Existing provider code can keep
serving legacy endpoints while new and migrated services use these models,
registry, router, validation, cache and diagnostics primitives.
"""

from backend.market_data.models import (
    AssetClass,
    DataAvailability,
    DataOrigin,
    DataQualityMetadata,
    DataStatus,
    EntitlementStatus,
    Instrument,
    InstrumentIdentifier,
    OHLCVBar,
    ProviderMetadata,
    ProviderState,
    Quote,
)
from backend.market_data.registry import ProviderRegistration, get_provider_registry
from backend.market_data.routing import MarketDataRouter, MarketDataRequest

__all__ = [
    "AssetClass",
    "DataAvailability",
    "DataOrigin",
    "DataQualityMetadata",
    "DataStatus",
    "EntitlementStatus",
    "Instrument",
    "InstrumentIdentifier",
    "MarketDataRequest",
    "MarketDataRouter",
    "OHLCVBar",
    "ProviderMetadata",
    "ProviderRegistration",
    "ProviderState",
    "Quote",
    "get_provider_registry",
]
