# Night research log (2026-11-06 → 07)

Rules: pre-registered tests only, real costs, DISC 2012–18 → VAL 2019–22 → LOCK 2023–26, multiple-testing correction, and **no forced results**.

## 1. Hour-of-day scan (25 pairs × 24 h × 2 directions × 2 holds = 2,400 tests, UTC and London clocks)

**First run (`r8_hour_scan_results.json`): 5 / 42 "PROVEN".** Every one of them sat at the daily rollover:
- crosses long 17–18 London → 20–21;
- crosses short 21:00 London → 00:00.

**ARTIFACT.** The bar OPEN mid was built as bid open + half of the spread at the bar CLOSE. Rollover-hour bars close with a huge spread, so their "open" was inflated. That faked long exits and short entries at rollover. The crosses with the widest rollover spreads "won" most.

**Fixed (`r8_hour_scan_exact_*`):** exact quotes only. Bar closes are used; buy at the ask (bid close + spread at that close), sell at the bid.
- **0 / 2,400 survive BH**, on both clocks.
- **The top clean test is Flow AM itself** (EURUSD short 09 UTC, 3 h): DISC +1.54 bp (t 3.4), VAL +1.90, LOCK +0.38. This is consistent with the round-4 finding (deteriorating), and it does not survive correction for 2,400 tests.

**Knock-on:** `r6_box.bars()` uses the same open construction. Of the round-6 tests that used opens, gotobi, turn-of-month and news had already failed, so nothing is rescued. **Carry (X6 / r6f) uses month-start opens, often the Sunday rollover bar, so it must be re-checked with exact quotes (TODO).**

## 2. Carry with exact quotes (`round6/r6f_carry_exact.py`)
Essentially unchanged: markup 0.7%, flat size, +12.1 / +16.2 / +44.5 bp per month (t 0.5 / 0.5 / 1.7), +2.57%/yr, Sharpe 0.35, max DD −16%. The open-construction bug did not drive carry. Still weak / "promising", with crash risk.

## 3. Factor momentum (`r8c_factor_momentum.py`, Zhang 2022 idea)
- **Dollar-factor TSMOM, weekly:** negative for k = 21, 63 and 126 (−0.35, −1.58 and −3.71%/yr). FAILED.
- **Carry timing** (hold only after a positive 3-month carry): +1.58%/yr vs +2.35%/yr when always on. Timing HURTS; crashes are not predictable this way. FAILED.

## 4. New 2y yields for GBP / CHF / NZD
- **Sources:** BoE nominal spot curve, SNB rendoblid 2J, RBNZ B2 (two files) → `data/macro/yields_ext/yields_2y_plus.csv` (a new file; the live `yields_2y.csv` is untouched).
- **Coverage:** CHF ends 2025-07 and NZD 2025-08 (thin in 2014 and 2020). AUD: the RBA blocks downloads (403).
- **Flow ML retrained with them** (`r8_flowml_yields.py`): running.

## 5. Intraday momentum into the London fix (`r8d`, Elaut et al. 2018)
- M1 (21:00 → 15:00 signal, trade 15 → 16 UTC): basket −1.41 / −0.86 / −1.16 bp.
- M2 (21 → 09 signal, trade 09 → 12): −1.24 / −0.62 / −1.01 bp.

All 52 pair tests FAILED: no signal before costs either.

## 6. Refining the real edges (`r8e`)
- **Flow AM timing grid**, entries 08 / 09 / 10 × exits 11 / 12 / 13 UTC: the live 09 → 12 is already the best on DISC, and also the best in LOCK (+0.38 bp). Every variant is weak in 2023–26, so the edge faded rather than moved. No change.
- **Volatility-managed sizing** (Moreira & Muir 2017): Flow ML Sharpe 1.58 → 1.69 (2016–20) but 0.87 → 0.68 (2021–26); Flow AM worse in every split. FAILED; keep flat 0.5% risk.

## 7. S&P 500 big day → JPY crosses next 24 h (`r8f`)
Continuation and reversal, both directions: all FAILED (18–61 events per split, signs flip between periods).

## 8. Flow ML with the new GBP / CHF / NZD yields (`r8_flowml_yields.py`, 3 seeds, walk-forward 2016–26)
- **14 pairs that gained yields** (no late hours, global threshold): 2021–26 goes from −0.03 bp (no yields, round 7) to **+0.59 bp** (t 0.8); 2016–20 +1.45 → +1.48. Real but weak.
- **LIVE5** unchanged (+1.67 bp; 2021–26 +1.04).
- **AUD pairs** (no AUD yields; the group is defined by data availability, not performance): **+1.24 to +1.79 bp per trade in both runs, t 1.9–3.1, positive in 10–11 of 11 years**; 2021–26 +0.86 to +1.20 bp (t 0.8–1.6). That is the same quality as the live 5 (2021–26 +1.0, t 1.1–1.3). AUDJPY, AUDUSD and EURAUD are strongest; AUDCHF is negative.
- **Status: PROMISING, not proven.** Recent years are not significant on their own (also true of the live 5).

