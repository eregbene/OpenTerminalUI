# BSI V3 Planned-Entry Hardening Audit

Source: `data\research\bsi_v3_planned_entry_jan_aug_2026.json`

Status: `PARTIAL_HARDENING_AUDIT_FROM_EXISTING_COMPACT_ARTIFACT`

Important limitation: the compact Jan-Aug artifact stores full aggregate totals but not every timestamped planned trade row. Exact dedup, latency, same-bar, portfolio sequencing, and adaptive attribution need an instrumented replay artifact.

Runtime fix: confluence evidence now carries all linked plan IDs, and broker acceptance consumes all linked component plans together.

| Overlap Pair                                      | Sample Count |
| ------------------------------------------------- | ------------ |
| bsi_v3_4h_order_block + bsi_v3_abcd               | 25           |
| bsi_v3_reactionary_block + bsi_v3_spectre         | 25           |
| bsi_v3_ar50 + bsi_v3_reactionary_block            | 19           |
| bsi_v3_ar50 + bsi_v3_spectre                      | 19           |
| bsi_v3_4h_order_block + bsi_v3_abc                | 18           |
| bsi_v3_abc + bsi_v3_abcd                          | 18           |
| bsi_v3_order_flow + bsi_v3_reactionary_block      | 14           |
| bsi_v3_order_flow + bsi_v3_spectre                | 14           |
| bsi_v3_ar50 + bsi_v3_order_flow                   | 13           |
| bsi_v3_holy_grail + bsi_v3_standard_deviation_po3 | 13           |
| bsi_v3_mmxm + bsi_v3_standard_deviation_po3       | 12           |
| bsi_v3_order_flow + bsi_v3_weaver                 | 8            |
| bsi_v3_ar50 + bsi_v3_monday_range                 | 7            |
| bsi_v3_monday_range + bsi_v3_order_flow           | 7            |
| bsi_v3_monday_range + bsi_v3_reactionary_block    | 7            |
| bsi_v3_monday_range + bsi_v3_spectre              | 7            |
| bsi_v3_ar50 + bsi_v3_weaver                       | 6            |
| bsi_v3_reactionary_block + bsi_v3_weaver          | 6            |
| bsi_v3_spectre + bsi_v3_weaver                    | 6            |
| bsi_v3_4h_order_block + bsi_v3_order_flow         | 3            |
| bsi_v3_abc + bsi_v3_order_flow                    | 3            |
| bsi_v3_abcd + bsi_v3_order_flow                   | 3            |
| bsi_v3_4h_order_block + bsi_v3_reactionary_block  | 2            |
| bsi_v3_4h_order_block + bsi_v3_spectre            | 2            |
| bsi_v3_abc + bsi_v3_reactionary_block             | 2            |
| bsi_v3_abc + bsi_v3_spectre                       | 2            |
| bsi_v3_abcd + bsi_v3_reactionary_block            | 2            |
| bsi_v3_abcd + bsi_v3_spectre                      | 2            |
| bsi_v3_monday_range + bsi_v3_weaver               | 2            |
| bsi_v3_4h_order_block + bsi_v3_ar50               | 1            |
| bsi_v3_4h_order_block + bsi_v3_monday_range       | 1            |
| bsi_v3_4h_order_block + bsi_v3_weaver             | 1            |
| bsi_v3_abc + bsi_v3_ar50                          | 1            |
| bsi_v3_abc + bsi_v3_monday_range                  | 1            |
| bsi_v3_abc + bsi_v3_weaver                        | 1            |
| bsi_v3_abcd + bsi_v3_ar50                         | 1            |
| bsi_v3_abcd + bsi_v3_monday_range                 | 1            |
| bsi_v3_abcd + bsi_v3_weaver                       | 1            |
