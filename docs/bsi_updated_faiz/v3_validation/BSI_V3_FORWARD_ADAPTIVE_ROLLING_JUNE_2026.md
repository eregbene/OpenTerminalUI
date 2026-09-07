# BSI V3 Forward Adaptive Validation

Label: `rolling_june_2026`

Train: `2026-01-01T00:00:00+00:00` to `2026-06-01T00:00:00+00:00`. Test: `2026-06-01T00:00:00+00:00` to `2026-07-01T00:00:00+00:00`.

Symbols: `EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD`.

Train allowed buckets: `94`

| Metric            | Test Raw Baseline | Test Mentor Managed | Train-Window Adaptive on Test |
| ----------------- | ----------------- | ------------------- | ----------------------------- |
| Trades            | 2697              | 2697                | 2554                          |
| Win Rate          | 24.03             | 61.33               | 61.63                         |
| Net R             | 172.7222          | 397.2222            | 402.2222                      |
| Profit Factor     | 1.1711            | 1.3934              | 1.4249                        |
| Max Losing Streak | 35                | 7                   | 7                             |
| Max DD R          | 55.5953           | 13.0                | 13.0                          |

## 20K Account Estimate

- `0.10%_risk`: profit `$8044.44`, ending balance `$28044.44`
- `0.25%_risk`: profit `$20111.11`, ending balance `$40111.11`
- `0.50%_risk`: profit `$40222.22`, ending balance `$60222.22`

This is forward-split replay math, not compounded and not live-routing proof.
