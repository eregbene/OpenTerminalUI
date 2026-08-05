# Forex Framework Architecture

The forex framework layer lives in `backend/forex_frameworks`. It converts existing deterministic forex intelligence snapshots into normalized framework signals.

## Boundary

The layer is read-only. It does not create orders, modify strategies, approve risk, mutate paper accounts, or call brokers.

## Flow

1. Forex candles are loaded through the existing forex service.
2. `ForexIntelligenceService` creates the deterministic feature snapshot.
3. `ForexFrameworkService` builds a `FrameworkContext`.
4. Registered framework plugins emit `FrameworkSignal` objects.
5. Signals are persisted in `forex_framework_signals`.
6. Comparison and thesis services summarize confluence.

## Plugin Contract

Every framework declares:

- `framework_id`
- `display_name`
- `version`
- `group`
- `status`
- `weight`
- `required_inputs`
- `shared_evidence_keys`
- deterministic `analyze(context)` behavior

Implemented frameworks use existing intelligence and market-structure outputs. Data-heavy frameworks return limited or research status until the required evidence exists.
