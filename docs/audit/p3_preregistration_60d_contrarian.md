# P3 pre-registration: "against the 60-day trend" (NOT approved to run)

Status: **draft for review, 2026-11-03.** Don't run it until the user approves this text. Once approved, it's frozen: any change makes it a new experiment and is logged in the registry.

## Origin (why this is a hypothesis, not a finding)
In `tm_check` (Jul 26 – Oct 26 2026, fib618/imbalance/enigma/Bennett, 5 FX pairs, 155 trades), the day-trader-style Time Machine split trades into favoured (−0.20R) and opposed (+0.73R).

The audit then showed that the split is ≈ "with / against the trailing 60-day trend" (74 % agreement):
- against: +0.61R (75 trades)
- with: +0.04R (80 trades)

It was found after ≥ 6 splits on a window that had already been used to pick the live strategies. It's concentrated in GBPUSD and 3 weeks, with a day-clustered t of 2.07. Discovery window: **2026-07-01 → 2026-11-03**, never to be used for testing.

## Hypotheses
- **H1 (primary).** Among trades from the frozen strategy set, the mean net R of trades **against** the trailing 60-day trend is higher than that of trades **with** it. Test: one-sided, difference > 0.
- **H2 (secondary).** Trades against the trend have mean net R > 0.
- **H0.** Difference ≤ 0 (H1), mean ≤ 0 (H2).
- **No other variants** are part of this experiment: no other lookback (20/30/90 days), no threshold on trend size, no per-pair choice. The Time Machine's own split (repo mode `rp_avg_r > 0`, 2,000 replays, seed 19) is reported **for information only** and doesn't decide anything.

## Exact definitions
- `with60 = sign((C_last − C_60) × dir) > 0`, where:
  - `C_last` = close of the last completed M5 bar before `t_entry`
  - `C_60` = close of the first M5 bar at or after `t_entry − 60×86400`
  - mid/bid as stored in `candles`
  - a zero difference → excluded and counted
- Net R = `r` from `bensim.backtest` (spread, slippage, commission, swap).

## Data and setup (frozen)
- **Period:** 2021-10-01 → 2026-06-30 (MT5 ICMarkets M5, UTC), all of it before the discovery window.
  - Honest caveat: these years were used before for **strategy-level** results (fib618 5y, imbalance 5y, enigma 5y, Bennett setups), but this split was never computed on them.
  - USDCAD before 2024-02 includes the Dukascopy fill, which is reported separately.
- **Strategies and params:** fib618 = `weekly_check.LIVE`, imbalance = `books.IMB`, enigma = `books.ENIGMA`, bennett = `bennett_strategy.LIVE`. These are the discovery set; no additions.
- **Symbols:** EURUSD, GBPUSD, USDJPY, USDCHF, USDCAD (the discovery set). XAUUSD is reported as a separate secondary result.
- **Costs:** primary `exec_profile("observed_account")` (Friday close, observed swap per lot-night, observed spread, $0 commission). Sensitivity: `exec_profile("legacy")`. The conclusion must hold under the primary profile.

## Sample requirements (set before running)
- **Power.** Assume the true difference is half the discovered one (winner's curse): δ = 0.28R, SD of R ≈ 1.3R. 80 % power at one-sided α = 0.01 needs n ≈ 2·(2.326+0.842)²·1.69/0.0784 ≈ **433 trades per group**.
- **Minimum to evaluate:** ≥ 433 trades in each group **and** ≥ 250 distinct trade days in each group. If not met, the result is "inconclusive"; it doesn't pass and it doesn't fail.

## Metrics
1. **Primary:** difference of mean net R (against − with). One-sided p from a **week-block bootstrap** (resample whole calendar weeks of trades, 10,000 paths, seed 19).
2. Mean net R of the against group, with a week-block bootstrap p (H2), plus its DSR (N = registry trials at run time).
3. **Consistency:** sign of the difference per year (2021Q4 counted with 2022), per strategy, per symbol, and per profile (observed / legacy).
4. **Leave-one-out:** difference with each symbol dropped, and with each strategy dropped.
5. For information: the Time Machine repo split; overlap between with60 and the TM split.

## Acceptance criteria (all must hold for "supported")
- A. Primary difference > 0 with one-sided week-block p **< 0.01**.
- B. The against group's net mean > 0 with p < 0.05 (H2).
- C. Difference > 0 in **≥ 4 of the 5 years** and in **≥ 3 of the 4 strategies**.
- D. Leave-one-symbol-out and leave-one-strategy-out: difference stays > 0 every time.
- E. All of the above under the primary cost profile.

**Outcomes:**
- **Supported:** the most it allows is a separate, pre-registered **forward paper** test. No live change.
- **Rejected:** the hypothesis is closed. No respecification (another lookback, another subset) on the same data.
- **Inconclusive:** sample too small. Report and stop.

## Run plan (after approval only)
- One command: `python -m bensim.p3_contrarian`, written after approval. Runs at BELOW_NORMAL priority; about 4 strategies × 5 years × 6 symbols.
- One registry entry, plus a reproducible report in `reports/experiments/p3_<date>/`.
- Nothing touches live or paper.
