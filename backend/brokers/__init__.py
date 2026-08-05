from backend.brokers.ibkr import ibkr_adapter
from backend.brokers.mt5 import mt5_adapter
from backend.brokers.registry import broker_registry

broker_registry.register("ibkr", ibkr_adapter)
broker_registry.register("mt5", mt5_adapter)

__all__ = ["broker_registry"]
