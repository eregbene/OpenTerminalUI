# Forex Framework Registry

Framework definitions are centralized in `backend/forex_frameworks/registry.py`.

## Implemented

- `trend_following`
- `mean_reversion`
- `momentum`
- `breakout`
- `price_action`
- `support_resistance`
- `supply_demand`
- `smc`
- `ict`

## Limited Data

These frameworks are exposed with normalized contracts but return `INSUFFICIENT_DATA` until their evidence sources are available:

- `classical_technical`
- `market_structure`
- `wyckoff`
- `vsa`
- `dow_theory`
- `ichimoku`
- `fibonacci`
- `auction_market_theory`
- `market_profile`
- `volume_profile`
- `session_trading`
- `carry_macro`
- `correlation_intermarket`

## Research

These frameworks are intentionally marked research-only:

- `harmonic_patterns`
- `elliott_wave`
- `gann`

Research status means the framework is known to the registry, but the app does not claim production-grade deterministic detection yet.
