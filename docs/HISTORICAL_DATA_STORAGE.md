# Historical Data Storage

Current storage remains SQLite-compatible with optional PostgreSQL deployment. Existing OHLCV cache/database paths are preserved.

Phase 4 adds snapshot metadata and canonical bar models for future separation of:

- raw provider data
- normalized data
- validated data
- resampled data

No large migration was performed.
