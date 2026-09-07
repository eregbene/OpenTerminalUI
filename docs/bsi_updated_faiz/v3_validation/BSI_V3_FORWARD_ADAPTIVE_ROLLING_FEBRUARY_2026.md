# BSI V3 Forward Adaptive Validation

Label: `rolling_february_2026`

Train: `2026-01-01T00:00:00+00:00` to `2026-02-01T00:00:00+00:00`. Test: `2026-02-01T00:00:00+00:00` to `2026-03-01T00:00:00+00:00`.

Symbols: `EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD`.

Train allowed buckets: `77`

| Metric            | Test Raw Baseline | Test Mentor Managed | Train-Window Adaptive on Test |
| ----------------- | ----------------- | ------------------- | ----------------------------- |
| Trades            | 2457              | 2457                | 2222                          |
| Win Rate          | 34.19             | 69.8                | 70.3                          |
| Net R             | 896.5007          | 939.5007            | 884.1271                      |
| Profit Factor     | 2.2554            | 2.3156              | 2.3914                        |
| Max Losing Streak | 36                | 5                   | 4                             |
| Max DD R          | 35.1301           | 17.0                | 7.0                           |

## 20K Account Estimate

- `0.10%_risk`: profit `$17682.54`, ending balance `$37682.54`
- `0.25%_risk`: profit `$44206.36`, ending balance `$64206.36`
- `0.50%_risk`: profit `$88412.71`, ending balance `$108412.71`

This is forward-split replay math, not compounded and not live-routing proof.
