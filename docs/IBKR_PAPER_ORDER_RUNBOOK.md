# IBKR Paper Order Runbook

1. Confirm `IBKR_MODE=PAPER`.
2. Confirm TWS/Gateway paper mode.
3. Run `/api/brokers/ibkr/read-only-acceptance`.
4. Verify EUR/USD contract source is `REAL_IBKR_PAPER`.
5. Prepare acceptance via `/api/brokers/ibkr/acceptance/prepare`.
6. Execute only after manual confirmation.
7. Verify acknowledgement, fill, commission, protective exit, and reconciliation.
8. Restart backend and Redis.
9. Reconcile again.

Do not submit XAU/USD. Do not resubmit unknown orders.
