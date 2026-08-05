# IBKR Event Handling

Broker events are persisted to `broker_events` with:

- broker order ID
- event type
- broker timestamp
- received timestamp
- sequence
- payload
- payload hash

Expected real adapter events include next valid order ID, open order, order status, execution details, commission report, completed order, error, and connection closed.
