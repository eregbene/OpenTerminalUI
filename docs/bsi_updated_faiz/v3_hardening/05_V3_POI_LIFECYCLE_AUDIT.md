# BSI V3 Planned-Entry Hardening Audit

Source: `data\research\bsi_v3_planned_entry_jan_aug_2026.json`

Status: `PARTIAL_HARDENING_AUDIT_FROM_EXISTING_COMPACT_ARTIFACT`

Important limitation: the compact Jan-Aug artifact stores full aggregate totals but not every timestamped planned trade row. Exact dedup, latency, same-bar, portfolio sequencing, and adaptive attribution need an instrumented replay artifact.

Expired/unconfirmed setups reported by existing replay: `13412`.

Runtime terminal states now include expired, invalidated, missed, submitting, broker accepted, consumed, and rejected.
