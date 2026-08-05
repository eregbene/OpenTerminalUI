# Forex Feature Store

Forex intelligence persists deterministic feature vectors in `forex_feature_vectors`.

Framework signals reference the feature vector ID when available and persist their own normalized payloads in `forex_framework_signals`.

## Purpose

- preserve reproducible analysis inputs
- link framework signals to deterministic evidence
- avoid recalculating or duplicating market-structure logic
- support read-only comparison, thesis, and future explainability

## Current Stores

- `forex_feature_vectors`: deterministic per-candle intelligence features
- `forex_framework_signals`: normalized framework outputs and confluence inputs

Neither store has trading authority.
