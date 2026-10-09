# BSI V3 Forward Adaptive Validation

Label: `rolling_june_2026_rr1p25`

Train: `2026-01-01T00:00:00+00:00` to `2026-06-01T00:00:00+00:00`. Test: `2026-06-01T00:00:00+00:00` to `2026-07-01T00:00:00+00:00`.

Symbols: `EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD`.

Train allowed buckets: `94`

| Metric            | Test Raw Baseline | Test Mentor Managed | Train-Window Adaptive on Test |
| ----------------- | ----------------- | ------------------- | ----------------------------- |
| Trades            | 2700              | 2700                | 2557                          |
| Win Rate          | 24.07             | 61.3                | 61.6                          |
| Net R             | 172.1897          | 396.1897            | 401.1897                      |
| Profit Factor     | 1.1704            | 1.3922              | 1.4235                        |
| Max Losing Streak | 35                | 7                   | 7                             |
| Max DD R          | 55.5953           | 13.0                | 13.0                          |

## 20K Account Estimate

- `0.10%_risk`: profit `$8023.79`, ending balance `$28023.79`
- `0.25%_risk`: profit `$20059.49`, ending balance `$40059.49`
- `0.50%_risk`: profit `$40118.97`, ending balance `$60118.97`

This is forward-split replay math, not compounded and not live-routing proof.
