# Portfolio Source Of Truth

Authoritative sources:

- Order intent: canonical OMS/trading order intent.
- Order state: OMS state machine.
- Execution and fill: broker adapter execution records or paper simulator records.
- Commission: execution/fill commission record.
- Cash: Phase 12 ledger plus broker-paper account snapshot when reconciled.
- Position quantity: sum of applicable fills after adjustments.
- Average cost: deterministic average-cost calculation.
- Mark price: explicit mark snapshot with source, mode, timestamp, freshness, and quality.
- Realized P&L: finalized ledger/accounting records.
- Unrealized P&L: mark-to-market valuation records.
- Net liquidation and equity: cash plus marked positions.
- Strategy equity: strategy equity snapshots.
- Exposure and drawdown: deterministic Phase 12 calculations.
- Broker reconciliation: reconciliation snapshot and mismatch records.

Value classes:

- Broker authoritative: external broker account/execution data after reconciliation.
- Internal calculated: P&L, exposure, attribution, risk, performance.
- Simulated: paper trading and IBKR paper adapter state.
- Cached: market data and health snapshots.
- Provisional: incomplete valuation or missing reconciliation.
- Reconciled: records with explicit reconciliation status.
