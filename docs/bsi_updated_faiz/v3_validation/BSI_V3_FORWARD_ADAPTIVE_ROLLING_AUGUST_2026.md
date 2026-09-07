# BSI V3 Forward Adaptive Validation

Label: `rolling_august_2026`

Train: `2026-01-01T00:00:00+00:00` to `2026-08-01T00:00:00+00:00`. Test: `2026-08-01T00:00:00+00:00` to `2026-09-01T00:00:00+00:00`.

Symbols: `EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD`.

Train allowed buckets: `92`

| Metric            | Test Raw Baseline | Test Mentor Managed | Train-Window Adaptive on Test |
| ----------------- | ----------------- | ------------------- | ----------------------------- |
| Trades            | 3391              | 3391                | 3137                          |
| Win Rate          | 23.89             | 58.54               | 58.59                         |
| Net R             | 141.2113          | 377.2113            | 356.0068                      |
| Profit Factor     | 1.1062            | 1.2836              | 1.2893                        |
| Max Losing Streak | 51                | 9                   | 9                             |
| Max DD R          | 196.905           | 138.905             | 138.905                       |

## 20K Account Estimate

- `0.10%_risk`: profit `$7120.14`, ending balance `$27120.14`
- `0.25%_risk`: profit `$17800.34`, ending balance `$37800.34`
- `0.50%_risk`: profit `$35600.68`, ending balance `$55600.68`

This is forward-split replay math, not compounded and not live-routing proof.
