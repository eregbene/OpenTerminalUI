# Research Limitations

Known limitations:

- File-backed registry is implemented for the vertical slice; relational persistence migration exists but service is not yet switched to ORM tables.
- Single-symbol accounting is implemented; multi-instrument portfolio accounting remains legacy/future work.
- Partial fills, broker margin, live balances and order routing are intentionally not implemented.
- True Monte Carlo and true multi-objective optimization are placeholders in this slice.
- Asset-specific corporate actions, futures/options multipliers and forex conversion are flagged as unsupported unless handled by legacy paths.
