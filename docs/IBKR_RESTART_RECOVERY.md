# IBKR Restart Recovery

FX-5 adds a recovery gate:

```text
RECOVERY_IN_PROGRESS
```

During recovery:

- candidate generation may remain read-only
- manual broker approval is blocked
- active trades remain visible
- emergency disable remains available
- no new IBKR paper submission is allowed

Recovery completes only after reconciliation. If reconciliation is blocking, the recovery state remains failed/blocking.
# FX-5B Update

Real IBKR paper mode must recover from PostgreSQL, not Redis or file state. Backend restart and Redis restart must not lose broker orders, executions, incidents, or reconciliations.
