# BSI V3 Forward Adaptive Validation

Label: `jan_april_train_may_august_test`

Train: `2026-01-01T00:00:00+00:00` to `2026-05-01T00:00:00+00:00`. Test: `2026-05-01T00:00:00+00:00` to `2026-09-01T00:00:00+00:00`.

Symbols: `EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD`.

Train allowed buckets: `91`

| Metric            | Test Raw Baseline | Test Mentor Managed | Train-Window Adaptive on Test |
| ----------------- | ----------------- | ------------------- | ----------------------------- |
| Trades            | 11793             | 11793               | 11054                         |
| Win Rate          | 23.62             | 58.98               | 59.21                         |
| Net R             | 337.314           | 1238.814            | 1232.7618                     |
| Profit Factor     | 1.0726            | 1.2667              | 1.285                         |
| Max Losing Streak | 58                | 9                   | 9                             |
| Max DD R          | 256.6229          | 108.1796            | 108.1796                      |

## 20K Account Estimate

- `0.10%_risk`: profit `$24655.24`, ending balance `$44655.24`
- `0.25%_risk`: profit `$61638.09`, ending balance `$81638.09`
- `0.50%_risk`: profit `$123276.18`, ending balance `$143276.18`

This is forward-split replay math, not compounded and not live-routing proof.
