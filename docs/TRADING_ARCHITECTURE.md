# Trading Architecture

Phase 8 adds `backend/trading` as the canonical internal paper-trading boundary.

Flow:

```text
Research candidate -> human deployment approval -> strategy intent -> risk evaluation
-> paper order -> simulator -> fill -> ledger -> account/position snapshot
```

Ownership:

| Layer | Package | Responsibility |
|---|---|---|
| Deployment Controller | `backend/trading/deployments.py` | Human approval, enabled/stale status. |
| Risk Engine | `backend/trading/risk` | Sizing, account, position, data and emergency checks. |
| OMS | `backend/trading/oms` | Order lifecycle, duplicate prevention, cancellation. |
| Execution Simulator | `backend/trading/execution` | Deterministic internal fills with execution model metadata. |
| Portfolio Ledger | `backend/trading/portfolio` | Append-only fill ledger and reproducible account rebuilds. |
| Persistence | `backend/trading/persistence.py` | File-backed vertical-slice store. |

The package does not call brokers, LLMs or frontend code.
