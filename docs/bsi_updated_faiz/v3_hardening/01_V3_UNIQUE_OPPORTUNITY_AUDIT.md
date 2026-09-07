# BSI V3 Planned-Entry Hardening Audit

Source: `data\research\bsi_v3_planned_entry_jan_aug_2026.json`

Status: `PARTIAL_HARDENING_AUDIT_FROM_EXISTING_COMPACT_ARTIFACT`

Important limitation: the compact Jan-Aug artifact stores full aggregate totals but not every timestamped planned trade row. Exact dedup, latency, same-bar, portfolio sequencing, and adaptive attribution need an instrumented replay artifact.

Raw detections: `24033`

Reported planned trades: `10621`

Sample rows audited: `356`

Sample unique theses: `356`

Sample unique entry opportunities: `356`

Verdict: original `10,621` cannot be proven fully unique from the compact artifact alone.
