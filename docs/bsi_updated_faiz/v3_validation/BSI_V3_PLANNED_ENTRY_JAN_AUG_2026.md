# BSI V3 Planned POI Confirmation Entry Validation

Window: `2026-01-01T00:00:00+00:00` to `2026-09-01T00:00:00+00:00`

Symbols: `EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURJPY, GBPJPY, XAUUSD`

Old baseline reference: `D:\Devops\trading\OpenTerminalUI\data\research\bsi_v3_rolling_walk_forward_jan_august_2026_rr1p25.json`

Course material used: `125/125` videos, `125/125` transcripts, `125/125` contact sheets.

## Extracted Mentor Rules

- Draw HTF liquidity/internal liquidity, POIs, FVG/OB/range levels first.
- Do not chase price; wait for price to return into the POI.
- Confirm on M1/M5 with displacement plus structure/FVG proxy.
- Enter only inside valid session/killzone where the model requires it.
- Move risk-free after structure breaks in favor; partial around structure/targets.
- Use consistent risk; reduce after losing streak; step up only after profit cushion.

## Comparison

| Model                                     | Trades | Win Rate | Net R     | Expectancy | PF     | Max LS | Max DD R |
| ----------------------------------------- | ------ | -------- | --------- | ---------- | ------ | ------ | -------- |
| instant_entry_same_window                 | 24033  | 64.85    | 6328.2157 | 0.2633     | 1.7805 | 9      | 76.6369  |
| planned_entry                             | 10621  | 70.04    | 4544.5328 | 0.4279     | 2.5305 | 8      | 13.2343  |
| planned_entry_plus_adaptive_bucket_filter | 10285  | 70.38    | 4476.7702 | 0.4353     | 2.5769 | 6      | 11.5     |
| planned_entry_adaptive_skipped            | 336    | 59.52    | 67.7626   | 0.2017     | 1.5198 | 8      | 22.9982  |

## Strategy Detail

| Strategy                        | Instant Trades | Instant Net R | Planned Trades | Planned Net R | Expired/Unconfirmed |
| ------------------------------- | -------------- | ------------- | -------------- | ------------- | ------------------- |
| bsi_v3_order_flow               | 1823           | 525.2819      | 831            | 345.075       | 992                 |
| bsi_v3_smt_divergence           | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_abc                      | 133            | 90.5          | 51             | 46.5          | 82                  |
| bsi_v3_abcd                     | 73             | 42.5          | 32             | 26.0          | 41                  |
| bsi_v3_asian_v2                 | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_0930                     | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_reactionary_block        | 3643           | 1012.867      | 1577           | 668.6036      | 2066                |
| bsi_v3_ict_silver_bullet        | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_silver_bullet_with_bias  | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_4h_order_block           | 73             | 42.5          | 32             | 26.0          | 41                  |
| bsi_v3_mmxm                     | 1797           | 628.8072      | 817            | 481.1706      | 980                 |
| bsi_v3_mmxm_second_distribution | 2467           | 12.0789       | 1322           | 257.9786      | 1145                |
| bsi_v3_holy_grail               | 1799           | 603.5279      | 813            | 473.883       | 986                 |
| bsi_v3_juggernaut               | 4              | 2.5           | 1              | 0.5           | 3                   |
| bsi_v3_spectre                  | 3644           | 1014.367      | 1578           | 670.1036      | 2066                |
| bsi_v3_monday_range             | 129            | 43.5804       | 60             | 23.4544       | 69                  |
| bsi_v3_weaver                   | 1043           | 362.827       | 456            | 206.1403      | 587                 |
| bsi_v3_standard_deviation_po3   | 3590           | 1185.2627     | 1527           | 896.049       | 2063                |
| bsi_v3_ar50                     | 2684           | 621.9753      | 1171           | 424.8992      | 1513                |
| bsi_v3_ifvg_po3                 | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_turtle_soups_ranges      | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_yin_yang                 | 16             | 11.0          | 5              | 1.5           | 11                  |
| bsi_v3_4h_candle_ranges         | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_smt_session_hl           | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_1h_candle_ranges         | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_enigma_range             | 1115           | 128.6404      | 348            | -3.3245       | 767                 |

## Decision

This validates the planned-entry idea as research. The live MT5 runtime still needs the persistent pending-setup queue before this becomes the demo execution model.