## 9. Flow ML holding period 12 h / 24 h (running, `r8g`)

### 8b. Portfolio: LIVE5 vs LIVE5 + AUD pairs (Flow ML, per-pair threshold, no late hours)
| run | half | LIVE5 daily Sharpe | LIVE5+AUD daily Sharpe | total bp |
|---|---|---|---|---|
| r7 | 2016–20 | 1.37 | 1.27 | 6,571 → 10,844 |
| r7 | 2021–26 | 0.47 | **0.70** | 2,491 → 6,278 |
| r8b | 2016–20 | 1.07 | 0.87 | 5,572 → 8,263 |
| r8b | 2021–26 | 0.59 | **0.92** | 3,271 → 8,648 |

Recent years improve, mostly through diversification (more independent trades); the early years are slightly worse. **PROMISING** → shadow-log the AUD Flow ML signals live (no orders).

## 9. Flow ML holding period (r8g, same model, extended yields)
- **LIVE5 and AUD pairs: 6 h is best.** 12 h / 24 h are weaker or negative; the live setting is right.
- **The 14 pairs that GAINED yields improve with longer holds**, positive in all 12 threshold / hour versions: 6 h +0.42..+1.02, 12 h +1.27..+1.58, 24 h +1.23..+3.06 bp.
- **After overnight swap** (yield difference − 0.7%/yr broker markup, Wednesday ×3):
  - 12 h per-pair threshold: **+1.54 bp (t 1.7)**, 2016–20 +0.96, 2021–26 +2.02, 9/11 years;
  - 24 h global: +1.96 (t 1.3).
- Economic logic: rates drive slow moves. **PROMISING**, but it is the best of 36 versions, so not proven.

## 10. Candidate "Flow ML v2" portfolio (walk-forward 2016–26, net of swap, no late hours)
| portfolio | trades | bp/trade | daily Sharpe | 2021–26 Sharpe | max DD bp | years + |
|---|---|---|---|---|---|---|
| live (5 pairs, 6 h) | 5,033 | +1.69 | 0.79 | 0.55 | −1,759 | 9/11 |
| **+ AUD 6 h** | 10,894 | +1.49 | **0.86** | **0.88** | −2,659 | **11/11** |
| + AUD + rate pairs 12 h | 18,492 | +1.44 | 0.82 | 0.94 | −5,102 | 10/11 |

## 11. Built: the Flow ML AUD SHADOW book (`intel/flow_shadow.py`, in the advisor, read-only)
- Scores the 6 AUD pairs every H1 close with the LIVE model and the live engine's own functions.
- Records signals above the live threshold (no late hours, one per pair) and resolves them after 6 h.
- Tests: 3 new (rule, late hours, no order path, append-only); full suite 359 passed.
- Smoke test on the last market hour (Fri 21:00 UTC, a late hour, so correctly no signal): AUDJPY / AUDUSD were above the threshold.
- Starts collecting forward evidence at Sunday's open.

## 12. Live 5: global vs per-pair threshold
Mixed: per-pair is better per trade in round 7 (2016–20) but worse daily Sharpe in round 8 (2016–20). Neither is better in both. **Keep the live global threshold.**

## 13. ATR-expansion filter for Flow ML (the strongest finding)
- **5-seed confirmation of the AUD pairs:** +1.39 to +1.47 bp, 2021–26 +0.93..+1.68, 9–10/11 years.
- **ATR6/48 > 1.2 at the decision** (rule from the round-4 audit of the live 5 pairs):
  - live 5: +4.32 vs +0.02 bp (Sharpe 1.05 vs 0.72, DD −773 vs −1,977, 11/11 years);
  - **20 unseen pairs, 3 runs:** expanding +1.80..+2.11 vs rest +0.62..+0.77 bp, one-sided p 0.012 / 0.045 / 0.058, expanding better in 6–8 of 11 years;
  - monotonic over thresholds 1.0–1.4.
- **Live DEMO:** 23 sent trades, inconclusive.
- **Does it generalise to Flow AM?** Mixed (DISC worse, VAL/LOCK better), so it is specific to the Flow ML signal rather than a general effect.
