# BSI V3 Forward Adaptive Validation

Label: `rolling_july_2026_rr1p0`

Train: `2026-01-01T00:00:00+00:00` to `2026-07-01T00:00:00+00:00`. Test: `2026-07-01T00:00:00+00:00` to `2026-08-01T00:00:00+00:00`.

Symbols: `EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD`.

Train allowed buckets: `96`

| Metric            | Test Raw Baseline | Test Mentor Managed | Train-Window Adaptive on Test |
| ----------------- | ----------------- | ------------------- | ----------------------------- |
| Trades            | 3117              | 3117                | 2895                          |
| Win Rate          | 13.76             | 51.72               | 51.47                         |
| Net R             | -721.0851         | -304.5851           | -288.5851                     |
| Profit Factor     | 0.5045            | 0.7907              | 0.787                         |
| Max Losing Streak | 57                | 12                  | 12                            |
| Max DD R          | 734.3111          | 326.4154            | 310.4154                      |

## 20K Account Estimate

- `0.10%_risk`: profit `$-5771.7`, ending balance `$14228.3`
- `0.25%_risk`: profit `$-14429.26`, ending balance `$5570.74`
- `0.50%_risk`: profit `$-28858.51`, ending balance `$-8858.51`

This is forward-split replay math, not compounded and not live-routing proof.
