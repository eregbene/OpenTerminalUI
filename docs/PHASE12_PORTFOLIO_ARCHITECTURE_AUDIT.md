# Phase 12 Portfolio Architecture Audit

Canonical flow:

`research candidate -> approved paper deployment -> active strategy instance -> trade proposal -> risk decision -> OMS order -> broker order -> execution -> fill -> position -> portfolio ledger -> valuation -> P&L -> attribution -> risk -> report`

Reused models:

- Trading and paper account intent/order/fill models from `backend/trading`.
- Broker abstraction and IBKR paper adapter from `backend/brokers`.
- Existing risk engine remains authoritative for pre-trade decisions.
- Existing AI evidence architecture remains read-only.

New Phase 12 models:

- `Phase12Portfolio`
- `Phase12PortfolioMembership`
- `Phase12StrategyAllocation`
- `Phase12Snapshot`
- `Phase12LedgerEntry`
- `Phase12PerformanceSnapshot`
- `Phase12ExecutionQuality`
- `Phase12Alert`
- `Phase12Incident`
- `Phase12Report`
- `Phase12ReplayEvent`
- `Phase12OperationalHealth`

Remaining file-backed or in-memory state:

- The existing canonical trading control service still has simulated/in-memory paths from earlier phases.
- Phase 12 records are database-backed, but full migration of all historical operational state into Phase 12 tables is not complete.
