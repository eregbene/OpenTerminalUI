# Trading API

Canonical paper endpoints live under `/api/trading/paper`.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/accounts` | Create isolated internal paper account. |
| `GET` | `/accounts` | List canonical paper accounts. |
| `GET` | `/accounts/{account_id}/snapshot` | Return account, positions, orders, exposure and P&L. |
| `POST` | `/accounts/{account_id}/emergency-disable` | Enable or clear emergency override. |
| `POST` | `/accounts/{account_id}/reconcile` | Compare stored account with ledger rebuild. |
| `POST` | `/deployments` | Create a strategy deployment pending approval. |
| `POST` | `/deployments/{deployment_id}/approve` | Human approval boundary. |
| `POST` | `/deployments/{deployment_id}/stale` | Mark deployment stale. |
| `POST` | `/intents` | Submit strategy proposal intent through risk and OMS. |
| `GET` | `/orders` | List canonical paper orders. |
| `POST` | `/orders/{order_id}/simulate` | Submit approved order to the internal simulator. |
| `POST` | `/orders/{order_id}/cancel` | Idempotent cancellation. |

Existing `/api/paper/*` and `/api/oms/*` routes are preserved.
