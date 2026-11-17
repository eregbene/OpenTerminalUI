# Round 9 result (2026-11-07): stronger ATR thresholds + 12 h exits for Flow ML (pre-registered: PREREG_ATR.md)
Script `r9_atr_levels.py`, numbers `r9_results.json`. Baseline reproduced (LIVE5 ATR>1.2: 4.32 bp, 2,028 trades).

| claim | bp/trade (1.2 -> th) | DISC/VAL/LOCK better | daily Sharpe (1.2 -> th) | verdict |
|---|---|---|---|---|
| LIVE5 1.4 | 4.32 -> 5.40 | yes | 1.07 -> 0.93 | FAIL (Sharpe) |
| LIVE5 1.7 | 4.32 -> 8.30 | no (VAL 2.24 < 2.71) | 1.07 -> 0.69 | FAIL |
| OTHER20 1.4 | 1.80 -> 2.69 | yes | 0.59 -> 0.66 | **PASS** (t 2.28, BH ok) |
| OTHER20 1.7 | 1.80 -> 4.54 | no (LOCK 1.65 < 2.12) | 0.59 -> 0.62 | FAIL |

Exits: E1 hold 12 h and E2 winners to 12 h FAIL in every group/threshold (best +1.9 bp, t < 1.5). 6 h stays.
Reading: higher thresholds raise bp per trade but cut trades, so total profit and Sharpe fall for the live 5 -> keep 1.2 there.
For the wider 25-pair book 1.4 is better (ALL25 1-lot max DD -$27.8k at 1.2 vs -$12.1k at 1.4, similar profit).
