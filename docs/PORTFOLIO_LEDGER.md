# Portfolio Ledger

Phase 8 uses append-only ledger entries for canonical paper accounting.

Events currently covered:

- cash deposit
- fill
- fees
- realized P&L from reductions

Accounting method:

- average-cost position accounting
- Decimal precision for cash, price, quantity, fees and P&L
- account snapshots are rebuilt from ledger entries plus marks

This is a deterministic paper ledger, not broker-equivalent settlement or margin accounting.
