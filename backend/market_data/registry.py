from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from backend.config.settings import get_settings
from backend.market_data.capabilities import Capability
from backend.market_data.models import AssetClass, EntitlementStatus, ProviderState


class RateLimitPolicy(BaseModel):
    requests_per_second: float | None = None
    requests_per_minute: int | None = None
    requests_per_day: int | None = None
    concurrent_requests: int | None = None
    source: str = "unknown"


class ProviderHealthSnapshot(BaseModel):
    health_status: ProviderState = ProviderState.UNKNOWN
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    last_error_code: str | None = None
    latency_ms: float | None = None
    circuit_breaker_state: str = "closed"
    cooldown_until: datetime | None = None


class ProviderRegistration(BaseModel):
    provider_id: str
    display_name: str
    asset_classes: list[AssetClass] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    data_types: list[str] = Field(default_factory=list)
    priority: int = 100
    enabled: bool = True
    configured: bool = False
    authenticated: bool = False
    entitlement_status: EntitlementStatus = EntitlementStatus.UNKNOWN
    supports_realtime: bool = False
    supports_historical: bool = False
    supports_streaming: bool = False
    supports_depth: bool = False
    supports_fundamentals: bool = False
    supports_news: bool = False
    rate_limit_policy: RateLimitPolicy = Field(default_factory=RateLimitPolicy)
    health: ProviderHealthSnapshot = Field(default_factory=ProviderHealthSnapshot)
    adapter: Any = Field(default=None, exclude=True)
    notes: str | None = None

    model_config = {"arbitrary_types_allowed": True, "use_enum_values": True}


@dataclass
class MarketDataProviderRegistry:
    _providers: dict[str, ProviderRegistration] = field(default_factory=dict)

    def register(self, registration: ProviderRegistration) -> None:
        provider_id = registration.provider_id.strip().lower()
        if not provider_id:
            raise ValueError("provider_id is required")
        registration.provider_id = provider_id
        self._providers[provider_id] = registration

    def get(self, provider_id: str) -> ProviderRegistration | None:
        return self._providers.get(provider_id.strip().lower())

    def all(self) -> list[ProviderRegistration]:
        return sorted(self._providers.values(), key=lambda p: (p.priority, p.provider_id))

    def providers_for(
        self,
        *,
        capability: Capability | str,
        asset_class: AssetClass | str | None = None,
        require_configured: bool = False,
        require_entitled: bool = False,
    ) -> list[ProviderRegistration]:
        cap = Capability(capability)
        asset = AssetClass(asset_class) if asset_class else None
        rows: list[ProviderRegistration] = []
        for provider in self._providers.values():
            if not provider.enabled:
                continue
            if cap not in provider.capabilities and cap.value not in provider.capabilities:
                continue
            if asset and asset not in provider.asset_classes and asset.value not in provider.asset_classes:
                continue
            if require_configured and not provider.configured:
                continue
            if require_entitled and provider.entitlement_status != EntitlementStatus.ENTITLED:
                continue
            rows.append(provider)
        return sorted(rows, key=lambda p: (p.priority, p.provider_id))

    def mark_success(self, provider_id: str, latency_ms: float | None = None) -> None:
        provider = self.get(provider_id)
        if not provider:
            return
        provider.health.health_status = ProviderState.CONFIGURED if provider.configured else ProviderState.INSTALLED
        provider.health.last_success_at = datetime.now(timezone.utc)
        provider.health.latency_ms = latency_ms
        provider.health.last_error_code = None

    def mark_failure(self, provider_id: str, error_code: str) -> None:
        provider = self.get(provider_id)
        if not provider:
            return
        provider.health.health_status = ProviderState.DEGRADED
        provider.health.last_failure_at = datetime.now(timezone.utc)
        provider.health.last_error_code = error_code

    def diagnostics(self, *, include_adapters: bool = False) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for provider in self.all():
            row = provider.model_dump(mode="json", exclude={"adapter"} if not include_adapters else set())
            out.append(row)
        return out


_registry: MarketDataProviderRegistry | None = None


