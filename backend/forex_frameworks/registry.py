from __future__ import annotations

from collections import OrderedDict

from backend.forex_frameworks.models import FrameworkDefinition, FrameworkStatus
from backend.forex_frameworks.plugins import (
    BreakoutFramework,
    ForexFramework,
    ICTFramework,
    LimitedDataFramework,
    MeanReversionFramework,
    MomentumFramework,
    PriceActionFramework,
    SMCFramework,
    SupplyDemandFramework,
    SupportResistanceFramework,
    TrendFollowingFramework,
)
from backend.forex_intelligence.instruments import SUPPORTED_FOREX_SYMBOLS, SUPPORTED_TIMEFRAMES


FRAMEWORK_GROUPS: dict[str, str] = {
    "trend_following": "Quantitative",
    "mean_reversion": "Quantitative",
    "momentum": "Quantitative",
    "breakout": "Quantitative",
    "price_action": "Price Action",
    "support_resistance": "Price Action",
    "supply_demand": "Price Action",
    "classical_technical": "Classical Technical",
    "dow_theory": "Classical Technical",
    "ichimoku": "Classical Technical",
    "fibonacci": "Classical Technical",
    "harmonic_patterns": "Experimental",
    "elliott_wave": "Experimental",
    "gann": "Experimental",
    "market_structure": "Smart Money",
    "wyckoff": "Smart Money",
    "vsa": "Smart Money",
    "smc": "Smart Money",
    "ict": "Smart Money",
    "auction_market_theory": "Auction and Volume",
    "market_profile": "Auction and Volume",
    "volume_profile": "Auction and Volume",
    "session_trading": "Quantitative",
    "carry_macro": "Macro and Intermarket",
    "correlation_intermarket": "Macro and Intermarket",
}

DISPLAY_NAMES: dict[str, str] = {
    "classical_technical": "Classical Technical",
    "market_structure": "Market Structure",
    "wyckoff": "Wyckoff",
    "vsa": "Volume Spread Analysis",
    "dow_theory": "Dow Theory",
    "ichimoku": "Ichimoku",
    "fibonacci": "Fibonacci",
    "harmonic_patterns": "Harmonic Patterns",
    "elliott_wave": "Elliott Wave",
    "gann": "Gann",
    "auction_market_theory": "Auction Market Theory",
    "market_profile": "Market Profile",
    "volume_profile": "Volume Profile",
    "session_trading": "Session Trading",
    "carry_macro": "Carry and Macro Context",
    "correlation_intermarket": "Correlation and Intermarket",
}

RESEARCH_IDS = {"harmonic_patterns", "elliott_wave", "gann"}


class FrameworkRegistry:
    def __init__(self) -> None:
        symbols = set(SUPPORTED_FOREX_SYMBOLS)
        self._frameworks: OrderedDict[str, ForexFramework] = OrderedDict()
        for framework in [
            TrendFollowingFramework(symbols),
            MeanReversionFramework(symbols),
            MomentumFramework(symbols),
            BreakoutFramework(symbols),
            PriceActionFramework(symbols),
            SupportResistanceFramework(symbols),
            SupplyDemandFramework(symbols),
            SMCFramework(symbols),
            ICTFramework(symbols),
        ]:
            self.register(framework)
        for framework_id in FRAMEWORK_GROUPS:
            if framework_id in self._frameworks:
                continue
            status = FrameworkStatus.RESEARCH if framework_id in RESEARCH_IDS else FrameworkStatus.LIMITED
            self.register(LimitedDataFramework(symbols, framework_id, DISPLAY_NAMES.get(framework_id, framework_id.replace("_", " ").title()), FRAMEWORK_GROUPS[framework_id], status=status))

    def register(self, framework: ForexFramework) -> None:
        self._frameworks[framework.framework_id] = framework

    def all(self, enabled_only: bool = False) -> list[ForexFramework]:
        frameworks = list(self._frameworks.values())
        return [framework for framework in frameworks if self.definition(framework).enabled] if enabled_only else frameworks

    def get(self, framework_id: str) -> ForexFramework | None:
        return self._frameworks.get(framework_id)

    def require(self, framework_id: str) -> ForexFramework:
        framework = self.get(framework_id)
        if framework is None:
            raise ValueError(f"unsupported framework: {framework_id}")
        return framework

    def definition(self, framework: ForexFramework) -> FrameworkDefinition:
        return FrameworkDefinition(
            framework_id=framework.framework_id,
            display_name=framework.display_name,
            version=framework.version,
            status=framework.status,
            enabled=True,
            group=framework.group,
            supported_symbols=sorted(framework.supported_symbols),
            supported_timeframes=sorted(framework.supported_timeframes, key=lambda item: SUPPORTED_TIMEFRAMES.index(item) if item in SUPPORTED_TIMEFRAMES else 99),
            limitations=framework.limitations,
            dependencies=framework.dependencies,
            weight=framework.weight,
        )

    def definitions(self) -> list[FrameworkDefinition]:
        return [self.definition(framework) for framework in self.all()]


registry = FrameworkRegistry()
