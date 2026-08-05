# IBKR Real Paper Acceptance

The acceptance order is limited to one manually confirmed EUR/USD paper trade.

Required gates:

- real IBKR paper adapter
- PostgreSQL source of truth
- paper account verified
- account allowlisted
- EUR/USD contract verified from real IBKR
- recovery complete
- reconciliation matched
- no blocking incident
- no duplicate order reference
- manual confirmation

The current code blocks execution until a verified real adapter is available.
