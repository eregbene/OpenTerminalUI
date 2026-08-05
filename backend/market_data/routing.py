from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.market_data.capabilities import Capability
from backend.market_data.models import AssetClass, EntitlementStatus, ProviderState
from backend.market_data.registry import MarketDataProviderRegistry, ProviderRegistration, get_provider_registry


@dataclass(frozen=True)
class MarketDataRequest:
    capability: Capability
    asset_class: AssetClass = AssetClass.UNKNOWN
    symbol: str | None = None
    provider_id: str | None = None
    require_configured: bool = False
    require_entitled: bool = False
    allow_fallback: bool = True
    max_staleness_seconds: float | None = None
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutingDecision:
    selected_provider: ProviderRegistration | None
    candidates: list[str]
    rejected: list[dict[str, str]]
    fallback_used: bool = False
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected_provider": self.selected_provider.provider_id if self.selected_provider else None,
            "candidates": self.candidates,
            "rejected": self.rejected,
            "fallback_used": self.fallback_used,
            "reason": self.reason,
        }


class MarketDataRouter:
    def __init__(self, registry: MarketDataProviderRegistry | None = None) -> None:
        self.registry = registry or get_provider_registry()

    def route(self, request: MarketDataRequest) -> RoutingDecision:
        if request.provider_id:
            provider = self.registry.get(request.provider_id)
            if not provider:
                return RoutingDecision(None, [], [{"provider": request.provider_id, "reason": "not_registered"}], reason="pinned_provider_not_registered")
            rejected = self._rejection_reason(provider, request)
            if rejected:
                return RoutingDecision(None, [provider.provider_id], [{"provider": provider.provider_id, "reason": rejected}], reason="pinned_provider_rejected")
            return RoutingDecision(provider, [provider.provider_id], [], fallback_used=False, reason="pinned_provider")

        candidates = self.registry.providers_for(
            capability=request.capability,
            asset_class=request.asset_class if request.asset_class != AssetClass.UNKNOWN else None,
            require_configured=False,
            require_entitled=False,
        )
        rejected: list[dict[str, str]] = []
        for provider in candidates:
            reason = self._rejection_reason(provider, request)
            if reason:
                rejected.append({"provider": provider.provider_id, "reason": reason})
                continue
            fallback_used = provider.provider_id == "internal-demo" or provider.priority >= 800
            if fallback_used and not request.allow_fallback:
                rejected.append({"provider": provider.provider_id, "reason": "fallback_disabled"})
                continue
            return RoutingDecision(
                provider,
                [p.provider_id for p in candidates],
                rejected,
                fallback_used=fallback_used,
                reason="selected_by_priority",
            )
        return RoutingDecision(None, [p.provider_id for p in candidates], rejected, reason="no_provider_available")

    @staticmethod
    def _rejection_reason(provider: ProviderRegistration, request: MarketDataRequest) -> str | None:
        if not provider.enabled:
            return "disabled"
        if request.capability not in provider.capabilities and request.capability.value not in provider.capabilities:
            return "capability_missing"
        if request.asset_class != AssetClass.UNKNOWN and request.asset_class not in provider.asset_classes and request.asset_class.value not in provider.asset_classes:
            return "asset_class_unsupported"
        if request.require_configured and not provider.configured:
            return "not_configured"
        if request.require_entitled and provider.entitlement_status != EntitlementStatus.ENTITLED:
            return "not_entitled"
        if provider.health.health_status in {ProviderState.DOWN, ProviderState.DISABLED}:
            return "provider_down"
        if provider.entitlement_status in {EntitlementStatus.AUTHENTICATION_FAILED, EntitlementStatus.SUBSCRIPTION_REQUIRED} and request.require_configured:
            return provider.entitlement_status.value
        return None
