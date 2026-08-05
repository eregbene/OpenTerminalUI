# Forex Framework Signal Schema

Signals are normalized with `FrameworkSignal` in `backend/forex_frameworks/models.py` and persisted in `forex_framework_signals`.

## Main Fields

- `framework_id`
- `framework_name`
- `framework_version`
- `symbol`
- `timeframe`
- `analysis_timestamp`
- `feature_vector_id`
- `source_dataset_id`
- `bias`
- `signal_type`
- `confidence`
- `quality`
- `status`
- `supporting_evidence`
- `conflicting_evidence`
- `missing_evidence`
- `limitations`
- `zones`
- `levels`
- `risk_notes`

## Persistence

The database row stores the normalized payload, content hash, generated timestamp, and a unique key across framework, version, symbol, timeframe, analysis timestamp, and feature vector ID.
