# Round 10 pre-registration (written 2026-11-07, BEFORE any test was run)

Sources: Karnaukh "The Dollar Ahead of FOMC Target Rate Changes"; Mueller, Tahbaz-Salehi & Vedolin "Exchange Rates and
Monetary Policy Uncertainty" (J. Finance 2017); Lee & Wang (2025) post-FOMC reversal; Breedon & Ranaldo "Intraday
Patterns in FX Returns and Order Flow" (JMCB 2013). Already tested earlier and NOT repeated: COT (round 3), month-end
unconditional (round 3), single pair-hour scan (round 8).

Data: FXCM H1 2012-2026 (data/bars/fxcm), EXACT quotes (buy at ask = bid close + spread, sell at bid close), + 0.3 bp
slippage per trade. FOMC statement days 2012-2026 from federalreserve.gov (data/macro/fomc_dates.csv, scheduled only;
2020-03-03 / 03-15 unscheduled excluded). US 2y yield: data/macro/yields_ext/yields_2y_plus.csv. Overnight holds: carry
from 2y gaps /365 per night minus 0.5 bp/night broker markup (AUD: no yield -> carry 0, markup only).
USD pairs: EURUSD GBPUSD AUDUSD NZDUSD USDJPY USDCHF USDCAD; "long USD" = sell XXXUSD / buy USDXXX. Results pooled.
Periods: DISC 2012-17, VAL 2018-21, LOCK 2022-26.

- H1a pre-FOMC conditional drift: entry 15:00 UTC on the 2nd weekday before the statement day D, exit 17:00 UTC on D
  (before the 18/19 UTC statement). Direction = sign of the US 2y change from D-25 to D-3 business days (data only up to
  D-3): rising -> long USD, falling -> short USD.
- H1b same, only when |change| >= 10 bp.
- H2 pre-FOMC unconditional long USD, same window.
- H3 post-FOMC reversal: entry 20:00 UTC on D, exit 20:00 UTC on D+1 (next weekday), direction = OPPOSITE of the USD
  move over the H1 window (measured on the same 7 pairs, mid to mid).
- H4 home-hours depreciation (pooled, all 25 pairs): home windows in LOCAL time, DST-aware: Tokyo 09-15 (JPY), Sydney
  09-15 (AUD, NZD), London 08-12 (EUR, GBP, CHF), New York 08-16 (USD, CAD). Per pair and window: sell when only the
  base currency is at home, buy when only the quote is at home, skip otherwise. Entry at the window open, exit at its close.

PASS (each of the 5 claims): mean net bp > 0 in DISC, VAL and LOCK; event-clustered (H1-H3: per FOMC event; H4: per
week) t >= 2 over 2012-26; Benjamini-Hochberg q = 0.05 over the 5 claims (one-sided). Per-pair / per-window results are
reported only, never used to pass. $ reported on the 100k account at 1 / 5 / 7 lots.
