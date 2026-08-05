# Phase 12.1 Report Scheduling

Report schedules are durable records owned by user and workspace. They support report type, portfolio, frequency, timezone, enabled state, next run, last run, retry policy, and payload metadata.

Deleting a schedule disables it instead of removing history.

## Remaining Work

The persistent schedule model is implemented. Automatic due-schedule polling and durable job execution remain a mandatory gate before declaring all Phase 12.1 report criteria complete.
