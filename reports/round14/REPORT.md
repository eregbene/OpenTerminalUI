# Round 14: equity-rebalancing FX flow (Camanho/Hau/Rey). Verdict: FAIL, all 3 claims

The rules were fixed in `PREREG.md` before any data was downloaded or any test was run. Trade months run 2000-01 to 2026-10 (322 months). Returns are net of 3 bp per traded leg, a 0.75 %/yr swap markup and policy-rate carry.

| Claim | mean net %/mo | t | P1 00-08 | P2 09-17 | P3 18-26 | since 2022 | Sharpe | max DD (units) | worst month | BH | PASS |
|---|---|---|---|---|---|---|---|---|---|---|---|
| H1 time-series z>1 | -0.355 | -2.98 | -0.104 | -0.398 | -0.568 | -0.464 | -0.58 | -120.7 % | -7.11 % (2010-05) | no | **FAIL** |
| H2 cross-section R1 | -0.312 | -2.51 | -0.115 | -0.311 | -0.514 | -0.469 | -0.49 | -100.5 % | -8.62 % (2016-06) | no | **FAIL** |
| H3 cross-section R3 | -0.338 | -2.73 | -0.563 | -0.362 | -0.084 | +0.104 | -0.53 | -123.5 % | -10.42 % (2009-03) | no | **FAIL** |

Gross means before costs: H1 -0.26, H2 -0.12, H3 -0.18 %/month. The flow is the wrong sign even before costs.

Dollars use 1 lot = $100k notional per side (simple sum, no compounding):

| Claim | $/yr 1 lot | $/yr 5 lots | $/yr 7 lots | worst month 1 / 5 / 7 lots |
|---|---|---|---|---|
| H1 | -4,265 | -21,324 | -29,854 | -7,114 / -35,571 / -49,800 |
| H2 | -3,744 | -18,721 | -26,209 | -8,625 / -43,125 / -60,375 |
| H3 | -4,056 | -20,278 | -28,389 | -10,424 / -52,118 / -72,966 |

A max drawdown above 100 % in units means even a 1-lot account would have lost more than its whole $100k.

## In plain English
The paper says the currency of a stock market that beat the US should weaken over the next month. In our data the opposite happens on average: the currency tends to keep rising, which looks like a little momentum or risk-on co-movement. Fading it loses money in every period and on every version. The costs (about 0.1-0.2 %/month) make it worse. Not tradable.

## Data
- Yahoo daily closes are saved in data/indices/. Coverage: GSPC, GDAXI, N225, FTSE and GSPTSE from 1990-01; SSMI from 1990-11; AXJO from 1992-11; NZ50 only from 2003-01. No month is missing in any series.
- NZD has no z-score until 2006, so it enters H1 only from 2006-03. It enters H2/H3 from 2003 once R1/R3 exist. Every other currency has signals before 2000.
- FX data: the BIS "end of period" rate is the central-bank fixing on the last business day (the ECB fixes at 14:15 CET). So it comes before the US equity close. The FX month therefore starts a few hours before the signal is fully known (see post-hoc check 1).
- Sanity checks: sample months are printed by the script. Weights for month n use only the index closes at end of n-1. Carry uses rates at end of n-1. Asserts in the script check that the H1 position count matches the previous month's |S|>1 count, and check the FX return formula.

## Post-hoc notes (NOT part of the pre-registered test)
1. Index month-ends taken from the second-to-last trading day, so the signal is known before the FX fixing: H1 -0.334 (t -2.94), H2 -0.383 (t -3.33), H3 -0.305 (t -2.57). Same conclusion; the timing overlap is not the cause.
2. Reversed sign (follow the outperforming market's currency), net: H1 +0.164 %/mo (t 1.38, P1 negative), H2 -0.077 (t -0.62), H3 +0.024 (t 0.19). Nothing that would pass. Do not treat this as a finding: it is a new hypothesis picked after seeing the data.
