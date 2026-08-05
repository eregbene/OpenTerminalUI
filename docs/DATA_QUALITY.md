# Data Quality

Implemented controls:

- data status enums for realtime, delayed, cached, stale, fallback, simulated, demo, unavailable and partial data
- separate origin, entitlement, availability and provider-health concepts
- candle quality flags
- reusable candle validation policy
- representative frontend badges for realtime/delayed/cached/fallback/stale/simulated/unavailable states

Legacy paths still need broad migration so every endpoint emits canonical metadata.
