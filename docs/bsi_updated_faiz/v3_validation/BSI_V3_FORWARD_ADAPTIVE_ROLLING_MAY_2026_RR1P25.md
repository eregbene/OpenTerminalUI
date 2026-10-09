# BSI V3 Forward Adaptive Validation

Label: `rolling_may_2026_rr1p25`

Train: `2026-01-01T00:00:00+00:00` to `2026-05-01T00:00:00+00:00`. Test: `2026-05-01T00:00:00+00:00` to `2026-06-01T00:00:00+00:00`.

Symbols: `EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD`.

Train allowed buckets: `91`

| Metric            | Test Raw Baseline | Test Mentor Managed | Train-Window Adaptive on Test |
| ----------------- | ----------------- | ------------------- | ----------------------------- |
| Trades            | 2494              | 2494                | 2343                          |
| Win Rate          | 32.84             | 66.32               | 66.97                         |
| Net R             | 747.2328          | 786.2328            | 779.5471                      |
| Profit Factor     | 1.9161            | 1.9639              | 2.0379                        |
| Max Losing Streak | 23                | 6                   | 6                             |
| Max DD R          | 47.7554           | 15.2554             | 9.815                         |

## 20K Account Estimate

- `0.10%_risk`: profit `$15590.94`, ending balance `$35590.94`
- `0.25%_risk`: profit `$38977.36`, ending balance `$58977.36`
- `0.50%_risk`: profit `$77954.71`, ending balance `$97954.71`

This is forward-split replay math, not compounded and not live-routing proof.
