# R1 / R2 pre-registration (fixed 2026-11-03, before any R1/R2 trade or label was generated)

Approved by the user 2026-11-03. Any change after this point is a new experiment and is logged in the registry.

## Datasets (same for R1 and R2)
| Role | Period (entry time, UTC) | Status |
|---|---|---|
| Discovery | 2026-07-01 → 2026-11-03 | already seen (tm_check, P2, forensic); used for description only |
| **Validation** | **2021-10-01 → 2024-03-31** | the R1/R2 statistics were never computed here; strategy-level 5y results were seen before (an honest caveat) |
| **Confirmation** | **2024-04-01 → 2026-06-30** | read **once**, only after the validation section is written and frozen; same code, no changes |

- Candles: DB `candles` M5 (ICMarkets MT5, UTC); USDCAD before 2024-02 is the Dukascopy fill. Gaps aren't filled.
- Calendar: DB `economic_events` (Forex Factory, scraped weekly, UTC-calibrated on the NY 08:30 anchors). 2021-09-26 → 2026-10-18, 53 weeks every year, 0 duplicates. High impact only.

## Trades (same for R1 and R2)
- **Strategies, frozen:**
  - fib618 = `weekly_check.LIVE`
  - enigma = `books.ENIGMA`
  - imbalance = `books.IMB` (R2 only)
- **Symbols:** EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD, XAUUSD.
- **Costs:** `exec_profile("observed_account")`. Spread and commission observed 2026-11 and applied to the past = **estimated**; swap per lot-night observed, applied to the past = **estimated**.
- **Friday rule as live:** fib618 closes Friday 20:00 UTC; enigma and imbalance hold weekends.

## R1: news exposure
**Labels are computed from entry time and the calendar only, before any outcome is joined:**
- `news24` = a high-impact event for either currency of the pair (XAUUSD → USD; currency "All" counts for every pair) with time in (t_entry, t_entry + 24 h].
- Currency flags, each in (t_entry, t_entry + 24 h]: `usd24`, `eur24`, `other24` (the pair's non-USD, non-EUR currency: GBP, JPY, CHF, CAD).
- **Descriptive only (depends on the outcome, never tested):** `news_held` = an event between entry and the actual exit. `exit_at_news` = exit within 30 min after an event.

**H1 (primary).** For fib618 and for enigma separately: mean net R(news24) − mean net R(no news24) > 0. One-sided, week-block bootstrap (resample whole calendar weeks of trades, 10,000 paths, seed 19). Holm over the 2 strategies.

**H1b (concentration).** The share of the top-decile-R trades with news24 is above the share among all trades. Null: shift the whole calendar by k whole weeks (k = ±1…±26, 52 shifts), recompute the labels, and get the one-sided p as the fraction of shifts with a share ≥ the real one.

**Always reported:** each group's mean, total, PF and 95 % CI; the same **without the top 1 % of trades** and **without the top 5 winners**; by year; by pair; by currency flag.

**Outcomes:**
- **Supported:** validation Holm p < 0.05 **and** confirmation one-sided p < 0.05 with the same sign.
- **Rejected:** validation p ≥ 0.05, or confirmation doesn't replicate.

Either way, it's **research knowledge only**. No filter is deployed.

## R2: strategy vs Time Machine noise
**Matched null for every trade.** `timemachine.replay` in bensim mode:
- pool = the 26 weeks of M5 before entry, hour-of-week 1 h blocks, drift removed;
- 2,000 replays, seed 19;
- **the trade's own** symbol, direction, entry price, stop, target and start time;
- the **same spread model** (`observed_account`);
- horizon = the trade's own time limit: the time of the 600th M5 bar after entry (200 M15 bars), and for fib618 also Friday 20:00 UTC.

So reward:risk, direction, stop distance and target distance match exactly, trade by trade, on the same market data.

**Per trade:**
- `excess_hit` = 1[exit = target] − TM P(target first).
- `excess_R` = r_gross (after spread and slippage, before commission and swap) − TM mean R.
- `excess_R_net` = r_net − (TM mean R − commission_r + swap_r).

**H2 (primary).** For fib618, enigma and imbalance separately, mean `excess_hit` > 0 and mean `excess_R` > 0. One-sided, week-block bootstrap 10,000 / seed 19, Holm over the **6 tests** (3 strategies × 2 metrics).

**Also reported:**
- calibration (TM P(target) quartiles vs the real hit rate);
- by year and by pair;
- AUC of the TM P(target) vs AUC of 1/(1+RR), to see how much is geometry.

**Outcomes:**
- **Edge beyond noise:** validation Holm p < 0.05 **and** confirmation p < 0.05 with the same sign, for that strategy and metric.

**Sample (expected):** fib618 ≈ 350 trades/yr → about 900 in validation and 800 in confirmation. To detect a 3-point `excess_hit` (SD ≈ 0.45) at 80 % power, α 0.05, needs ≈ 1,400. **Power is limited (~60 %)**: a null result means "not shown", not "no edge". Enigma (≈ 100/yr) is underpowered for small effects.

## What can't happen
- Re-running confirmation with changed code.
- Choosing pairs, years or thresholds after seeing validation.
- Deploying filters.
- Running R3–R7 or P3.
