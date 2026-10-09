# BSI V3 Forward Adaptive Validation

Label: `rolling_august_2026_rr1p0`

Train: `2026-01-01T00:00:00+00:00` to `2026-08-01T00:00:00+00:00`. Test: `2026-08-01T00:00:00+00:00` to `2026-09-01T00:00:00+00:00`.

Symbols: `EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD`.

Train allowed buckets: `94`

| Metric            | Test Raw Baseline | Test Mentor Managed | Train-Window Adaptive on Test |
| ----------------- | ----------------- | ------------------- | ----------------------------- |
| Trades            | 3393              | 3393                | 3139                          |
| Win Rate          | 23.87             | 58.44               | 58.49                         |
| Net R             | 137.1901          | 372.1901            | 350.9856                      |
| Profit Factor     | 1.1028            | 1.279               | 1.2843                        |
| Max Losing Streak | 48                | 9                   | 9                             |
| Max DD R          | 200.9262          | 143.9262            | 143.9262                      |

## 20K Account Estimate

- `0.10%_risk`: profit `$7019.71`, ending balance `$27019.71`
- `0.25%_risk`: profit `$17549.28`, ending balance `$37549.28`
- `0.50%_risk`: profit `$35098.56`, ending balance `$55098.56`

This is forward-split replay math, not compounded and not live-routing proof.
