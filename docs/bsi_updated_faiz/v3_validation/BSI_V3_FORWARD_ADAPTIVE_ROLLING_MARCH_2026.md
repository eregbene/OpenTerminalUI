# BSI V3 Forward Adaptive Validation

Label: `rolling_march_2026`

Train: `2026-01-01T00:00:00+00:00` to `2026-03-01T00:00:00+00:00`. Test: `2026-03-01T00:00:00+00:00` to `2026-04-01T00:00:00+00:00`.

Symbols: `EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD`.

Train allowed buckets: `86`

| Metric            | Test Raw Baseline | Test Mentor Managed | Train-Window Adaptive on Test |
| ----------------- | ----------------- | ------------------- | ----------------------------- |
| Trades            | 2672              | 2672                | 2467                          |
| Win Rate          | 39.33             | 73.58               | 73.94                         |
| Net R             | 1366.4543         | 1320.4543           | 1228.6157                     |
| Profit Factor     | 3.0081            | 2.9405              | 2.9701                        |
| Max Losing Streak | 15                | 6                   | 6                             |
| Max DD R          | 18.217            | 7.0893              | 7.0893                        |

## 20K Account Estimate

- `0.10%_risk`: profit `$24572.31`, ending balance `$44572.31`
- `0.25%_risk`: profit `$61430.79`, ending balance `$81430.79`
- `0.50%_risk`: profit `$122861.57`, ending balance `$142861.57`

This is forward-split replay math, not compounded and not live-routing proof.
