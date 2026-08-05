# Forex Strategy API

## Strategy Registry

- `GET /api/forex-strategies`
- `GET /api/forex-strategies/{strategy_id}`
- `GET /api/forex-strategies/{strategy_id}/performance`
- `POST /api/forex-strategies/{strategy_id}/enable-paper`
- `POST /api/forex-strategies/{strategy_id}/pause`
- `POST /api/forex-strategies/{strategy_id}/resume`
- `PATCH /api/forex-strategies/{strategy_id}/execution-mode`

Mutation endpoints require authentication. `RULE_BASED_AUTO_PAPER` is rejected unless the strategy is paper-approved or active.

## Candidates

- `GET /api/forex-signals/summary`
- `GET /api/forex-signals/candidates`
- `POST /api/forex-signals/generate`
- `GET /api/forex-signals/candidates/{candidate_id}`
- `POST /api/forex-signals/candidates/{candidate_id}/approve`
- `POST /api/forex-signals/candidates/{candidate_id}/reject`
- `POST /api/forex-signals/candidates/{candidate_id}/cancel`

Generation and mutation endpoints require authentication.

## Execution

- `GET /api/forex-execution/active`
- `GET /api/forex-execution/history`
- `GET /api/forex-execution/{trade_id}`
- `POST /api/forex-execution/{trade_id}/exit`
- `GET /api/forex-execution/orders`
- `GET /api/forex-execution/orders/{order_id}`
- `POST /api/forex-execution/orders/{order_id}/cancel`
- `GET /api/forex-execution/reconciliation`
- `GET /api/forex-execution/reconciliation/{trade_id}`
- `POST /api/forex-execution/reconciliation/run`

Manual exit remains paper-only and does not enable live broker execution.

## IBKR Acceptance

- `GET /api/brokers/ibkr/status`
- `GET /api/brokers/ibkr/account`
- `GET /api/brokers/ibkr/contracts`
- `POST /api/brokers/ibkr/connect`
- `POST /api/brokers/ibkr/disconnect`
- `POST /api/brokers/ibkr/reconcile`
