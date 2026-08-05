# Phase 11 Trading Flow Audit

Canonical flow:

```text
strategy decision -> trade proposal -> risk evaluation -> approved order intent -> OMS PaperOrder -> broker adapter -> broker order -> execution -> fill -> position/ledger -> portfolio -> reconciliation
```

Existing canonical models live in `backend/trading/models.py`.

- Orders: `PaperOrder`, `OrderIntent`, `OrderStatus`.
- Fills: `PaperFill`.
- Accounts: `PaperAccount`, `PaperAccountSnapshot`.
- Risk: `RiskEvaluation`, `RiskDecision`, `RiskPolicy`.
- Portfolio: `LedgerEntry`, `Position`, `PortfolioSnapshot`.
- Reconciliation: `ReconciliationResult`.
- Emergency disable: `EmergencyControl`.
- Idempotency: `OrderIntent.idempotency_key` and broker idempotency store.
- Correlation: `correlation_id` and `causation_id`.

Phase 11 does not duplicate order or portfolio models. Broker orders are metadata and receipts linked to canonical OMS orders.
