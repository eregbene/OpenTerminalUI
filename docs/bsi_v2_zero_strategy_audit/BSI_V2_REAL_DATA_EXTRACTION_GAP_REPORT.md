# BSI V2 Real Data Extraction Gap Report

## Fixture Dependency Table

| Field | Used by strategy | Present in golden fixture | Present in real replay | Derivable from raw bars | Fix |
|---|---|---:|---:|---:|---|
| `bsi_v2_fixture["asian"]` | Asian | yes | no | yes | Added raw Asian range/sweep/MSS/FVG extractor. |
| `bsi_v2_fixture["abc"]` | ABC | yes | no | yes | Added raw swing A/B/C + MSS/FVG extractor. |
| `bsi_v2_fixture["abcd"]` | ABCD | yes | no | yes | Added raw two-sided liquidity-purge/retest extractor. |
| `bsi_v2_fixture["under_over"]` | Under/Over | yes | no | yes | Added equal-level touch/fakeout/reclaim extractor. |
| `bsi_v2_fixture["0930"]` | 9:30 | yes | no | only with index/M1 data | Added explicit scope rejection for non-index universe. |
| `bsi_v2_fixture["reactionary"]` | Reactionary | yes | no | yes | Added array1 mitigation then array2 FVG extractor. |
| `bsi_v2_fixture["ob_liquidity"]` | OB Liquidity | yes | no | yes | Added same-order-block fakeout/reclaim extractor. |
| `LiquidityLevel.source` | New York | n/a | `confirmed_swing` | yes | Accepted `confirmed_swing` as real swing liquidity. |

## Remaining Caveats

The new raw extractors are conservative first production extractors. They are not parameter optimization and do not change Order Flow.

9:30 still needs index history and M1 execution candles for proper validation.
