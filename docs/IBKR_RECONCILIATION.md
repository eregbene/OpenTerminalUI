# IBKR Reconciliation

FX-5 reconciliation compares local broker order records and event state.

Statuses include:

- `MATCHED`
- `LOCAL_ONLY`
- `BROKER_ONLY`
- `QUANTITY_MISMATCH`
- `PRICE_MISMATCH`
- `STATUS_MISMATCH`
- `MISSING_COMMISSION`
- `MISSING_PROTECTIVE_ORDER`
- `ACCOUNT_MISMATCH`
- `UNKNOWN`

Blocking mismatches create operational incidents and block new IBKR paper submission until reviewed.
# FX-5B Update

Reconciliation compares local broker records with broker orders, executions, positions, and account state. Blocking states include account mismatch, position mismatch, unknown submission, missing protection, recovery in progress, and critical incidents.
