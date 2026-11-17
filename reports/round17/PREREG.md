# Round 17 — Economic momentum and output-gap currency sorts: pre-registration

Written 2026-11-07 BEFORE any strategy return was computed (only data coverage was inspected).
Papers: Dahlquist & Hasseltoft (JFE 2020) "Economic momentum and currency returns";
Colacito, Riddiough & Sarno (JFE 2020) "Business cycles and currency returns". 2018-2026 is post-publication.

## Data (data/macro/econ/econ_tidy.csv, built by reports/round17/fetch_econ.py; latest vintages, NOT real-time)
- IP: OECD DF_INDSERV 4.3, production volume, total industry (BTE), seasonally adjusted. Monthly: USD EUR JPY GBP CAD.
  Quarterly: CHF (monthly only from 2010-10, so quarterly used for the whole sample), AUD, NZD.
- CPI inflation yoy (%): BIS WS_LONG_CPI monthly for USD EUR JPY GBP CAD CHF; OECD DF_PRICES_ALL quarterly (GY) for AUD NZD.
- Unemployment rate SA, 15+: OECD DF_IALFS_UNE_M monthly for USD JPY GBP CAD AUD; quarterly for CHF (1999-2009 one Q2 obs per year only) and NZD;
  euro area from Eurostat une_rt_m geo EA21 (from 2000-01).
- No series dropped: every currency has all three series (coverage table in data/macro/econ/coverage.csv).
- Publication lag: an observation whose reference period ends in month r is usable from the end of month r+2 (monthly series)
  or r+3 (quarterly series). Example: March IP usable at end of May; Q1 (ref month 03) usable at end of June.
  x_avail(s) = latest observation usable at end of month s.

## Rules
- Universe: USD EUR JPY GBP CAD CHF AUD NZD. FX returns from BIS month-end rates data/macro/cpi/WS_XRU_g8_eop.csv
  (foreign units per USD; holding return of currency X over month n = rate[n-1]/rate[n]-1; USD = 0).
  Carry = policy-rate differential (i_X - i_USD)/12 at end of month n-1 from data/macro/policy/policy_rates_monthly_eop.csv (percent, /100).
  Currency excess return over month n: e_X = FX return + carry. If a policy rate is missing at n-1, the last earlier value is used.
- Costs: 3 bp per traded leg on position changes = 0.0003 x sum over non-USD currencies of |w_n - w_{n-1}|,
  plus 0.75 %/yr swap markup = 0.0075/12 x sum over non-USD currencies of |w_n|. USD positions cost nothing.
- Portfolio weights: +1/3 on each of the top 3, -1/3 on each of the bottom 3 (USD may be a member; its return is 0).
  Net return month n = sum_X w_X e_X - costs. Ties broken alphabetically (earlier code ranks higher).
  If fewer than 6 currencies are scored at a signal date, the portfolio is flat that month (w = 0, costs of closing apply).
- Underlying series per currency: IP growth yoy (monthly: IP_m/IP_{m-12}-1; quarterly: IP_q/IP_{q-4}-1), CPI inflation yoy, minus unemployment rate.
- EM score at end of month t: for each series, d = x_avail(t) - x_avail(t-12) (both must exist); z-score d across currencies that have it
  (population sd, need >= 4 currencies, else that series is skipped that month); EM = mean of the currency's available z's.
- OG at end of month t: take all IP observations usable at t (native frequency, need >= 60 obs), log IP, one-sided HP trend by
  re-estimating a two-sided HP filter (lambda 129600 monthly / 1600 quarterly) on data to that point and taking the gap at the last obs;
  z-score across currencies (need >= 4).
- H1: monthly long top 3 / short bottom 3 by EM, equal weight, signal end of t, hold month t+1.
  H2: same by OG. H3: score = mean of z(EM) (EM re-z-scored across currencies) and OG z; if one is missing, the other alone.
- Sample: holding months from the first month in which all of EM, OG and H3 have >= 6 currencies scored (target ~2000) to 2026-10.
  Periods by holding month: P1 2000-2008, P2 2009-2017, P3 2018-2026 (months before 2000, if any, count in the full sample only).
- PASS each of H1-H3: mean monthly net > 0 in P1, P2 and P3; full-sample monthly t (mean/sd*sqrt(n)) >= 2;
  Benjamini-Hochberg at q = 0.05 across H1-H3 using one-sided normal p-values from that t.
- Reported: Sharpe (annualised, mean/sd*sqrt 12), max drawdown of cumulative (non-compounded) net return, worst month, mean since 2022,
  gross means, and $ on a $100,000 account at 1, 5, 7 lots (1 lot = $100k notional per side; monthly $ = net return x 100,000 x lots, not compounded).
  Correlation of H3 monthly net returns with simple carry (long top 3 / short bottom 3 by policy rate at t, same costs) — reported only.
- No rule changes after results; anything added afterwards is labelled post-hoc.
