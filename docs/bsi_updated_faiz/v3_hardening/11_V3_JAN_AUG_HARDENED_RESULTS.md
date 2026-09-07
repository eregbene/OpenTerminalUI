# BSI V3 Planned-Entry Hardening Audit

Source: `data\research\bsi_v3_planned_entry_jan_aug_2026.json`

Status: `PARTIAL_HARDENING_AUDIT_FROM_EXISTING_COMPACT_ARTIFACT`

Important limitation: the compact Jan-Aug artifact stores full aggregate totals but not every timestamped planned trade row. Exact dedup, latency, same-bar, portfolio sequencing, and adaptive attribution need an instrumented replay artifact.

| Model                                     | Trades | WR    | Net R     | Expectancy | PF     | Max LS | Max DD R |
| ----------------------------------------- | ------ | ----- | --------- | ---------- | ------ | ------ | -------- |
| instant_entry_same_window                 | 24033  | 64.85 | 6328.2157 | 0.2633     | 1.7805 | 9      | 76.6369  |
| planned_entry                             | 10621  | 70.04 | 4544.5328 | 0.4279     | 2.5305 | 8      | 13.2343  |
| planned_entry_plus_adaptive_bucket_filter | 10285  | 70.38 | 4476.7702 | 0.4353     | 2.5769 | 6      | 11.5     |
| planned_entry_adaptive_skipped            | 336    | 59.52 | 67.7626   | 0.2017     | 1.5198 | 8      | 22.9982  |

| Strategy                        | Raw  | Planned | Net R    | WR    | PF     | Expired/Unconfirmed |
| ------------------------------- | ---- | ------- | -------- | ----- | ------ | ------------------- |
| bsi_v3_order_flow               | 1823 | 831     | 345.075  | 69.55 | 2.4548 | 992                 |
| bsi_v3_smt_divergence           | 0    | 0       | 0.0      | None  | None   | 0                   |
| bsi_v3_abc                      | 133  | 51      | 46.5     | 84.31 | 6.8125 | 82                  |
| bsi_v3_abcd                     | 73   | 32      | 26.0     | 81.25 | 5.3333 | 41                  |
| bsi_v3_asian_v2                 | 0    | 0       | 0.0      | None  | None   | 0                   |
| bsi_v3_0930                     | 0    | 0       | 0.0      | None  | None   | 0                   |
| bsi_v3_reactionary_block        | 3643 | 1577    | 668.6036 | 69.31 | 2.4551 | 2066                |
| bsi_v3_ict_silver_bullet        | 0    | 0       | 0.0      | None  | None   | 0                   |
| bsi_v3_silver_bullet_with_bias  | 0    | 0       | 0.0      | None  | None   | 0                   |
| bsi_v3_4h_order_block           | 73   | 32      | 26.0     | 81.25 | 5.3333 | 41                  |
| bsi_v3_mmxm                     | 1797 | 817     | 481.1706 | 75.15 | 3.7764 | 980                 |
| bsi_v3_mmxm_second_distribution | 2467 | 1322    | 257.9786 | 63.39 | 1.5461 | 1145                |
| bsi_v3_holy_grail               | 1799 | 813     | 473.883  | 75.28 | 3.6837 | 986                 |
| bsi_v3_juggernaut               | 4    | 1       | 0.5      | 100.0 | inf    | 3                   |
| bsi_v3_spectre                  | 3644 | 1578    | 670.1036 | 69.33 | 2.4584 | 2066                |
| bsi_v3_monday_range             | 129  | 60      | 23.4544  | 70.0  | 2.303  | 69                  |
| bsi_v3_weaver                   | 1043 | 456     | 206.1403 | 72.15 | 2.7621 | 587                 |
| bsi_v3_standard_deviation_po3   | 3590 | 1527    | 896.049  | 75.31 | 3.6615 | 2063                |
| bsi_v3_ar50                     | 2684 | 1171    | 424.8992 | 68.66 | 2.2358 | 1513                |
| bsi_v3_ifvg_po3                 | 0    | 0       | 0.0      | None  | None   | 0                   |
| bsi_v3_turtle_soups_ranges      | 0    | 0       | 0.0      | None  | None   | 0                   |
| bsi_v3_yin_yang                 | 16   | 5       | 1.5      | 60.0  | 1.75   | 11                  |
| bsi_v3_4h_candle_ranges         | 0    | 0       | 0.0      | None  | None   | 0                   |
| bsi_v3_smt_session_hl           | 0    | 0       | 0.0      | None  | None   | 0                   |
| bsi_v3_1h_candle_ranges         | 0    | 0       | 0.0      | None  | None   | 0                   |
| bsi_v3_enigma_range             | 1115 | 348     | -3.3245  | 53.45 | 0.9783 | 767                 |
