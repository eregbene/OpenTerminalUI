# Round 3: pre-registration (written 2026-11-06, before any Round 3 result)

## Data
- Panel: `r3_panel.pkl`, 25 FXCM pairs, H1, 2012-01 to 2026-10, built by `r3_build.py`.
- Base: `disc_data.pkl`, the round-2 discovery panel.
- Decision time: the H1 bar close. Every feature is causal; a truncation test on EURUSD found no column that uses future bars.

## Splits (fixed)

| Split | Years | Use |
|---|---|---|
| DISC | 2012–2018 | Discovery, the only data used to choose anything |
| VAL | 2019–2022 | First confirmation |
| LOCK | 2023-01 to 2026-10 | Final confirmation |

Honest caveat: rounds 1–2 already produced walk-forward results on 2016–2026 for generic ML models and for currency strength. The rule tests here (areas A–D, I) have never been run on any split. VAL and LOCK are therefore new for them, but not "never seen" in the broadest sense. The only truly untouched data is forward paper data.

## Costs
- The real FXCM spread at the entry hour and at the exit hour: half at entry, half at exit, already in the panel. Rollover-hour spreads are therefore the real, wide ones.
- Plus 0.3 bp slippage per round trip.
- Plus swap / rollover financing of 0.5 bp per 24h held, prorated: 6h 0.125, 12h 0.25, 24h 0.5, 48h 1.0, bracket trades 0.5.

## Outcomes per rule (all net)
- Fixed holds of 6, 12, 24 and 48h, in bp.
- Bracket trade b12: stop 1 ATR24, target 2 ATR24, 24-bar time exit; a bar that touches both counts as the stop. Measured in R.
- One open trade per pair per rule: a new signal is ignored until the previous trade has ended.

## Statistics
- Week-clustered t (sum of each week's trades), one-sided p, 95% CI of the mean.
- Benjamini–Hochberg over ALL rule × direction × outcome tests on DISC, at q = 0.05.

## Grades
- **PROVEN**:
  - DISC BH-significant and mean > 0;
  - VAL p < 0.05 and mean > 0;
  - LOCK p < 0.05 and mean > 0;
  - positive in ≥ 60% of all years;
  - positive in ≥ 60% of the pairs it trades.
- **PROMISING**: DISC BH-significant and VAL passes, but LOCK has mean > 0 with p ≥ 0.05. Or DISC is significant at nominal p < 0.01 only, but VAL and LOCK are both positive with p < 0.10.
- **FAILED**: everything else.

## Rule thresholds
Fixed round numbers, written in `r3_tests.py` before it was run. The one data-driven cut (the week-gap 80th percentile) comes from DISC only.

## ML / scoring / portfolio (areas E, G, H)
- Train on DISC, evaluate VAL. Then refit on DISC+VAL and evaluate LOCK.
- Fixed hyper-parameters, no tuning.
- Trade rule: top 2% |prediction|, threshold taken from the training predictions, net 12h.
- Baseline: the candles-only feature set (round-1 OWN).

## Existing 9,277 trades (area F)
- Filters are chosen on trades before 2019 only, at most 3: the best single conditions with DISC t > 2.
- They are then tested on 2019–2022 and 2023–2026 trades.
- Measured by expectancy, profit factor, drawdown and net R, not win rate.

## Corrections log (recorded before re-running; no results were looked at for these items)
- 2026-11-06: `ret_24` is already volatility-normalised in the panel. Dividing by volatility again meant rule C04 never fired (0 trades), and neither did the two "24h move > 1.5 sd" trade conditions. Fix: use `ret_24` directly.
- 2026-11-06: `compress_H1` (a range ratio) and `discount_H4` (a fib position, 0–1) are continuous, not 0/1 flags. Fixed cuts were set: compressed = `compress_H1 < 1.0`, correct side = `discount_H4 < 0.5`.
- The ML feature `z24` was the same mis-scaled value, but only as a monotone rescaling, which tree models ignore. Results are unchanged in kind and the run is left as is.
