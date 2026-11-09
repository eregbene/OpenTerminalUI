# FXCM M5 / H1 data-quality report (2026-11-03, read-only, no strategy backtests)

Code: `bensim/fxcm_quality.py`. Files: `coverage.csv`, `ic_compare.csv`, `detail.json` (per year and per hour).

## Source and format
- **Origin:** `bensim/fxcm_history.py` (docstring + code L69-79), downloaded from `candledata.fxcorporate.com`, one gzip CSV per week. **Weeks that returned 404 were counted, not filled.**
- **Prices:** **bid** OHLC. M5 is **aggregated from M1**. `spr` = AskClose − BidClose at the bar close, clipped at ≥ 0. Times are **UTC bar open**.
- **Instruments:** 25 pairs: AUDCAD AUDCHF AUDJPY AUDNZD AUDUSD CADCHF CADJPY EURAUD EURCHF EURGBP EURJPY EURNZD EURUSD GBPCAD GBPCHF GBPJPY GBPNZD GBPUSD NZDCAD NZDCHF NZDJPY NZDUSD USDCAD USDCHF USDJPY. **No XAUUSD.**
- **Range:** every pair 2012-01-02 03:05 → 2026-10-09 20:55 UTC; ~1.065 M M5 bars per pair.

## Integrity (all 25 pairs, M5)
| Check | Result |
|---|---|
| duplicate timestamps | **0** |
| non-increasing timestamps | **0** |
| off the 5-min grid | **0** |
| bad OHLC (high < max(open, close), low > min(open, close), low ≤ 0) | **0** |
| weekday holes > 15 min | 105 (EURUSD) – 394 (EURNZD) per pair |

**Missing data concentrates in whole weeks and grows after 2022.**

Hours lost to weekday holes (EURUSD), by year:

| 2012 | 2013 | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 169 | 123 | 181 | 202 | 49 | 97 | 218 | 218 | 443 | 392 | 291 | 494 | 458 | 723 | 49 |

- **Missing bars vs IC Markets** (bars IC has that FXCM lacks): EURUSD 2021: 4; 2022: 42; 2023: 1,642; 2024: 4,706; 2025: 6,570; **2026: 20,278 (≈ 35 % of the year)**. They're spread evenly over all hours, i.e. whole weeks missing.
- **CADCHF and NZDCHF have a 100-day hole** (2026-05-01 → 2026-08-09) and lose 1,000–2,900 h a year from 2024.
- **Largest holes:** typically one week (e.g. 2024-08-23, 2025-06-20, 2025-07-11 USDCAD 16 days).

## Time-zone consistency (vs the IC Markets DB, overlapping pairs)
Median absolute close difference in pips, FXCM(t) vs IC(t + lag):

| Pair | Overlap | lag −60 m | lag −5 m | **lag 0** | IC bars present in FXCM |
|---|---|---|---|---|---|
| EURUSD | 2018-10 → 2026-10 | 4.4 | 1.3 | **0.1** | 94.4 % |
| GBPUSD | 2021-10 → 2026-10 | 6.0 | 1.7 | **0.2** | 91.1 % |
| USDJPY | 2021-10 → 2026-10 | 7.6 | 2.1 | **0.2** | 91.0 % |
| USDCHF | 2021-10 → 2026-10 | 4.3 | 1.2 | **0.1** | 90.7 % |
| USDCAD | 2024-02 → 2026-10 | 4.2 | 1.3 | **0.3** | 84.2 % |
| AUDUSD | 2021-10 → 2026-10 | 4.4 | 1.3 | **0.1** | 91.1 % |

- **Lag 0 is clearly best on every pair, so the time stamps are consistent UTC (H).**
- **Prices agree closely:** 0.1–0.3 pip median difference; the p95 and > 5-pip shares are in `ic_compare.csv`.
- **IC Markets bars are almost all in FXCM where FXCM has data:** 99.85–99.96 % of FXCM bars exist in IC. The 6–16 % shortfall is FXCM's missing weeks.

## Spread coverage
| | EURUSD | GBPUSD | USDJPY | USDCHF | USDCAD | AUDUSD | crosses |
|---|---|---|---|---|---|---|---|
| bars with spread > 0 | 91.9 % | 98.0 % | 95.2 % | 98.1 % | 97.9 % | 96.7 % | 78–99.8 % (CADCHF 79 %, NZDCHF 78 %) |
| median spread (pips) | 0.2 | 0.5 | 0.3 | 0.4 | 0.4 | 0.3 | 0.4–2.4 |
| p90 spread (pips) | 0.5 | 1.1 | 0.7 | 1.0 | 0.9 | 0.6 | 0.9–5.5 |

- **Zero-spread bars:** EURUSD 2013–2021 has up to 21 % zero-spread bars (2020-21); 0 % from 2023. They're **artefacts** (ask = bid at the close, or clipped negative values).
- **FXCM spreads are a different broker and account type.** IC demo observed today: EURUSD 0.8–0.9 pip, $0 commission. FXCM's EURUSD median is 0.2 pip. **They don't reproduce IC Markets execution costs** and must not be used as such. They're usable as a *relative* (by hour, by year, by news) spread profile only.

## Suitability by strategy input
| Need | FXCM status |
|---|---|
| M5 bid OHLC for `plan()` strategies (fib618, imbalance, enigma, H1 FVG, Bennett, pullback, orderflow, gf) | **available**, 25 pairs; M15/H1/H4/D1 derivable by resampling (same as the live engine). Usable for 2012 → 2022 with ~1–4 % missing hours/yr; **2023 → 2026 degraded** (up to 35 % missing in 2026) |
| XAUUSD (fib618, imbalance, enigma live symbol) | **not available** |
| Long-context windows (Bennett htf 1,300 H1 bars; gf 3,200) | available. Missing weeks break swing structure locally, so warm-up and holes must be flagged per trade |
| Spread per bar | available but FXCM-specific, with zero artefacts → cost model must stay `exec_profile` (IC) plus FXCM as sensitivity only |
| Tick volume | **not stored** in the FXCM files (no `v` column) |
| News calendar overlap | calendar starts 2021-09-26 → news-aware tests on FXCM limited to 2021-10 → 2026-06 regardless of price history |
| Broker server-time / D1 boundary (NY 17:00) | UTC bars, resampling must reproduce the broker day (parallax/cta use NY-close D1) |

## Limitations
1. Different liquidity source and feed from IC Markets: fills, gaps and spikes can differ even with matching closes.
2. **Missing weeks after 2022.** A test must skip trades whose holding window touches a hole, never fill.
3. No gold.
4. No tick volume.
5. Spreads unusable as IC costs.

**Before any FXCM backtest is approved:** a loader that reports holes per trade, plus a pre-registered decision on whether 2023-2026 FXCM data is used at all, or IC Markets data instead for the pairs that have it.


---
**ERRATA (2026-11-03, C7):** the "hours lost" / weekday-hole figures exclude missing whole weeks and understate gaps. Use `coverage_pct_by_year.csv`: 2012-2022 99-100 %, 2023 97 %, 2024 91-94 %, 2025 90-93 %, 2026 64-65 %.
