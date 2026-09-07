# BSI V2 Durable Lifecycle Report

Date: 2026-09-03

## Verdict

Durable lifecycle foundation is implemented for BSI_BASELINE_V2_AUDIOVISUAL research/replay.

DEMO remains disabled and not approved by this report.

## Implemented

- Added `BSIDurableLifecycleStore` in `backend/mt5_strategies/families/bsi_v2_lifecycle.py`.
- Persists lifecycle records and transitions to JSON for restart-safe research/replay validation.
- Scopes stored lifecycle by `namespace` and `replay_run_id`.
- Keeps methodology opportunity identity separate from account execution identity.
- Stores account execution ids on the lifecycle record, without adding account id to `bsi_entry_opportunity_id`.
- Threads optional `account_id` and `replay_run_id` through V2 evaluator contexts only.

## Lifecycle States

Supported states remain:

- DETECTED
- THESIS_CREATED
- ENTRY_ARMED
- ENTRY_AVAILABLE
- CONSUMED
- INVALIDATED
- EXPIRED

## Duplicate Prevention

Restart behavior is validated:

- First pass can create and consume an opportunity.
- A new store instance reading the same persisted file rejects the same opportunity as already consumed.
- A separate replay run id can evaluate the same methodology setup independently.

## Validation

Command:

`python -m pytest backend/tests/test_bsi_v2_validation_readiness.py -q`

Result:

`3 passed`

Command:

`python -m pytest backend/tests/test_bsi_v2_foundation.py backend/tests/test_bsi_v2_new_york.py backend/tests/test_bsi_v2_all_strategies.py backend/tests/test_bsi_v2_validation_readiness.py -q`

Result:

`40 passed`

## Demo Gate

Passed for durable lifecycle mechanics.

Not sufficient for DEMO because historical PIT replay/full backfill was not executable in this environment.
