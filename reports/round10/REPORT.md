# Round 10 result (2026-11-07): FOMC dollar drift + home-hours depreciation -- ALL FAIL (pre-registered: PREREG.md)
Script r10_fomc_home.py, numbers r10_results.json, trades r10_fomc_trades.csv / r10_home_trades.csv. FXCM 2012-26 exact quotes.

| claim | trades (events) | net bp/trade | t | DISC / VAL / LOCK | verdict |
|---|---|---|---|---|---|
| H1a pre-FOMC drift, 2y-direction | 756 (108) | +0.06 | 0.01 | +1.7 / +2.2 / -3.8 | FAIL |
| H1b same, only |dy2| >= 10 bp | 350 (50) | +8.07 | 1.15 | -4.4 / +20.9 / +8.4 | FAIL |
| H2 long USD pre-FOMC | 798 (114) | -7.43 | -1.68 | all negative | FAIL |
| H3 post-FOMC reversal 24 h | 798 (114) | +6.45 | 1.23 | +7.8 / -17.8 / +26.1 | FAIL |
| H4 home-hours depreciation, 25 pairs | 145,666 | -1.15 | -6.8 | all negative | FAIL (costs) |
Post-hoc check (not a claim): SHORT USD pre-FOMC +4.06 bp, t 0.92, LOCK -0.03 -> nothing after costs + swap.
