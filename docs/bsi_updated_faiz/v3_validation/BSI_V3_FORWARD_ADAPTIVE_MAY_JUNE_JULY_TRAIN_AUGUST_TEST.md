# BSI V3 Forward Adaptive Validation

Label: `may_june_july_train_august_test`

Train: `2026-05-01T00:00:00+00:00` to `2026-08-01T00:00:00+00:00`. Test: `2026-08-01T00:00:00+00:00` to `2026-09-01T00:00:00+00:00`.

Symbols: `EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD`.

Train allowed buckets: `61`

| Metric            | Test Raw Baseline | Test Mentor Managed | Train-Window Adaptive on Test |
| ----------------- | ----------------- | ------------------- | ----------------------------- |
| Trades            | 3392              | 3392                | 2312                          |
| Win Rate          | 23.88             | 58.52               | 59.13                         |
| Net R             | 140.2113          | 376.2113            | 319.3067                      |
| Profit Factor     | 1.1053            | 1.2826              | 1.3576                        |
| Max Losing Streak | 51                | 9                   | 9                             |
| Max DD R          | 197.905           | 139.905             | 86.0272                       |

## 20K Account Estimate

- `0.10%_risk`: profit `$6386.13`, ending balance `$26386.13`
- `0.25%_risk`: profit `$15965.33`, ending balance `$35965.33`
- `0.50%_risk`: profit `$31930.67`, ending balance `$51930.67`

This is forward-split replay math, not compounded and not live-routing proof.
