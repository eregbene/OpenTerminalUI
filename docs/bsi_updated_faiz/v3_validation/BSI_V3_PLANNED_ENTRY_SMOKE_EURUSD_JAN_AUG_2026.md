# BSI V3 Planned POI Confirmation Entry Validation

Window: `2026-01-01T00:00:00+00:00` to `2026-09-01T00:00:00+00:00`

Symbols: `EURUSD`

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

| Model                                     | Trades | Win Rate | Net R    | Expectancy | PF     | Max LS | Max DD R |
| ----------------------------------------- | ------ | -------- | -------- | ---------- | ------ | ------ | -------- |
| instant_entry_same_window                 | 2372   | 60.08    | 371.2802 | 0.1565     | 1.4075 | 9      | 51.9126  |
| planned_entry                             | 1107   | 66.76    | 357.0813 | 0.3226     | 2.0424 | 8      | 7.0      |
| planned_entry_plus_adaptive_bucket_filter | 1054   | 67.36    | 347.2164 | 0.3294     | 2.0766 | 5      | 7.0      |
| planned_entry_adaptive_skipped            | 53     | 54.72    | 9.8649   | 0.1861     | 1.4923 | 4      | 4.6351   |

## Strategy Detail

| Strategy                        | Instant Trades | Instant Net R | Planned Trades | Planned Net R | Expired/Unconfirmed           |
| ------------------------------- | -------------- | ------------- | -------------- | ------------- | ----------------------------- |
| bsi_v3_order_flow               | 184            | 31.0736       | 88             | 30.7554       | 96                            |
| bsi_v3_smt_divergence           | 0              |               | 0              |               | NO_STRATEGY_SPECIFIC_DETECTOR |
| bsi_v3_abc                      | 16             | 0.0           | 6              | 4.5           | 10                            |
| bsi_v3_abcd                     | 14             | 0.5           | 6              | 4.5           | 8                             |
| bsi_v3_asian_v2                 | 0              |               | 0              |               | NO_STRATEGY_SPECIFIC_DETECTOR |
| bsi_v3_0930                     | 0              |               | 0              |               | NO_STRATEGY_SPECIFIC_DETECTOR |
| bsi_v3_reactionary_block        | 367            | 71.4657       | 159            | 49.9135       | 208                           |
| bsi_v3_ict_silver_bullet        | 0              |               | 0              |               | NO_STRATEGY_SPECIFIC_DETECTOR |
| bsi_v3_silver_bullet_with_bias  | 0              |               | 0              |               | NO_STRATEGY_SPECIFIC_DETECTOR |
| bsi_v3_4h_order_block           | 14             | 0.5           | 6              | 4.5           | 8                             |
| bsi_v3_mmxm                     | 0              |               | 0              |               | NO_STRATEGY_SPECIFIC_DETECTOR |
| bsi_v3_mmxm_second_distribution | 334            | -43.9126      | 183            | 18.5454       | 151                           |
| bsi_v3_holy_grail               | 184            | 45.1499       | 96             | 52.6926       | 88                            |
| bsi_v3_juggernaut               | 0              |               | 0              |               | NO_STRATEGY_SPECIFIC_DETECTOR |
| bsi_v3_spectre                  | 367            | 71.4657       | 159            | 49.9135       | 208                           |
| bsi_v3_monday_range             | 66             | 10.8527       | 31             | 8.9544        | 35                            |
| bsi_v3_weaver                   | 104            | 29.1027       | 50             | 12.3845       | 54                            |
| bsi_v3_standard_deviation_po3   | 367            | 101.4575      | 169            | 89.6436       | 198                           |
| bsi_v3_ar50                     | 254            | 46.4657       | 119            | 34.4135       | 135                           |
| bsi_v3_ifvg_po3                 | 0              |               | 0              |               | NO_STRATEGY_SPECIFIC_DETECTOR |
| bsi_v3_turtle_soups_ranges      | 0              |               | 0              |               | NO_STRATEGY_SPECIFIC_DETECTOR |
| bsi_v3_yin_yang                 | 0              | 0.0           | 0              | 0.0           | 0                             |
| bsi_v3_4h_candle_ranges         | 0              | 0.0           | 0              | 0.0           | 0                             |
| bsi_v3_smt_session_hl           | 0              |               | 0              |               | NO_STRATEGY_SPECIFIC_DETECTOR |
| bsi_v3_1h_candle_ranges         | 0              |               | 0              |               | NO_STRATEGY_SPECIFIC_DETECTOR |
| bsi_v3_enigma_range             | 101            | 7.1593        | 35             | -3.6351       | 66                            |

## Decision

This validates the planned-entry idea as research. The live MT5 runtime still needs the persistent pending-setup queue before this becomes the demo execution model.
