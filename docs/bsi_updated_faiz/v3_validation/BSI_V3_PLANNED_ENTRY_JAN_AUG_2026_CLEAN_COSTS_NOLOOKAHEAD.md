# BSI V3 Planned POI Confirmation Entry Validation

Window: `2026-01-01T00:00:00+00:00` to `2026-08-12T00:00:00+00:00`

Symbols: `EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, EURJPY, GBPJPY, XAUUSD`

Old baseline reference: `D:\Devops\trading\OpenTerminalUI\data\research\bsi_v3_rolling_walk_forward_jan_august_2026_rr1p25.json`

Course material used: `125/125` videos, `125/125` transcripts, `125/125` contact sheets.

> **Read first (2026-09-25).** Clean single-source data (MT5 MetaQuotes-Demo; H1/H4/D1 built from M5),
> IC Markets costs charged, same-bar stop+target = loss, HTF bias only from closed bars, and planned entries
> filled at market on the confirmation bar close exactly like live (`_opportunity_from_plan`).
> **Result: the live planned-entry method is net negative, -0.074R/trade, PF 0.85, over 7,090 trades; every strategy is negative.**
> - `instant_entry_same_window` is NOT tradeable evidence: it assumes a fill at the FVG midpoint without checking price returns there.
> - `planned_entry_plus_adaptive_bucket_filter` is in-sample (buckets picked on these same trades), not an edge.
> - The auto-generated "Decision" line below is superseded by this note.

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
| instant_entry_same_window                 | 21501  | 70.02    | 6087.3492 | 0.2831     | 1.8664 | 8      | 29.9854  |
| planned_entry                             | 7090   | 50.03    | -525.502  | -0.0741    | 0.8519 | 11     | 528.0929 |
| planned_entry_plus_adaptive_bucket_filter | 1484   | 57.55    | 182.5672  | 0.123      | 1.2999 | 6      | 11.2453  |
| planned_entry_adaptive_skipped            | 5606   | 48.04    | -708.0692 | -0.1263    | 0.7591 | 11     | 709.1957 |

## Strategy Detail

| Strategy                        | Instant Trades | Instant Net R | Planned Trades | Planned Net R | Expired/Unconfirmed |
| ------------------------------- | -------------- | ------------- | -------------- | ------------- | ------------------- |
| bsi_v3_order_flow               | 1433           | 304.1198      | 499            | -29.7413      | 934                 |
| bsi_v3_smt_divergence           | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_abc                      | 177            | 90.1131       | 61             | -13.5977      | 116                 |
| bsi_v3_abcd                     | 49             | 19.3123       | 17             | 0.7182        | 32                  |
| bsi_v3_asian_v2                 | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_0930                     | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_reactionary_block        | 3156           | 919.6043      | 1110           | -71.6557      | 2046                |
| bsi_v3_ict_silver_bullet        | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_silver_bullet_with_bias  | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_4h_order_block           | 49             | 19.3123       | 17             | 0.7182        | 32                  |
| bsi_v3_mmxm                     | 1589           | 289.8461      | 576            | -26.4782      | 1013                |
| bsi_v3_mmxm_second_distribution | 2748           | 1134.2069     | 725            | -32.314       | 2023                |
| bsi_v3_holy_grail               | 1409           | 194.827       | 498            | -46.669       | 911                 |
| bsi_v3_juggernaut               | 2              | 0.1914        | 0              | 0.0           | 2                   |
| bsi_v3_spectre                  | 3158           | 921.3787      | 1112           | -72.1755      | 2046                |
| bsi_v3_monday_range             | 120            | 48.0771       | 47             | -2.8306       | 73                  |
| bsi_v3_weaver                   | 920            | 350.8106      | 326            | -12.6839      | 594                 |
| bsi_v3_standard_deviation_po3   | 3157           | 621.0627      | 1041           | -44.7693      | 2116                |
| bsi_v3_ar50                     | 2228           | 596.2542      | 796            | -154.9522     | 1432                |
| bsi_v3_ifvg_po3                 | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_turtle_soups_ranges      | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_yin_yang                 | 22             | 16.4208       | 8              | -1.125        | 14                  |
| bsi_v3_4h_candle_ranges         | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_smt_session_hl           | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_1h_candle_ranges         | 0              | 0.0           | 0              | 0.0           | 0                   |
| bsi_v3_enigma_range             | 1284           | 561.8119      | 257            | -17.946       | 1028                |

## Decision

This validates the planned-entry idea as research. The live MT5 runtime still needs the persistent pending-setup queue before this becomes the demo execution model.
