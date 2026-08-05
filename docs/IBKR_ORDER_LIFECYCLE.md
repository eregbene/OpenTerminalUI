# IBKR Order Lifecycle

FX-5 persists broker order records and append-only broker events.

## Supported Initial Type

`MARKET` is the initial supported acceptance order type.

## States

IBKR statuses are mapped into canonical internal states. `Submitted` is not treated as filled. Unknown submission results are marked `SUBMISSION_STATUS_UNKNOWN` and must reconcile before retry.

## Event Ledger

Events include order acknowledgement, status changes, fills, commissions, cancellation requests, rejections, connection loss/restoration, and reconciliation corrections.
# FX-5B Update

Broker orders, events, executions, reconciliations, recovery runs, and incidents are modeled as durable database records. Broker events are append-only by API convention.
