from __future__ import annotations

from pydantic import BaseModel, Field

from backend.brokers.errors import BrokerCapabilityError


class BrokerCapabilities(BaseModel):
    broker: str
    environment: str
    account_types: set[str] = Field(default_factory=set)
    asset_types: set[str] = Field(default_factory=set)
    order_types: set[str] = Field(default_factory=set)
    time_in_force: set[str] = Field(default_factory=set)
    market_data: set[str] = Field(default_factory=set)
    historical_data: set[str] = Field(default_factory=set)
    supports_cancel: bool = True
    supports_modify: bool = False
    supports_streaming: bool = False
    supports_currency_conversion: bool = False
    live_trading_enabled: bool = False

    def require_asset(self, asset_type: str) -> None:
        if asset_type.upper() not in self.asset_types:
            raise BrokerCapabilityError("UNSUPPORTED_ASSET", f"unsupported asset type: {asset_type}", status_code=422)

    def require_order(self, order_type: str, tif: str) -> None:
        if order_type.upper() not in self.order_types:
            raise BrokerCapabilityError("UNSUPPORTED_ORDER_TYPE", f"unsupported order type: {order_type}", status_code=422)
        if tif.upper() not in self.time_in_force:
            raise BrokerCapabilityError("UNSUPPORTED_TIME_IN_FORCE", f"unsupported time in force: {tif}", status_code=422)
