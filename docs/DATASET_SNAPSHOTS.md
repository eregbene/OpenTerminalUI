# Dataset Snapshots

Implemented in `backend/market_data/snapshots.py`.

Snapshot metadata captures:

- instruments
- asset classes
- timeframes
- date range
- provider
- adjustment mode
- validation policy
- quality status
- dataset version
- content hash
- resampling config
- excluded/corrected records

The content hash is deterministic for reproducibility hooks.
# Strategy Research Usage

Phase 7 `DatasetSnapshot` records symbol, timeframe, bars, provider, adjustment mode and quality policy. The dataset hash is included in every research lineage record and reproducibility manifest.
