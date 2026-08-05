# Broker Architecture

The broker layer lives in `backend/brokers`.

- `BrokerReadAdapter` exposes health, accounts, snapshots, positions, open orders, executions, contracts, quotes, historical bars, and reconciliation.
- `BrokerOrderAdapter` exposes controlled submit and cancel only.
- Capabilities are declared in `BrokerCapabilities` and enforced before operations.
- IBKR support lives in `backend/brokers/ibkr`.
- The Research Agent and AI packages must not import broker order adapters.

Order mutation is exposed only through canonical paper OMS routes.
# Phase 12 Broker Boundary

Phase 12 consumes broker-paper records for portfolio accounting and operations but does not change broker submission, live trading state, or IBKR adapter authority.
