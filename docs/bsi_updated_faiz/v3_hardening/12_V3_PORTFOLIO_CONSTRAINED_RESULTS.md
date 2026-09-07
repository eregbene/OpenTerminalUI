# BSI V3 Planned-Entry Hardening Audit

Source: `data\research\bsi_v3_planned_entry_jan_aug_2026.json`

Status: `PARTIAL_HARDENING_AUDIT_FROM_EXISTING_COMPACT_ARTIFACT`

Important limitation: the compact Jan-Aug artifact stores full aggregate totals but not every timestamped planned trade row. Exact dedup, latency, same-bar, portfolio sequencing, and adaptive attribution need an instrumented replay artifact.

Portfolio-constrained $10K at 0.25% risk cannot be finalized from compact aggregates. Simple unconstrained reference is included in JSON only and is not the final portfolio result.
