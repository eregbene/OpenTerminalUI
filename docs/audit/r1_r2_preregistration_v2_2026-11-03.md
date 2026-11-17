# R1 / R2 pre-registration: **version 2** (2026-11-03)

**Supersedes** `r1_r2_preregistration_2026-11-03.md` (v1, kept unchanged, md5 d28b33b0e5fe7bd69d461a7734c72acb). **Not approved to run.** R1/R2 remain on hold until the user approves this version. No R1/R2 result exists: the v1 trade generator was stopped before writing anything.

## Changes from v1 (reasons in `CORRECTIONS_2026-11-03.md`)
| # | v1 | v2 | Reason |
|---|---|---|---|
| 1 | Friday rule: fib618 closes Fri 20:00 UTC; **enigma and imbalance hold weekends** | **all three close at the first bar from Friday 20:00 UTC, no entries after it** (`exec_profile("observed_account")` unchanged, `friday_close_utc=20` for all) | C1: `live.close_all` closes every bensim book (live.py L142-145, L198-199) |
| 2 | "USDCAD before 2024-02 is the Dukascopy fill" | **USDCAD M5 exists only from 2024-02-06** (Dukascopy/ForexSB to 2024-12-31, IC Markets from 2025-01-01). In validation, USDCAD contributes 2024-02-06 → 2024-03-31 only (~8 weeks). No data is filled or substituted | C2 |
| 3 | Expected samples: fib618 ≈ 900 validation / 800 confirmation | revised below from measured P2 v1 rates | 1 and 2 |
| 4 | Code: `bensim/r12_trades.py` | **must set the Friday close for enigma and imbalance** before any run (C5). The change is to be reviewed with this document | C5 |

Everything else in v1 is unchanged: datasets and split dates, label definitions, H1 / H1b / H2, metrics, bootstrap, Holm sets, the acceptance rules, the "read confirmation once" rule, and the restrictions.

## Revised sample estimates
Trade rates come from P2 v1 `observed_account` (Aug–Nov 2026, live Friday rule): fib618 5.11, imbalance 4.44, enigma 1.33 trades per symbol-month. They're an **estimate**: historical rates may differ.

| Set | Symbol-months | fib618 | imbalance | enigma |
|---|---|---|---|---|
| Validation 2021-10-01 → 2024-03-31 (5 symbols × 30 months + USDCAD ≈ 1.8) | ≈ 152 | ≈ 780 | ≈ 675 | ≈ 200 |
| Confirmation 2024-04-01 → 2026-06-30 (6 × 27) | 162 | ≈ 830 | ≈ 720 | ≈ 215 |

**Power, as fixed before any result:**
- **R1** (difference news − none, assuming a roughly half/half split): detecting 0.3R (SD 1.3) at 80 % power, α 0.05 one-sided, needs ≈ 230 per group.
  - fib618 (≈ 390 per group in validation) is adequate.
  - **enigma (≈ 100 per group) is underpowered.** An enigma null result reads "not shown".
- **R2** (excess hit 3 points, SD 0.45): needs ≈ 1,400.
  - fib618 validation power ≈ 58 %, imbalance ≈ 53 %, enigma ≈ 22 %.
  - A null result means "not shown", not "no edge".
- **Minimum to report a test at all:** ≥ 100 trades in each compared group (R1) or ≥ 150 trades (R2) in that set. Below that the test is reported as "insufficient sample" and isn't counted in Holm.
