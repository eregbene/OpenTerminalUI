# Mentor replay report — 2026-11-09

Question from the user: *why did we say "no edge" when the code may not even trade like the mentor?*
Method from now on: (1) code the rules, (2) replay the mentor's OWN trades from the video frame by frame and fix the
code until it takes them, (3) only then backtest, (4) only then a verdict.

## 1. Mentor trades replayed (full-size frames)

| Mentor / video | His trade | Before today | After today's fixes |
|---|---|---|---|
| Bennett, 9-rules video | EURUSD short 2 Jan 2026, 1.17569 / stop 1.17657 / T 1.17201, ~+4R | no trade | same leg traded 30 Dec (1.17725, +3.5R), not his entry |
| Bennett, 9-rules video | EURUSD short 6 Jan 2026, 1.17217 / 1.17442 / 1.16591, ~+2.8R | no trade | **exact entry 1.17217** (5 Jan 21:40), but entered before the 6 Jan push -> stopped |
| Bennett, $13,491 video | USDCAD long 10 Mar 2026, 1.3544 / ~1.3522 / 1.3640, +5R | no trade | not matched (channel/range trade, no channel mode) |
| Bennett, weekly outlook 12-16 Jan | AUDUSD short only after a close < 0.6660 | we shorted anyway (+0.15R) | condition not coded |
| Faiz, video 2 (F1) | GBPUSD 15M short 9 Aug 2022, ~1.2124 / ~1.2138 / 1.20689 | went LONG, -1R | **exact**: 09:10 short 1.21254 / 1.21383 / 1.20655, +4.64R |
| Faiz, video 3 (F2) | GBPUSD 15M long 22 Jul 2022, 1.1936 / 1.19109 / 1.19713 (1.39R) | went SHORT, -1R | setup found (1.19376 / 1.19113) but not traded |
| Faiz, video 4 (F3) | GBPUSD 5M short 17 Aug 2022, ~1.2076 / ~1.2093 / 1.20275 | none | near-exact with internal M5 (1.20662 / 1.20966 / 1.20282) |
| Faiz, video 5 (F4) | EURUSD 5M long 8 Aug 2022, 1.0189 / 1.01835 / 1.02218 (5.72R) | none | **exact**: 18:50, 1.01885 / 1.01724 / 1.02220, +2.02R |
| Faiz, video 29 | EURUSD 5M short 6 Sep 2022, 0.99351 / 0.99443 / 0.98801 (~6R) | none | not matched (no trade any setting) |
| JeaFx, 68-min video | NZDJPY 15M long 7 May 2025, ~85.40 / ~85.27 / 86.16, +5.42% | none | **exact**: 18:05 UTC 85.401 / 85.252 / 86.18, +5.21R |
| JeaFx | NZDJPY 15M shorts 6 May 2025 (+3.94%, -1%) | none | not matched |
| Phantom, GBPJPY sell recap | GBPJPY 5M short 21 Aug 2020, 139.806 / 139.953 | — | not matched: our stop = his entry (zone inside his) |
| imbalance.mp4 (Oct) | 2 trades | exact | exact |

Matched now: Bennett 1 exact entry (of 3), Faiz 2 exact + 1 near (of 5), JeaFx 1 (of 3), Phantom 0 (of 1).

