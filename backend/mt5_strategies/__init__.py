"""Unified, deterministic, regime-aware multi-strategy layer feeding the MT5 autonomous
trading pipeline. See backend/mt5_strategies/families.py for the canonical strategy
implementations and backend/mt5_strategies/models.py for the shared StrategySignal contract
and activation registry (ACTIVE_MT5 / SHADOW_MT5).

No IBKR dependency anywhere in this package -- every strategy operates on already-fetched MT5
candles/quotes and the existing broker-agnostic backend.market_structure engine.
"""
