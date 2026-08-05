# IBKR Database Schema

FX-5B adds durable broker tables:

- `broker_connection_sessions`
- `broker_contracts`
- `broker_orders`
- `broker_events`
- `broker_executions`
- `broker_reconciliations`
- `broker_recovery_runs`
- `broker_incidents`

Broker events are append-only by API convention and protected by unique `(broker_order_id, sequence)`.

Real IBKR paper mode uses PostgreSQL as the source of truth. Legacy JSON state remains fixture-only and is not valid for real order submission.
