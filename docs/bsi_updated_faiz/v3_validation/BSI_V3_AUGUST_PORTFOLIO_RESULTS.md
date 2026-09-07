# BSI V3 August Portfolio Results

Raw baseline trades: `40`

Raw baseline net R: `20.0`

Mentor-managed net R: `26.0`

Adaptive-filtered trades: `0`

Adaptive-filtered win rate: `None`

Adaptive-filtered net R: `0.0`

Adaptive-filtered max losing streak: `0`

At 0.25% risk on $10,000, adaptive-filtered ending balance `$10000.0`.

Adaptive tuning mode: `IN_SAMPLE_MANAGED_R_BUCKET_GATE`. This is in-sample and must be forward-tested before V3 routing.

Allowed buckets: `0`. Blocked buckets: `20`.

## Allowed Buckets

| Strategy | Symbol | Trades | WR | Net R | Exp | PF | Max LS |
| -------- | ------ | ------ | -- | ----- | --- | -- | ------ |

## Blocked Buckets

| Strategy                 | Symbol | Trades | WR    | Net R | Reasons                                                                     |
| ------------------------ | ------ | ------ | ----- | ----- | --------------------------------------------------------------------------- |
| bsi_v3_reactionary_block | AUDUSD | 2      | 100.0 | 2.0   | sample_too_small                                                            |
| bsi_v3_reactionary_block | EURJPY | 2      | 100.0 | 2.0   | sample_too_small                                                            |
| bsi_v3_reactionary_block | EURUSD | 2      | 100.0 | 1.0   | sample_too_small                                                            |
| bsi_v3_reactionary_block | GBPJPY | 2      | 100.0 | 2.0   | sample_too_small                                                            |
| bsi_v3_reactionary_block | GBPUSD | 2      | 100.0 | 1.0   | sample_too_small                                                            |
| bsi_v3_reactionary_block | NZDUSD | 2      | 0.0   | -2.0  | sample_too_small, expectancy_below_threshold, profit_factor_below_threshold |
| bsi_v3_reactionary_block | USDCAD | 2      | 100.0 | 3.0   | sample_too_small                                                            |
| bsi_v3_reactionary_block | USDCHF | 2      | 100.0 | 1.0   | sample_too_small                                                            |
| bsi_v3_reactionary_block | USDJPY | 2      | 100.0 | 2.0   | sample_too_small                                                            |
| bsi_v3_reactionary_block | XAUUSD | 2      | 100.0 | 1.0   | sample_too_small                                                            |
| bsi_v3_spectre           | AUDUSD | 2      | 100.0 | 2.0   | sample_too_small                                                            |
| bsi_v3_spectre           | EURJPY | 2      | 100.0 | 2.0   | sample_too_small                                                            |
| bsi_v3_spectre           | EURUSD | 2      | 100.0 | 1.0   | sample_too_small                                                            |
| bsi_v3_spectre           | GBPJPY | 2      | 100.0 | 2.0   | sample_too_small                                                            |
| bsi_v3_spectre           | GBPUSD | 2      | 100.0 | 1.0   | sample_too_small                                                            |
| bsi_v3_spectre           | NZDUSD | 2      | 0.0   | -2.0  | sample_too_small, expectancy_below_threshold, profit_factor_below_threshold |
| bsi_v3_spectre           | USDCAD | 2      | 100.0 | 3.0   | sample_too_small                                                            |
| bsi_v3_spectre           | USDCHF | 2      | 100.0 | 1.0   | sample_too_small                                                            |
| bsi_v3_spectre           | USDJPY | 2      | 100.0 | 2.0   | sample_too_small                                                            |
| bsi_v3_spectre           | XAUUSD | 2      | 100.0 | 1.0   | sample_too_small                                                            |

Correlation-aware estimate: `RESEARCH_SCAFFOLD_ONLY_NOT_PROMOTION_GRADE; portfolio exposure constraints must be applied after strategy-specific detectors and golden examples are validated`.
