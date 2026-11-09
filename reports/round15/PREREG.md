# Round 15 pre-registration (written 2026-11-07 BEFORE running any test)

Idea: conditional pre-decision drift before NON-US central bank meetings (Borisenko & Pozdeev, St. Gallen WP 2017:
currencies appreciate ~0.5% in the 10 days before priced hikes, depreciate ~0.7% before priced cuts, no drift before
no-change meetings).

Meeting dates: data/macro/cb_meetings/<ccy>.csv (EUR ECB, GBP BoE, JPY BoJ, CAD BoC, CHF SNB, AUD RBA, NZD RBNZ),
scheduled decisions 2012-01 .. 2026-10, collected from the central banks' own sites before any test was run.

## Rules (as given)
- Expected-move proxy at decision day D for currency c: X = (2y yield c - policy rate c) at D-11 business days, using
  data/macro/yields_ext/yields_2y_plus.csv (USD EUR JPY CAD GBP CHF NZD, percent; AUD has no 2y -> AUD uses only the
  policy-rate-change variant below) and data/macro/policy/policy_rates_daily.csv (BIS daily policy rates, percent).
  Variant X2 = change in (2y - policy) over the 20 business days ending D-11.
- H1: if X >= +0.15 (market prices hikes) go LONG c vs USD; if X <= -0.15 go SHORT c vs USD; else no trade.
  Entry at the H1 close 10 business days before D at 15:00 UTC, exit at the H1 close at 06:00 UTC on D (before any of
  these banks announce). H2 same with X2 and threshold +/-0.10. H3 (no-change control, reported only): meetings with
  |X| < 0.05, long c vs USD.
- Pairs: EURUSD GBPUSD AUDUSD NZDUSD USDJPY USDCHF USDCAD (USD-quoted direction handled correctly: long JPY = short USDJPY).
  FXCM H1 bars data/bars/fxcm/<PAIR>_H1.npz, t = bar OPEN (epoch s UTC), o h l c = BID, spr in price units; quote at a bar
  CLOSE time = t+3600: bid = c, ask = c+spr. Buy at ask, sell at bid, +0.3 bp slippage.
- Swap: carry from 2y gaps (or policy-rate gaps if a 2y is missing) /365 per night crossed (21:00 UTC rollover, Wednesday
  counts 3) minus 0.5 bp/night markup.
- Periods: DISC 2012-17, VAL 2018-21, LOCK 2022-26 (by decision date D).
- PASS per claim (H1, H2): mean net bp per trade > 0 in all three periods; t >= 2 clustered by meeting; Benjamini-Hochberg
  q 0.05 across the 2. Per-bank results reported only.
- Dollars: $100,000 account, 1 lot = 100,000 units base; $ per bp per lot EUR 11.6, GBP 13.3, AUD 6.5, NZD 5.8, USD-base
  pairs 10. Report $ per year, worst month, max drawdown at 1, 5 and 7 lots.

## Implementation details fixed now (before running)
1. Business days = Monday-Friday (no holiday calendar). D-10 / D-11 / D-31 counted in business days.
2. Daily yields/policy rates: value on the date, else the last value up to 5 business days earlier (policy rates are
   step series and are forward-filled without limit). Missing X -> no trade (counted).
3. Entry = bar whose close is exactly 15:00 UTC on D-10 (bar open 14:00). Exit = bar whose close is exactly 06:00 UTC on D
   (bar open 05:00). If either bar is missing -> trade skipped and counted. Bars end 2026-10-09, later meetings drop out.
4. Net bp = side * (exit fill - entry fill) / entry mid * 1e4 - 0.3, with side +1 = buy the pair. Long c vs USD = buy
   EURUSD/GBPUSD/AUDUSD/NZDUSD, sell USDJPY/USDCHF/USDCAD.
5. Carry (bp/night) for long c vs USD = (r_c - r_USD) * 100 / 365 with r = 2y yields at entry date; if either 2y is
   missing both legs use policy rates. Sign flips for short c. Nights = number of 21:00 UTC instants in (entry, exit],
   a Wednesday 21:00 counts 3. Markup 0.5 bp per counted night is subtracted always.
6. AUD: H1 and H3 have no AUD trades (no 2y). AUD in H2 uses X2_AUD = change in the AUD policy rate over the 20 business
   days ending D-11, threshold +/-0.10 (the "policy-rate-change variant").
7. Clustered t: trades on the same decision date across banks are summed into one cluster (meeting-date cluster);
   with one bank per date this equals per-meeting t. t = mean / (sd / sqrt(n_clusters)). One-sided p for BH.
8. Monthly $ series and drawdown by exit month; $/bp per lot as above (USDJPY/USDCHF/USDCAD = 10).

## Known issue spotted before running (flagged now, not a rule change)
- Exit 06:00 UTC on D is BEFORE ECB/BoE/SNB/BoC announcements but AFTER BoJ (~03:00 UTC), RBA (03:30/04:30 UTC since 2024,
  03:30 UTC before) and RBNZ (01:00-02:00 UTC) announcements. So for JPY, AUD and NZD the literal rule holds the position
  THROUGH the announcement. Primary PASS/FAIL uses the rules exactly as given. A secondary "clean" variant is also
  reported (pre-registered now): JPY/AUD/NZD exit at the H1 close 06:00 UTC on D-1 business day; all else identical.
  It gets its own PASS/FAIL line and is not used to rescue the primary result.
- BIS "policy rate" for EUR is the MRO rate; the ECB's effective floor was the deposit rate (0.4-0.5 pp below MRO in
  2014-2024), so EUR X is biased negative in that era. Rules kept as given; noted in the report.
