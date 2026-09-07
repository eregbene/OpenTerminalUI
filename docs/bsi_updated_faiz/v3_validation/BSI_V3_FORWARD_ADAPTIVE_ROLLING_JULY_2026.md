# BSI V3 Forward Adaptive Validation

Label: `rolling_july_2026`

Train: `2026-01-01T00:00:00+00:00` to `2026-07-01T00:00:00+00:00`. Test: `2026-07-01T00:00:00+00:00` to `2026-08-01T00:00:00+00:00`.

Symbols: `EURUSD,GBPUSD,USDJPY,AUDUSD,USDCAD,USDCHF,NZDUSD,EURJPY,GBPJPY,XAUUSD`.

Train allowed buckets: `96`

| Metric            | Test Raw Baseline | Test Mentor Managed | Train-Window Adaptive on Test |
| ----------------- | ----------------- | ------------------- | ----------------------------- |
| Trades            | 3117              | 3117                | 2895                          |
| Win Rate          | 13.96             | 52.01               | 51.78                         |
| Net R             | -700.907          | -285.407            | -269.407                      |
| Profit Factor     | 0.5151            | 0.8026              | 0.7998                        |
| Max Losing Streak | 57                | 12                  | 12                            |
| Max DD R          | 723.133           | 312.7373            | 296.7373                      |

## 20K Account Estimate

- `0.10%_risk`: profit `$-5388.14`, ending balance `$14611.86`
- `0.25%_risk`: profit `$-13470.35`, ending balance `$6529.65`
- `0.50%_risk`: profit `$-26940.7`, ending balance `$-6940.7`

This is forward-split replay math, not compounded and not live-routing proof.
