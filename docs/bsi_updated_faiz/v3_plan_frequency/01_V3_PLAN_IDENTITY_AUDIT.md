# BSI V3 Plan Frequency Audit

Source: `data/research/bsi_v3_planned_entry_jan_aug_2026.json` plus live Docker DB state.

Plan identity now separates context, thesis, POI, entry opportunity, confirmation, and execution. Repeated scans update an active canonical plan instead of appending a duplicate when the POI/opportunity is the same.

| Strategy                        | Class            | Planning TF  | Confirmation TF  | Raw  | Planned | Expired | Opp/day* |
| ------------------------------- | ---------------- | ------------ | ---------------- | ---- | ------- | ------- | -------- |
| bsi_v3_order_flow               | htf_or_hybrid    | H4,M15       | M1_OR_M5_ALLOWED | 1823 | 831     | 992     | 10.66    |
| bsi_v3_smt_divergence           | htf_or_hybrid    | M15,M1       | M1_REQUIRED      | 0    | 0       | 0       | 0.0      |
| bsi_v3_abc                      | htf_or_hybrid    | H4,M15       | M1_OR_M5_ALLOWED | 133  | 51      | 82      | 0.78     |
| bsi_v3_abcd                     | htf_or_hybrid    | H4,M15       | M1_OR_M5_ALLOWED | 73   | 32      | 41      | 0.43     |
| bsi_v3_asian_v2                 | session/intraday | M15,M1       | M1_REQUIRED      | 0    | 0       | 0       | 0.0      |
| bsi_v3_0930                     | session/intraday | M15,M1       | M1_REQUIRED      | 0    | 0       | 0       | 0.0      |
| bsi_v3_reactionary_block        | htf_or_hybrid    | M15          | M1_OR_M5_ALLOWED | 3643 | 1577    | 2066    | 21.3     |
| bsi_v3_ict_silver_bullet        | session/intraday | M1           | M1_REQUIRED      | 0    | 0       | 0       | 0.0      |
| bsi_v3_silver_bullet_with_bias  | session/intraday | H1,M15,M5,M1 | M1_OR_M5_ALLOWED | 0    | 0       | 0       | 0.0      |
| bsi_v3_4h_order_block           | htf_or_hybrid    | H4,M15       | M1_OR_M5_ALLOWED | 73   | 32      | 41      | 0.43     |
| bsi_v3_mmxm                     | htf_or_hybrid    | H4,H1,M15,M5 | M5_REQUIRED      | 1797 | 817     | 980     | 10.51    |
| bsi_v3_mmxm_second_distribution | htf_or_hybrid    | H1,M15       | M1_OR_M5_ALLOWED | 2467 | 1322    | 1145    | 14.43    |
| bsi_v3_holy_grail               | htf_or_hybrid    | H1,M5        | M5_REQUIRED      | 1799 | 813     | 986     | 10.52    |
| bsi_v3_juggernaut               | htf_or_hybrid    | M1,M5        | M1_OR_M5_ALLOWED | 4    | 1       | 3       | 0.02     |
| bsi_v3_spectre                  | htf_or_hybrid    | M15          | M1_OR_M5_ALLOWED | 3644 | 1578    | 2066    | 21.31    |
| bsi_v3_monday_range             | session/intraday | M15          | M1_OR_M5_ALLOWED | 129  | 60      | 69      | 0.75     |
| bsi_v3_weaver                   | htf_or_hybrid    | H1,M15       | M1_OR_M5_ALLOWED | 1043 | 456     | 587     | 6.1      |
| bsi_v3_standard_deviation_po3   | htf_or_hybrid    | M5           | M1_OR_M5_ALLOWED | 3590 | 1527    | 2063    | 20.99    |
| bsi_v3_ar50                     | session/intraday | M15          | M1_OR_M5_ALLOWED | 2684 | 1171    | 1513    | 15.7     |
| bsi_v3_ifvg_po3                 | htf_or_hybrid    | M1           | M1_REQUIRED      | 0    | 0       | 0       | 0.0      |
| bsi_v3_turtle_soups_ranges      | session/intraday | M1           | M1_REQUIRED      | 0    | 0       | 0       | 0.0      |
| bsi_v3_yin_yang                 | htf_or_hybrid    | M15          | M1_OR_M5_ALLOWED | 16   | 5       | 11      | 0.09     |
| bsi_v3_4h_candle_ranges         | htf_or_hybrid    | H4,M15       | M1_OR_M5_ALLOWED | 0    | 0       | 0       | 0.0      |
| bsi_v3_smt_session_hl           | session/intraday | M15,M1       | M1_REQUIRED      | 0    | 0       | 0       | 0.0      |
| bsi_v3_1h_candle_ranges         | htf_or_hybrid    | H1,M1        | M1_REQUIRED      | 0    | 0       | 0       | 0.0      |
| bsi_v3_enigma_range             | htf_or_hybrid    | H4,H1,M15    | M1_OR_M5_ALLOWED | 1115 | 348     | 767     | 6.52     |