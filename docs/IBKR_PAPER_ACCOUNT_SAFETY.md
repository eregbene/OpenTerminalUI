# IBKR Paper Account Safety

Order submission requires:

- paper account verification,
- server-side account allow-list,
- live trading disabled,
- paper order submission enabled,
- emergency disable inactive,
- canonical OMS order,
- deterministic risk approval,
- user confirmation,
- unique idempotency key.

If paper status cannot be verified, the broker returns `BROKER_ENVIRONMENT_UNVERIFIED`.
