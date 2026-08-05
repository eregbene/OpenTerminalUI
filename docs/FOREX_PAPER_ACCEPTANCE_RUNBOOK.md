# Forex Paper Acceptance Runbook

## Read-Only Check

1. Start TWS or IB Gateway in paper mode.
2. Confirm API access is enabled.
3. Confirm read-only setting matches the intended test stage.
4. Set expected account and allow-list environment variables.
5. Connect through `/api/brokers/ibkr/connect`.
6. Verify `/api/brokers/ibkr/status`.
7. Verify `/api/brokers/ibkr/contracts`.
8. Run `/api/brokers/ibkr/reconcile`.

## One EUR/USD Paper Trade

Run only after read-only acceptance passes:

1. Select `EURUSD`.
2. Generate one deterministic candidate.
3. Review account, contract, risk, provider, and reconciliation state.
4. Approve with execution provider `IBKR_PAPER`.
5. Confirm broker acknowledgement and event ledger.
6. Close through OMS/broker controls.
7. Reconcile.
8. Restart backend and verify no duplicate order or unexpected position.
