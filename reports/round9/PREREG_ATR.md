# Round 9 pre-registration: stronger ATR filter + exits for Flow ML (written 2026-11-07, BEFORE running)

Data: `reports/round8/r8h_5seeds_trades_GLOBAL.csv` (Flow ML 5-seed walk-forward 2016-26, net of real FXCM spreads + 0.3 bp,
late hours 20-00 UTC removed). ATR6/48 from FXCM H1 bars, using only bars closed at the decision.
Periods: DISC 2016-18, VAL 2019-22, LOCK 2023-26. Groups: LIVE5 (EURUSD USDJPY USDCAD EURJPY CADJPY) and OTHER20 (all other pairs).

Disclosure: before writing this I already saw ATR-level means for my hand-picked TOP10 2016-25 (1.2-1.4 +2.4, 1.4-1.7 +3.3,
>1.7 +9.2 bp) and for 2026. TOP10 is therefore in-sample and is reported only, never used to pass.

## H1/H2: threshold 1.4 / 1.7 better than 1.2 (4 claims: 2 thresholds x LIVE5, OTHER20)
PASS only if ALL hold: (a) mean bp above the 1.2 book in DISC, VAL and LOCK; (b) week-clustered t >= 2 over 2016-26;
(c) daily Sharpe >= the 1.2 book; (d) LOCK mean > 0; (e) survives Benjamini-Hochberg q=0.05 over the 4 claims (one-sided p of t).

## H3/H4: improving the winners (exits), on trades with ATR > 1.2, groups LIVE5 and OTHER20
- E1: hold 12 h instead of 6 h.  - E2: only trades in profit at 6 h are held to 12 h (let winners run).
Measured as the extra bp from the same entries with exact quotes (long exits at bid, short at ask = bid + spread). Swap not
included (reported: share crossing 21 UTC). PASS: extra bp > 0 in DISC, VAL and LOCK AND week-clustered t >= 2, in BOTH groups.

## Reported, not tested: $ at 1 / 5 / 7 lots, guard cap max 2 legs per currency side, 10 y and 2026.
