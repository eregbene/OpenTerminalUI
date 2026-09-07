# BSI V3 Forward Adaptive Validation

Label: `jan_march_train_april_test`

Train: `2026-01-01T00:00:00+00:00` to `2026-04-01T00:00:00+00:00`. Test: `2026-04-01T00:00:00+00:00` to `2026-05-01T00:00:00+00:00`.

Symbols: `EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD`.

Train allowed buckets: `90`

| Metric            | Test Raw Baseline | Test Mentor Managed | Train-Window Adaptive on Test |
| ----------------- | ----------------- | ------------------- | ----------------------------- |
| Trades            | 2705              | 2705                | 2586                          |
| Win Rate          | 34.53             | 68.24               | 68.68                         |
| Net R             | 953.0196          | 981.5196            | 976.7005                      |
| Profit Factor     | 2.1545            | 2.1891              | 2.2579                        |
| Max Losing Streak | 47                | 6                   | 6                             |
| Max DD R          | 74.0241           | 38.0241             | 19.3432                       |

## 20K Account Estimate

- `0.10%_risk`: profit `$19534.01`, ending balance `$39534.01`
- `0.25%_risk`: profit `$48835.03`, ending balance `$68835.02`
- `0.50%_risk`: profit `$97670.05`, ending balance `$117670.05`

This is forward-split replay math, not compounded and not live-routing proof.
