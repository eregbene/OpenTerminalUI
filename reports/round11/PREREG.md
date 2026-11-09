# Round 11 pre-registration (written 2026-11-07 BEFORE running): Flow ML + central-bank policy-rate features

Idea: the 2y yield (already in Flow ML) mixes today's policy rate with expected hikes/cuts. Policy rates (BIS WS_CBPOL
daily, data/macro/policy/policy_rates_daily.csv, used with a 1-day lag) separate the two and give AUD rate data.
New features per pair (base minus quote): pol_gap (policy-rate gap), pol_d60 (its 60-business-day change), exp_gap
((2y - policy) base minus quote; NaN without 2y, e.g. AUD), pol_last (sign of each central bank's latest move, base
minus quote).
Method: identical to round 8 Flow ML walk-forward (r8_flowml_yields.py: edge-miner features + 2y gaps incl. GBP/CHF/NZD,
HGB long/short, train on years < Y-1, threshold q95 of year Y-1 predictions over 25 pairs, 6 h hold, one trade per pair,
real FXCM spreads), 3 seeds (0, 7, 13), BASE and +POLICY run in the same script with the same seeds. No late hours (20-00).
Periods: DISC 2016-18, VAL 2019-22, LOCK 2023-26. Groups: LIVE5, OTHER20.
PASS only if, in BOTH groups: (a) +POLICY bp/trade > BASE in DISC, VAL and LOCK; (b) daily Sharpe +POLICY > BASE;
(c) +POLICY week-clustered t >= 2. Also reported (not pass criteria): with the ATR6/48 > 1.2 filter, AUD pairs, $ at 1/5/7 lots.
