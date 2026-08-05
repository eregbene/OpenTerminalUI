# Strategy Events

Phase 6 emits in-memory event objects for:

- `strategy.decision.created`
- `strategy.proposal.created`

Events include:

- schema version
- event id
- strategy id/version
- instrument id
- as-of timestamp
- dataset snapshot id
- correlation id
- idempotency key
- evidence reference
- JSON payload

These events are ready for future event-bus persistence but are not currently published to Redis or Kafka.
