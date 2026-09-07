# BSI V3 Forward Adaptive Validation

Label: `rolling_may_2026`

Train: `2026-01-01T00:00:00+00:00` to `2026-05-01T00:00:00+00:00`. Test: `2026-05-01T00:00:00+00:00` to `2026-06-01T00:00:00+00:00`.

Symbols: `EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD`.

Train allowed buckets: `91`

| Metric            | Test Raw Baseline | Test Mentor Managed | Train-Window Adaptive on Test |
| ----------------- | ----------------- | ------------------- | ----------------------------- |
| Trades            | 2499              | 2499                | 2344                          |
| Win Rate          | 32.81             | 66.27               | 66.94                         |
| Net R             | 746.2328          | 785.2328            | 777.5471                      |
| Profit Factor     | 1.9115            | 1.9592              | 2.0338                        |
| Max Losing Streak | 23                | 6                   | 6                             |
| Max DD R          | 47.7554           | 15.2554             | 9.815                         |

## 20K Account Estimate

- `0.10%_risk`: profit `$15550.94`, ending balance `$35550.94`
- `0.25%_risk`: profit `$38877.36`, ending balance `$58877.36`
- `0.50%_risk`: profit `$77754.71`, ending balance `$97754.71`

This is forward-split replay math, not compounded and not live-routing proof.
