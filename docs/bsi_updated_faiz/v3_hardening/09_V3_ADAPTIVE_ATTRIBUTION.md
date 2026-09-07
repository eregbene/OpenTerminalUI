# BSI V3 Planned-Entry Hardening Audit

Source: `data\research\bsi_v3_planned_entry_jan_aug_2026.json`

Status: `PARTIAL_HARDENING_AUDIT_FROM_EXISTING_COMPACT_ARTIFACT`

Important limitation: the compact Jan-Aug artifact stores full aggregate totals but not every timestamped planned trade row. Exact dedup, latency, same-bar, portfolio sequencing, and adaptive attribution need an instrumented replay artifact.

Reported adaptive delta versus planned: `{'net_r': -67.7626, 'pf': 0.0464, 'win_rate': 0.34, 'max_dd_r': -1.7343}`

Exact per-trade adaptive attribution requires full timestamped counterfactual rows.
