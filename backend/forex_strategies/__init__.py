"""Controlled forex strategy selection and paper-candidate workflow."""

from backend.forex_strategies.registry import strategy_registry
from backend.forex_strategies.service import forex_signal_service

__all__ = ["strategy_registry", "forex_signal_service"]