def build_default_registry() -> MarketDataProviderRegistry:
    settings = get_settings()
    registry = MarketDataProviderRegistry()
    registry.register(
        ProviderRegistration(
            provider_id="yahoo",
            display_name="Yahoo Finance",
            asset_classes=[AssetClass.EQUITY, AssetClass.ETF, AssetClass.INDEX, AssetClass.CRYPTO],
            capabilities=[Capability.QUOTE, Capability.HISTORICAL_CANDLES, Capability.INSTRUMENT_REFERENCE],
            data_types=["quote", "ohlcv", "instrument"],
            priority=30,
            enabled=True,
            configured=True,
            authenticated=False,
            entitlement_status=EntitlementStatus.ENTITLED,
            supports_historical=True,
            rate_limit_policy=RateLimitPolicy(source="unknown"),
            notes="Existing Yahoo/yfinance paths. Real-time entitlement is not claimed.",
        )
    )
    registry.register(
        ProviderRegistration(
            provider_id="nse",
            display_name="NSE public website",
            asset_classes=[AssetClass.EQUITY, AssetClass.INDEX, AssetClass.OPTION, AssetClass.FUTURE],
            capabilities=[Capability.QUOTE, Capability.MARKET_DEPTH, Capability.CORPORATE_ACTIONS],
            data_types=["quote", "market_status", "corporate_actions"],
            priority=20,
            enabled=True,
            configured=True,
            authenticated=False,
            entitlement_status=EntitlementStatus.UNKNOWN,
            supports_realtime=False,
            supports_depth=True,
            rate_limit_policy=RateLimitPolicy(source="unknown"),
            notes="Public NSE endpoints can block datacenter IPs; circuit breaker exists in legacy client.",
        )
    )
    registry.register(
        ProviderRegistration(
            provider_id="fmp",
            display_name="Financial Modeling Prep",
            asset_classes=[AssetClass.EQUITY, AssetClass.ETF, AssetClass.OPTION],
            capabilities=[Capability.QUOTE, Capability.FUNDAMENTALS, Capability.NEWS, Capability.OPTIONS_CHAIN],
            data_types=["quote", "fundamentals", "news", "options_chain"],
            priority=40,
            enabled=True,
            configured=bool(settings.fmp_api_key),
            authenticated=bool(settings.fmp_api_key),
            entitlement_status=EntitlementStatus.UNKNOWN if settings.fmp_api_key else EntitlementStatus.AUTHENTICATION_FAILED,
            supports_fundamentals=True,
            supports_news=True,
            rate_limit_policy=RateLimitPolicy(source="provider_plan"),
        )
    )
    registry.register(
        ProviderRegistration(
            provider_id="finnhub",
            display_name="Finnhub",
            asset_classes=[AssetClass.EQUITY, AssetClass.NEWS],
            capabilities=[Capability.QUOTE, Capability.NEWS],
            data_types=["quote", "news"],
            priority=50,
            enabled=True,
            configured=bool(settings.finnhub_api_key),
            authenticated=bool(settings.finnhub_api_key),
            entitlement_status=EntitlementStatus.UNKNOWN if settings.finnhub_api_key else EntitlementStatus.AUTHENTICATION_FAILED,
            supports_news=True,
            rate_limit_policy=RateLimitPolicy(source="provider_plan"),
        )
    )
    registry.register(
        ProviderRegistration(
            provider_id="fred",
            display_name="FRED",
            asset_classes=[AssetClass.ECONOMIC],
            capabilities=[Capability.ECONOMIC_DATA],
            data_types=["economic"],
            priority=20,
            enabled=True,
            configured=bool(settings.fred_api_key),
            authenticated=bool(settings.fred_api_key),
            entitlement_status=EntitlementStatus.UNKNOWN if settings.fred_api_key else EntitlementStatus.AUTHENTICATION_FAILED,
            rate_limit_policy=RateLimitPolicy(source="provider_plan"),
        )
    )
    registry.register(
        ProviderRegistration(
            provider_id="internal-demo",
            display_name="Internal demo/fallback data",
            asset_classes=list(AssetClass),
            capabilities=[Capability.QUOTE, Capability.HISTORICAL_CANDLES, Capability.MARKET_DEPTH, Capability.OPTIONS_CHAIN],
            data_types=["quote", "ohlcv", "depth", "options_chain"],
            priority=900,
            enabled=True,
            configured=True,
            authenticated=False,
            entitlement_status=EntitlementStatus.ENTITLED,
            supports_historical=True,
            supports_depth=True,
            notes="Explicit fallback/demo source. Must never be labelled real-time.",
        )
    )
    return registry


def get_provider_registry() -> MarketDataProviderRegistry:
    global _registry
    if _registry is None:
        _registry = build_default_registry()
    return _registry


def reset_provider_registry(registry: MarketDataProviderRegistry | None = None) -> None:
    global _registry
    _registry = registry
