# BSI V2 V1/V2 Comparison Report

Date: 2026-09-03

## Verdict

Static methodology comparison is complete at code/test level. V1 historical metrics are available. V2 six-month performance and frequency comparison remains blocked pending a valid full V2 corpus.

## V1

- Production BSI remains registered as the existing `bsi` family.
- V1 dispatch was not replaced.
- Existing BSI regression remains clean in the selected checkpoint.

## V2

- V2 exists as isolated research code.
- V2 strategy id: `bsi_v2_research`.
- V2 version: `BSI_BASELINE_V2_AUDIOVISUAL`.
- V2 is not registered in production `EVALUATORS`.
- All nine V2 subtypes are callable through the isolated research dispatcher.

## All Nine V2 Subtypes

- bsi_order_flow
- bsi_asian
- bsi_new_york
- bsi_abc
- bsi_under_over
- bsi_0930
- bsi_reactionary
- bsi_abcd
- bsi_ob_liquidity

## Code-Level Differences

- V1 remains production-facing.
- V2 adds mentor-specific audiovisual evidence, lifecycle, freshness, and strategy subtype identity.
- V2 uses durable lifecycle research state when explicitly supplied.
- V2 keeps account execution identity separate from methodology opportunity identity.

## Performance/Frequency Status

V1 resolved subtype counts and expectancy:

| Strategy | N | Wins | Losses | Expectancy R | Net R | Profit Factor |
|---|---:|---:|---:|---:|---:|---:|
| bsi_0930 | 3 | 0 | 3 | -1.0000 | -3.0000 | 0.0000 |
| bsi_abc | 4 | 1 | 3 | 0.0947 | 0.3789 | 1.1263 |
| bsi_abcd | 178 | 61 | 117 | 0.0281 | 5.0000 | 1.0427 |
| bsi_asian | 14 | 6 | 8 | 2.7954 | 39.1358 | 5.8920 |
| bsi_new_york | 3266 | 1288 | 1978 | 0.1831 | 598.0000 | 1.3023 |
| bsi_order_flow | 28 | 8 | 20 | 0.0518 | 1.4505 | 1.0725 |
| bsi_under_over | 502 | 303 | 199 | 0.8178 | 410.5386 | 3.0630 |

V1 frequency over `2026-03-02T03:45:00Z` to `2026-08-28T23:45:00Z`:

| Strategy | N | /day | /week | /month |
|---|---:|---:|---:|---:|
| bsi_0930 | 3 | 0.0330 | 0.2310 | 1.0042 |
| bsi_abc | 4 | 0.0274 | 0.1916 | 0.8330 |
| bsi_abcd | 178 | 0.9988 | 6.9918 | 30.4019 |
| bsi_asian | 14 | 0.0826 | 0.5779 | 2.5126 |
| bsi_new_york | 3266 | 18.1613 | 127.1288 | 552.7834 |
| bsi_order_flow | 28 | 0.1626 | 1.1384 | 4.9498 |
| bsi_under_over | 502 | 2.8590 | 20.0133 | 87.0221 |

All BSI V1 combined:

- N: 3,995
- trades/day: 22.2150
- trades/week: 155.5051
- trades/month: 676.1695

Best-symbol concentration by net R:

- AUDUSD: 947 trades, +755.2952R
- EURUSD: 789 trades, +311.9062R
- USDJPY: 347 trades, +151.3123R
- GBPUSD: 406 trades, +59.2334R
- USDCHF: 453 trades, +46.9210R

Direction concentration:

- SHORT: 2,187
- LONG: 1,808

V2 performance/frequency:

- Real PIT sample: 12 contexts, 0 executable entries.
- Full corpus: not generated.
- Therefore no statistically honest V2 performance/frequency comparison exists yet.

## Demo Gate

Not passed.
