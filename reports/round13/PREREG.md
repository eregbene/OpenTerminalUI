# Round 13 pre-registration (written 2026-11-07 BEFORE running): FX-only, ideas never tested here

Sources: Evans "Forex Trading and the WMR Fix" (J. Banking & Finance; pre- and post-fix changes strongly negatively
correlated, strongest on the last trading day of the month); Panagiotou 2017 (pre-fix positions reverse during the fix);
Keloharju, Linnainmaa & Nyberg "Return Seasonalities" (J. Finance 2016; same-calendar-month returns predict, shown for
stocks, commodities, indices -- currencies untested); practitioner mean reversion of range-bound crosses.
Already tested and NOT repeated: momentum INTO the fix (round 8d), month-end unconditional, carry/momentum/value (round 12).

All FXCM data, EXACT quotes (buy at ask = bid + spread, sell at bid), +0.3 bp slippage. Times in London local time (DST-aware).
- H1a post-fix reversal: pre move = mid 15:30 -> 15:55; trade the OPPOSITE at 16:05 (after the 16:00 fix window), exit 17:00.
  M5 bars, 25 pairs, every weekday.
- H1b same, only the last trading day of each month.
- H1c reversal THROUGH the fix: opposite of the 15:30 -> 15:55 move, entry 15:55, exit 16:30.
- H2 same-calendar-month seasonality (BIS monthly 1994-2026, G8): score for month m = mean return vs USD of each currency in
  the same calendar month of all earlier years (>= 6 years); long top 3 / short bottom 3, hold the month; 3 bp per traded leg,
  policy-rate carry, 0.75 %/yr swap markup. Trading from 2000.
- H3 cross mean reversion on H4 (built from FXCM H1): AUDNZD, EURGBP, EURCHF (EURCHF from 2015-03). z = (close - SMA50)/SD50;
  fade when |z| first exceeds 2 at an H4 close; exit when z crosses 0 or after 30 H4 bars; carry from 2y gaps -0.5 bp/night.
Periods: H1/H3 DISC 2012-17, VAL 2018-21, LOCK 2022-26; H2 P1 2000-08, P2 2009-17, P3 2018-26.
PASS each: mean net > 0 in all three periods; clustered t >= 2 (H1: per week, H2: per month, H3: per week); BH q 0.05 over
the 5 claims. $ reported on the 100k account at 1 / 5 / 7 lots.
