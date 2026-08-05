# Phase 12.1 Persistence

Added migration `0015_phase12_1_persistence_hardening.py`.

## Durable Additions

- `phase12_attribution_records`
- `phase12_risk_snapshots`
- `phase12_alert_rules`
- `phase12_incident_timeline_entries`
- `phase12_report_schedules`
- `phase12_replay_sessions`
- `phase12_journal_entries`
- `phase12_journal_notes`

Ledger ingestion is idempotent by source event, source identifier, and entry type. Journal generation is idempotent by portfolio and source trade. Replay sessions are isolated from current portfolio state.

## Restart Verification

Focused tests create a portfolio, attach a strategy, approve and activate allocation, ingest a ledger event, create snapshots, schedules, replay sessions, and journal entries, then reopen a new database session and verify state remains available without duplication.
