# Trading OMS

Canonical states:

```text
CREATED -> PENDING_RISK -> APPROVED -> SUBMITTED_TO_SIMULATOR -> ACKNOWLEDGED
-> PARTIALLY_FILLED/FILLED
```

Terminal states include:

```text
RISK_REJECTED, CANCELLED, REJECTED, EXPIRED, ERROR
```

Invalid state transitions raise explicit errors. Duplicate prevention uses account, deployment, proposal and idempotency key.
