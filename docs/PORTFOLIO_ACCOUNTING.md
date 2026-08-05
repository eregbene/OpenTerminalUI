# Portfolio Accounting

Phase 12 uses deterministic average-cost accounting.

Rules:

- A fill belongs to one canonical order.
- Duplicate execution tuples are ignored by deterministic accounting tests.
- Trade cash flow includes notional and commission.
- Position quantity is derived from fills.
- Realized P&L is generated when a fill closes existing quantity.
- Unrealized P&L is generated from explicit marks.
- Missing marks produce `MISSING_MARK`.
- Missing FX rates produce `MISSING_FX_RATE`.
- Corrections must create superseding records rather than rewriting history.
