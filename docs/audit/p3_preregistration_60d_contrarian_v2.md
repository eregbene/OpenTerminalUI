# P3 pre-registration "against the 60-day trend": **version 2** (2026-11-03)

**Supersedes** `p3_preregistration_60d_contrarian.md` (v1, kept unchanged, md5 43059c0c6936daa28e714a3b25f45026). **NOT approved to run.**

## Changes from v1 (reasons in `CORRECTIONS_2026-11-03.md`)
| # | v1 | v2 | Reason |
|---|---|---|---|
| 1 | "USDCAD before 2024-02 includes the Dukascopy fill (reported separately)" | **USDCAD M5 exists only from 2024-02-06.** In the P3 period (2021-10-01 → 2026-06-30) USDCAD contributes ≈ 29 of 57 months (2024-02-06 → 2026-06-30). Nothing is filled. Leave-one-symbol-out (criterion D) is reported **with and without** USDCAD | C2 |
| 2 | Costs: `observed_account` (Friday close for all) | unchanged, now explicitly correct for **every** book: the live engine closes all bensim books on Friday 20:00 UTC | C1 (v1 was already consistent; the reason is now documented) |
| 3 | Sample estimate: none per strategy | revised below | C2 |

Everything else in v1 is unchanged: hypotheses H1/H2, the exact `with60` definition, strategies and frozen params, metrics, acceptance criteria A–E, outcomes, run plan.

## Revised sample estimates (estimate only; rates from P2 v1, Aug–Nov 2026)
- **Symbol-months:** EURUSD, GBPUSD, USDJPY, USDCHF 4 × 57 = 228, plus USDCAD ≈ 29 → **≈ 257**.

| Strategy | Rate per symbol-month | Expected trades | Per group (≈ half) |
|---|---|---|---|
| fib618 | 5.11 | ≈ 1,310 | ≈ 655 |
| imbalance | 4.44 | ≈ 1,140 | ≈ 570 |
| enigma | 1.33 | ≈ 340 | ≈ 170 |
| bennett | 0.28 | ≈ 70 | ≈ 35 |
| **all four** | | **≈ 2,870** | **≈ 1,430** (requirement: ≥ 433 per group and ≥ 250 trade days per group, met) |

**Effect on criterion C** ("difference > 0 in ≥ 3 of the 4 strategies"): Bennett (≈ 35 per group) can't give a stable sign.

**Fixed now, before any result:** a strategy with < 100 trades in either group counts as "**no vote**". Criterion C then becomes "> 0 in at least 3 of the strategies that vote, and at least 3 must vote". If fewer than 3 vote, C is "inconclusive" and the overall outcome is "inconclusive".
