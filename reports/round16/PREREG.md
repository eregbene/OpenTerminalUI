# Round 16 — Intraday-only carry: pre-registration

Written 2026-11-07 BEFORE any return was computed. Idea: Krohn, Mueller & Whelan (2024),
"Uncovered Interest Parity in High Frequency": carry is earned during the US trading day,
the overnight price move offsets the interest, announcement days give most of carry's return.
A position flat before the 17:00 New York rollover pays no swap.

## Data
- Prices: FXCM M5 `data/bars/fxcm/<PAIR>_M5.npz` (t = bar OPEN epoch s UTC; o h l c BID; spr in price units).
  Quote at bar close time t+300: bid = c, ask = c + spr, mid = c + spr/2. Spreads used as given (zero spreads included, count reported).
- Pairs: EURUSD GBPUSD AUDUSD NZDUSD USDJPY USDCHF USDCAD.
- Policy rates: `data/macro/policy/policy_rates_daily.csv` (BIS, percent), forward-filled.
- Release dates: `data/macro/us_releases/dates.csv` — NFP (BLS), CPI (BLS), Advance Retail Sales (Census).
  ISM Manufacturing and GDP advance DROPPED before testing (no complete official date list obtainable: FRED blocked, ISM no archive, BEA archive returned only ~32 of ~59 advance releases).

## Rules
1. Universe: USD EUR JPY GBP CAD CHF AUD NZD.
2. Signal for NY-local business day D (Mon-Fri): rank by policy rate of calendar day D-1 (ffilled). Long the top 3, short the bottom 3.
   Ties broken by alphabetical currency code (earlier code ranks higher). Same ranking for every hypothesis.
3. Each non-USD currency X is traded via its USD pair. Long X = buy XUSD / sell USDX; short X the reverse.
   Per-currency return vs USD (net): XUSD long `bid_exit/ask_entry - 1`; XUSD short `bid_entry/ask_exit - 1`;
   USDX long X `bid_entry/ask_exit - 1`; USDX short X `bid_exit/ask_entry - 1`; minus 0.3 bp slippage at entry and 0.3 bp at exit (0.6 bp per currency leg per day).
   Gross version: same with mid prices, no slippage. USD itself as a long/short member has return 0 and cost 0.
   Portfolio return = mean(returns of the 3 long members) + mean(signed returns of the 3 short members) (i.e. mean(long) - mean(short) in currency-return terms).
4. Window: entry quote at 08:00 New York local time (America/New_York, DST-aware), exit quote at 16:45 New York local time, same day.
   Quote = bar whose close time (t+300) equals the target; if missing, the latest bar whose close time is within 15 minutes before the target.
   If any of the 7 pairs lacks an entry or exit quote, the day is skipped.
5. Hypotheses (net):
   - H1: every business day.
   - H2: only days that are US release days (NFP, CPI or Retail Sales, NY date).
   - H3 (control, reported only): non-release days.
   Reported only: gross versions; "overnight" counterpart = same portfolio from 16:45 NY day D to 08:00 NY next trading day in the sample,
   price return (gross mid, and net of spread+slippage) plus carry = sum over members of signed (i_X - i_USD)/365 x nights,
   nights = 3 if the 17:00 NY rollover crossed is a Wednesday, else 1 (rates of day D-1, same as the ranking).
6. Periods (NY date): DISC 2012-2017, VAL 2018-2021, LOCK 2022-2026.
7. PASS per claim (H1, H2): mean net bp/day > 0 in DISC, VAL and LOCK; week-clustered (ISO week) t over the full sample >= 2;
   Benjamini-Hochberg at q = 0.05 over the claims tested (H1, H2), using one-sided normal p-values from the clustered t.
8. Dollars: $100,000 account; 1 lot = $100,000 notional per side (long side 100k split equally over the 3 long members, short side likewise).
   P&L $ = portfolio return x 100,000 x lots, not compounded. Report $ per year, worst calendar month and max drawdown at 1, 5, 7 lots.
9. No rule changes after results. Anything added afterwards is labelled post-hoc.