## 2. Code changes (all OPTIONS, defaults unchanged, live DEMO untouched)
- `bensim/bennett_strategy.py`: `leg_anchor="latest"` + `leg_swing_atr` (fib on the newest pull-back-confirmed 1H swing).
- `bensim/orderflow_strategy.py` (Faiz): `flow_min_rr` (he states no minimum in videos 1-5), `entry_sec` (M5/M15),
  `structure="swing"` (his MSS = the main swing's higher low broken).
- `bensim/sflow.py` (JeaFx): `ZONE_LIFE="touch"` (zone lives until touched / closed through, not killed by the next BOS);
  `ZONE_ORIGIN="impulse"` tried and rejected (did not recover his shorts, added losers).

## 3. Three-month tests (2026-08-07 .. 2026-11-06, 7 pairs, net of costs)

| Version | Trades | Win | R/trade | t | Total | $ at $500 risk | fixed 1 / 5 / 7 lots |
|---|---|---|---|---|---|---|---|
| Bennett LIVE (DEMO config) | 5 | 40% | +0.80 | 0.79 | +4.0R | +$2,002 | +$2.1k / +$10.6k / +$14.9k |
| Bennett base video | 36 | 33% | -0.02 | -0.08 | -0.8R | -$399 | -$3.7k / -$18.5k / -$25.9k |
| Bennett newest leg (1 ATR) | 49 | 22% | -0.12 | -0.47 | -5.9R | -$2,950 | +$6.0k / +$30.1k / +$42.1k |
| Bennett newest leg (2 ATR + FVG) | 33 | 27% | -0.03 | -0.08 | -0.8R | -$423 | +$2.9k / +$14.6k / +$20.4k |
| Faiz OLD (Oct) | 39 | 36% | +0.18 | 0.64 | +7.0R | +$3,504 | +$9.4k / +$47.1k / +$66.0k |
| Faiz MATCHED | 61 | 21% | -0.00 | -0.01 | -0.1R | -$58 | +$9.5k / +$47.3k / +$66.3k |
| JeaFx OLD H1 (live book) | 29 | 34% | +0.29 | 0.68 | +8.5R | +$4,239 | +$14.8k / +$74.1k / +$103.8k |
| JeaFx OLD M15 | 103 | 24% | -0.07 | -0.32 | -7.6R | -$3,784 | +$12.6k / +$62.9k / +$88.1k |
| JeaFx MATCHED H1 | 38 | 26% | -0.02 | -0.05 | -0.6R | -$323 | +$12.0k / +$59.8k / +$83.7k |
| JeaFx MATCHED M15 | 147 | 22% | +0.06 | 0.24 | +9.0R | +$4,482 | +$42.9k / +$214k / +$300k |

Fixed-lot $ are dominated by GOLD (wide gold stops x $10/pip/lot): JeaFx MATCHED M15 = gold +58R, every FX pair negative
(EURUSD -15R, USDCAD -20R). At $500 risk (our real sizing) the picture is the R column.
No version is statistically significant (all |t| < 1). Matching the mentor did NOT make any strategy clearly profitable
over these 3 months.

## 4. User's stop idea (stop >= target distance, normal targets), Bennett 3 months
| Version | his stop | stop >= target (1:1) |
|---|---|---|
| base video | 33% win, -0.8R | 53% win, -2.0R |
| newest leg (1 ATR) | 22% win, -5.9R | 47% win, **+3.0R** |
| newest leg (2 ATR + FVG) | 27% win, -0.8R | 39% win, -7.2R |
| LIVE | 40% win, +4.0R | 40% win, +0.8R |
Win rate rises as expected, but not past the ~55% a 1:1 needs; mixed, not significant; at fixed lots much bigger losses
(median stop 65-80 pips). Not adopted. Pip-value sizing ($500 / $1,000) stays for every strategy.

## 5. Conclusions
1. The earlier "no edge" verdicts on Bennett / Faiz / JeaFx were premature: the code did not take their trades.
2. After matching what we can, none of the mentor versions is proven profitable on 3 months; several are ~break-even.
3. Remaining mismatches have one recurring cause: the mentor picks the NEWEST / OUTER zone or swing and gives the
   stop room beyond the liquidity; our code picks an older / inner one or a tight stop.
4. Next: longer samples (1-2 years) for the matched versions before any DEMO change; Bennett channel mode; Faiz F2 and
   video 29; JeaFx shorts; Phantom's outer-zone rule.

## 6. Two-year test (2024-11-07 .. 2026-11-06, reports/round25/two_year.log) -- PASS bar: both years > 0, t >= 2, $ > 0
NO version passes. Closest: Bennett CHANNEL mode 1,032 trades +135.3R (Y1 +93.1 / Y2 +42.2, t 1.29, +$67.6k @$500; with a 1-ATR stop floor +147.9R, Y1 +86.6 / Y2 +61.4, t 1.41). Bennett base +38.1R (Y1 +34.1 / Y2 +3.9, t 1.05); Bennett live +12.2R (48 tr, both years +). Faiz matched -114.6R (t -2.17, significantly negative), Faiz old -47.3R; JeaFx all negative (-21.9 / -22.9 / -128.5R). User's 1:1 stop: pulls every version to ~0 (channel +135R -> +0.4R) -> dropped. Caveat: the common replay walker is harsher than the sflow/orderflow engines on tiny stops (JeaFx M15 engine +51R vs replay -128R); Bennett/channel agree. Next: channel mode on the UNTOUCHED 2021-10..2024-11 period before any DEMO step.

## 7. Channel mode on the UNTOUCHED period 2021-10-12 .. 2024-11-06 -- FAIL
1,253 trades, 16% win, -25.0R (t -0.23); $-12,486 @$500; fixed 7 lots $-56,811. Years: 2022 -76.4R, 2023 +92.9R, 2024 -44.1R. Gold -72.8R. Worst losing streak 35 trades. 1-ATR floor: -38.0R. The 2-year +135R does not repeat -> period-dependent, not an edge. Not for the DEMO.
