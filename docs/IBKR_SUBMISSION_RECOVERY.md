# IBKR Submission Recovery

If a submission is sent and acknowledgement is missing, status becomes `SUBMISSION_STATUS_UNKNOWN`.

Recovery must query:

- open orders
- completed orders
- executions
- positions
- account cash

Matching uses order reference, broker order ID, permanent ID, execution ID, con_id, symbol, side, quantity, and account hash. New submissions stay blocked until the unknown state is resolved.
