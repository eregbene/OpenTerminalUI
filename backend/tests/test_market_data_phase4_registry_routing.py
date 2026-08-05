from __future__ import annotations

from backend.market_data.capabilities import Capability
from backend.market_data.models import AssetClass, EntitlementStatus
from backend.market_data.registry import MarketDataProviderRegistry, ProviderRegistration
from backend.market_data.routing import MarketDataRequest, MarketDataRouter


def _registry() -> MarketDataProviderRegistry:
    registry = MarketDataProviderRegistry()
    registry.register(
        ProviderRegistration(
            provider_id="primary",
            display_name="Primary",
            asset_classes=[AssetClass.EQUITY],
            capabilities=[Capability.QUOTE],
            priority=10,
            configured=True,
            entitlement_status=EntitlementStatus.ENTITLED,
        )
    )
    registry.register(
        ProviderRegistration(
            provider_id="fallback",
            display_name="Fallback",
            asset_classes=[AssetClass.EQUITY],
            capabilities=[Capability.QUOTE],
            priority=900,
            configured=True,
            entitlement_status=EntitlementStatus.ENTITLED,
        )
    )
    return registry


def test_registry_lookup_and_capability_selection() -> None:
    registry = _registry()
    assert registry.get("PRIMARY") is not None
    rows = registry.providers_for(capability=Capability.QUOTE, asset_class=AssetClass.EQUITY, require_configured=True)
    assert [row.provider_id for row in rows] == ["primary", "fallback"]


def test_router_honors_priority_and_pinning() -> None:
    router = MarketDataRouter(_registry())
    decision = router.route(MarketDataRequest(capability=Capability.QUOTE, asset_class=AssetClass.EQUITY))
    assert decision.selected_provider is not None
    assert decision.selected_provider.provider_id == "primary"

    pinned = router.route(MarketDataRequest(capability=Capability.QUOTE, asset_class=AssetClass.EQUITY, provider_id="fallback"))
    assert pinned.selected_provider is not None
    assert pinned.selected_provider.provider_id == "fallback"
    assert pinned.fallback_used is False


def test_router_rejects_fallback_when_disabled() -> None:
    registry = MarketDataProviderRegistry()
    registry.register(
        ProviderRegistration(
            provider_id="internal-demo",
            display_name="Demo",
            asset_classes=[AssetClass.EQUITY],
            capabilities=[Capability.QUOTE],
            priority=900,
            configured=True,
            entitlement_status=EntitlementStatus.ENTITLED,
        )
    )
    decision = MarketDataRouter(registry).route(
        MarketDataRequest(capability=Capability.QUOTE, asset_class=AssetClass.EQUITY, allow_fallback=False)
    )
    assert decision.selected_provider is None
    assert decision.rejected[0]["reason"] == "fallback_disabled"
