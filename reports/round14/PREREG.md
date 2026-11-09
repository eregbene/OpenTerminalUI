# Round 14 pre-registration: equity-rebalancing FX flow

Written 2026-11-07 BEFORE downloading the new index data or running any test. Rules are fixed; anything added after results is labelled post-hoc in REPORT.md.

Hypothesis (Camanho, Hau & Rey, RFS 2022; Hau & Rey): when a foreign stock market outperforms the US, global funds rebalance/repatriate and the outperforming market's currency WEAKENS over the next month.

## Data
- Currencies: EUR (DAX ^GDAXI), JPY (Nikkei ^N225), GBP (FTSE ^FTSE), CAD (TSX ^GSPTSE), AUD (ASX200 ^AXJO), CHF (SMI ^SSMI), NZD (NZX50 ^NZ50); US = S&P 500 ^GSPC.
- All indices in LOCAL currency (Yahoo price index), month-end close = close of the last trading day of the calendar month (Yahoo timestamps shifted to exchange-local date with the exchange gmtoffset).
- FX: BIS month-end rates data/macro/cpi/WS_XRU_g8_eop.csv (foreign units per USD). Return of holding currency c over month n = rate[n-1]/rate[n] - 1.
- Carry: policy rate differential /12 from data/macro/policy/policy_rates_monthly_eop.csv (percent). Holding c long vs USD over month n earns (i_c[n-1] - i_USD[n-1])/100/12 (rates known at entry; missing values forward-filled from past only).
- Costs: 3 bp per traded leg on change of position (3 bp x |change in weight|, entry and exit both count) + swap markup 0.75 %/yr per unit position side (0.0075/12 x |weight| per month).

## Signal (end of month t, currency c)
- R1 = log return of c's index over month t minus S&P 500 log return over month t.
- R3 = same over months t-2..t (3-month log return difference).
- S = z-score of R1_t using mean and std of that currency's own R1 over the 36 months t-36..t-1 (only past data; needs all 36).

## Claims
- H1 time-series: S > +1 -> SHORT c vs USD for month t+1; S < -1 -> LONG c vs USD; else flat. Pooled across the 7 currencies: each month the active positions get equal weight 1/N_active (gross 1 unit; 0 if no position).
- H2 cross-sectional: each month rank the available currencies + USD (USD R1 = 0) by R1; SHORT the top 2 (largest relative equity outperformance), LONG the bottom 2, hold 1 month. Weights: -0.5 each short, +0.5 each long (1 unit per side). If USD falls in a bucket that leg is cash (weight 0).
- H3 = H2 using R3 instead of R1.
- Signal uses data through end of month t only; the return is month t+1 (FX t -> t+1, carry from rates at end of t).

## Sample
- Trade months from 2000-01 (signal at end of 1999-12) to 2026-10. If an index is missing for early years, that currency enters only once its signal exists (H1: once S exists; H2/H3: once R1/R3 exists) and this is reported.
- Periods: P1 2000-2008, P2 2009-2017, P3 2018-2026 (by trade month).

## PASS rule (per claim)
- mean monthly net return > 0 in P1, P2 and P3; AND
- monthly t-stat >= 2 over the full sample; AND
- survives Benjamini-Hochberg at q = 0.05 across the 3 claims (one-sided p from the full-sample monthly t).
- Report: Sharpe (annualised, mean/sd x sqrt 12), max drawdown (cumulative sum of net monthly returns), worst month, mean since 2022-01, and dollars on a $100,000 account at 1, 5 and 7 lots, 1 lot = $100,000 notional per side (monthly $ = net return x 100,000 x lots).
