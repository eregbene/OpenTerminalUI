# Round 4: strategy forensic audit (2026-11-06)

Report only. No configuration, permission, risk, strategy or broker setting was changed, and nothing was paused or activated. Every recommendation below waits for your explicit approval.

**Scripts and outputs** (`D:\Devops\trading\OpenTerminalUI\reports\round4\`):

| File | Contents |
|---|---|
| `r4_live.py` → `r4_positions.csv`, `r4_live_results.json` | Real DEMO performance by position ID, plus real risk |
| `r4_hist.py` → `r4_hist_results.json`, `r4_filter_tests.csv` | Historical profile of each strategy, filter tests, PSR/DSR, block bootstrap, PBO |

All tests were defined in the script headers before running. Failed tests are kept in the CSV.

---

## Part 1: the real DEMO performance (10 Oct to 6 Nov 2026)

**Method:**
- 195 MT5 deals were grouped by `position_id` into 99 positions, counted once each: 96 closed, 3 open, 0 partial.
- Each position takes its strategy from its **opening** deal (magic + comment). That is how a manual close, which MT5 files as magic 0, is attributed.
- Flow ML and Flow AM, which share magic 770103, are split by the opening comment (`flowml` / `flowam`).
- **All 96 closed positions match the engine's own database records** (`live_flow`, `live_books`, `live_signals`, `live_signals_bennett`, `manager_actions`). Attribution needs no manual review.

**Reconciling the "22 manual closes":** these are 22 magic-0 closing deals:
- **19 are your manual closes** (17 mobile, 2 desktop);
- 3 are closes of **"guard test" positions** (magic 999001, opened by a test script, −$0.26 total). Those 3 are not a strategy, and they are the only items flagged for review.

| Strategy | Closed positions | Net $ | Win | Exp. $ | Exp. R | PF | Max DD $ | Avg hold h | Your manual closes ($) | **Engine-only $** |
|---|---|---|---|---|---|---|---|---|---|---|
| fib618 | 27 | −1,261 | 33% | −47 | +0.05 | 0.78 | −2,698 | 11.0 | 5 (+2,257) | **−3,519** |
| Bennett | 2 | +166 | 50% | +83 | +0.34 | 1.68 | 0 | 15.3 | 1 (+409) | **−243** |
| Flow ML | 21 | −191 | 48% | −9 | −0.01 | 0.84 | −884 | 7.0 | 1 (+174) | **−366** |
| Flow AM | 13 | +1,118 | 62% | +86 | +0.05 | 1.89 | −375 | 2.8 | 3 (+1,068) | **+50** |
| imbalance | 13 | +1,421 | 31% | +109 | +0.44 | 1.64 | −1,946 | 3.3 | 3 (+3,116) | **−1,696** |
| enigma | 5 | +742 | 60% | +148 | +0.61 | 2.53 | −243 | 6.6 | 1 (+169) | **+573** |
| sflow | 10 | −1,427 | 30% | −143 | −0.44 | 0.21 | −1,167 | 2.0 | 3 (+378) | **−1,805** |
| parallax | 0 | — | — | — | — | — | — | — | — | — |
| CTA | 1 | +740 | 100% | — | +3.36 | — | 0 | 15.9 | 1 (+740) | **0** |
| manager (Claude) | 1 | +131 | — | — | +0.54 | — | — | 42.8 | 1 | 0 |
| **Total** | 96 | **+1,437** | | | | | | | **19 (+8,442)** | **−7,005** |

**Costs:**
- **Commission $0:** this is a spread-only account.
- **Swap −$229 in total**, mostly fib618 at −$172.
- Fees $0.

**The single most important finding:** the account's whole profit since 10 Oct comes from **your 19 manual closes (+$8,442)**. The engine's own exits lost $7,005.
- This is **not** evidence that the strategies work.
- It may be evidence that your discretionary exits add value. With only 19 closes, nothing can be concluded: we don't know what these trades would have done without your intervention, and closing winners is a selection effect.
- One example: the gold imbalance trade you closed at +$1,495 would be about +$2,015 open today. Its target hasn't been hit.

**Live MAE/MFE** (5-minute snapshots, median R):
- fib618: MFE +0.55, MAE −0.82;
- imbalance: +0.32 / −0.81;
- sflow: +0.39 / −0.62;
- flow ML: +0.07 / −0.10.

The 30 days alone say nothing statistically. As your brief requires, the classification below uses history plus forward data.

---

## Part 2: historical evidence for each strategy (net of costs)

The splits are DISC ≤ 2018, VAL 2019–22 and LOCK 2023–26.

| Strategy (source) | n | Exp | PF | t (week-clustered) | DISC / VAL / LOCK | Max DD | Years + | Pairs + | PSR | **DSR** | Block-bootstrap total, 90% CI |
|---|---|---|---|---|---|---|---|---|---|---|---|
| fib618 (FXCM 2012–20 + IC 2021–26) | 3,266 | −0.051R | 0.93 | −1.75 | −0.03 / −0.10 / −0.06 | −226R | 7/15 | **0/5** | 0.04 | 0.00 | [−314, −18]R |
| Bennett | 205 | +0.146R | 1.21 | 1.07 | +0.05 / +0.35 / +0.27 | −18R | 8/15 | 4/5 | 0.87 | 0.01 | [−16, +74]R |
| Flow ML (walk-forward 2016+) | 5,664 | **+1.58 bp** | 1.14 | **2.69** | +1.93 / +1.45 / +1.30 | −1,643 bp | 8/11 | **5/5** | 1.00 | **0.54** | [+3,511, +14,485] bp |
| Flow AM (rebuilt, FXCM EURUSD) | 3,723 | **+1.37 bp** | 1.24 | **4.64** | +1.54 / +1.90 / **+0.38** | −422 bp | **13/15** | 1/1 | 1.00 | **0.93** | [+3,310, +6,883] bp |
| imbalance | 4,802 | −0.046R | 0.94 | −1.50 | −0.10 / −0.02 / +0.02 | −322R | 8/15 | 3/7 | 0.07 | 0.00 | [−458, +16]R |
| enigma | 1,230 | −0.077R | 0.88 | −1.93 | −0.06 / −0.15 / −0.05 | −103R | 5/15 | 1/7 | 0.03 | 0.00 | [−174, −17]R |
| sflow (entry A, as live) | 1,539 | −0.126R | 0.84 | −2.39 | −0.11 / −0.13 / −0.15 | −248R | 3/15 | **0/7** | 0.01 | 0.00 | [−326, −54]R |
| parallax ("author", −100%) | 1,633 | +0.092R | 1.13 | 1.53 | +0.13 / +0.13 / **−0.04** | −47R | 9/15 | 17/25 | 0.99 | 0.14 | [−19, +327]R |
| CTA (v2) | 1,558 | −0.340R | 0.63 | −3.29 | −0.10 / −0.64 / −0.47 | −749R | 2/15 | 5/25 | 0.00 | 0.00 | [−793, −218]R |

**DSR** (deflated Sharpe ratio, Bailey & López de Prado 2014) is computed with 2,200 trials, a conservative count of every experiment in rounds 1–4. Only Flow AM (0.93) comes close to the usual 0.95 bar, and only over its full history.

**PBO** (CSCV, Bailey et al. 2017) measures overfitting from choosing between variants:
- parallax, 18 variants: 0.00, so the variant choice is not an overfit;
- CTA, v1/v2: 0.20;
- sflow, entry types: 0.20.

### Strategy-by-strategy findings

**fib618: no demonstrated edge.**
- Negative in all three periods; **no pair is positive**.
- Gold is no different from FX (−0.09R).
- The best regime, low volatility with a medium trend, is only +0.05R. The worst is low volatility with a strong trend (−0.28R).
- No session is positive.
- **What's left:** testing whether each confirmation (killzone / POI / sweep) adds information independently. The historical trades don't record which confirmations fired, so this needs a backtest batch with each confirmation switched on or off, about 2–4h of CPU (see §4.2). It isn't run yet. Given 0/5 pairs and DSR 0, it is unlikely to rescue the strategy.

**Bennett: insufficient data, with a positive lean.**
- +0.146R over 205 trades with t 1.07. VAL and LOCK are both positive (+0.35 / +0.27).
- **DSR 0.01:** with this many experiments behind it, 205 trades can't separate it from luck.
- Overlap (+0.29R) and Tokyo (+0.35R) beat London (−0.13R).
- GBPUSD −0.34R is the only clearly negative pair.
- The "+0.34R over 67 trades" figure was an earlier, narrower sample.

**Flow ML: promising but unvalidated (the strongest strategy for current data).**
- +1.58 bp per trade with t 2.69 overall, positive in every period, but VAL and LOCK alone are not significant (t 1.5 / 1.1). 2024–26 is +1.88 bp.
- **All 5 pairs are positive;** CADJPY is best (+2.3 bp), USDJPY weakest (+0.7 bp).
- **Session matters a lot:**
  - overlap +4.6 bp;
  - New York +2.6 bp;
  - London +2.3 bp;
  - Tokyo +0.7 bp;
  - late −2.1 bp (that's the rollover-cost hours).
- Regime: high volatility is best (+2.4 to +3.2 bp); low volatility with ranging is negative (−0.9 bp).
- **Live:** 21 trades, −$366 from engine exits, which is within noise for this edge size.
- Feature importance and probability calibration couldn't be analysed: the saved walk-forward trades don't store the model inputs. That is still to do.

**Flow AM: deteriorating.**
- The best long-run evidence of all: 13 of 15 years positive, t 3.4 in DISC and 3.3 in VAL.
- **But it faded:** LOCK +0.38 bp (t 0.8), 2024–26 +0.13 bp. 2024 and 2026 are negative so far.
- It is a London-open EURUSD effect and still positive on average, but the cushion over costs is now thin.
- Live: 13 trades, +$50 from engine exits.

**imbalance: no demonstrated edge, recently improving.**
- DISC −0.10R (t −2.3); LOCK +0.02R and 2024–26 +0.06R, neither significant.
- Positive on USDJPY, USDCAD and XAUUSD; negative on EURUSD (−0.18R) and AUDUSD.
- **Its positive regime is "trend":** +0.22R with medium/high volatility and trend, against −0.30R in the mid/mid regime.
- **Management, not direction, is the issue:**
  - 35% of losers reached +1R first (the highest of all books);
  - 77% of trades exit at the stop;
  - the planned R:R is about 4.9.

**enigma: no demonstrated edge.**
- Negative in every period; only 1 of 7 pairs positive.
- It overlaps with imbalance in concept, but as a sample it isn't a duplicate: 1,230 vs 4,802 trades, H4 vs donchian.
- The one-trade-per-pair rule already stops them holding the same pair at once.

**sflow: no demonstrated edge, and the implemented entry is worse than random.**
- **The live entry A loses to its own placebo** (same geometry, random timing) in every period:

  | | DISC | VAL | LOCK |
  |---|---|---|---|
  | Entry A | −0.11 | −0.13 | −0.15 |
  | Placebo | +0.03 | +0.07 | +0.12 |

  So the structure timing carries **negative** information. Either the BOS-then-limit timing is systematically late (it enters after the move) or the zone logic is inverted.
- **Entry C** (counter-trend lower-timeframe breaks into the higher-timeframe zone) looked positive on the 7 live pairs. It **fails on the 19 pairs it wasn't selected on** (−0.19R, 3 of 19 pairs positive), so it was a selection artifact.

**parallax: deteriorating, and live trading doesn't match the backtest.**
- +0.13R in 2012–22; LOCK −0.04R. 17 of 25 pairs positive.
- **Best regimes:** low/medium volatility (mid/range +0.21R). **Worst:** high volatility (−0.10R). Its one filter that passed the discovery-period correction, "avoid vol percentile > 0.8", did not hold in VAL or LOCK.
- **Design mismatch:** live closes every book on Friday (audit §12.6), but parallax's tested design is a 30-day hold. The live version is a different, untested strategy.

**CTA: no demonstrated edge (strongly negative).**
- −0.34R per trade; only 2 of 15 years positive. 2012 (+6.7R) is the whole story.
- **Filter result:** trading only when H4 + D1 trend agree, or skipping when currency strength or the 24h move is against the trade, is the only filter set confirmed in all three periods (BH-significant in DISC, p < 0.05 in VAL, p < 0.10 in LOCK). Even filtered, CTA stays negative (VAL −0.56R, LOCK −0.36R). The filter cuts losses; it doesn't create an edge.
- The live Friday close also cuts its 120-day trail, the same mismatch as parallax.

---

## Part 3: trade management (historical, R books)

| | fib618 | imbalance | enigma | Bennett |
|---|---|---|---|---|
| Median MFE / MAE (R) | 0.88 / 1.07 | 0.92 / 1.15 | 0.91 / 1.05 | 0.87 / 1.07 |
| Losers never above +0.3R (wrong direction / entry) | 36% | 30% | 33% | 40% |
| Losers that reached +1R, then lost (management) | 28% | **35%** | 24% | 25% |
| Winners that first went −0.5R against (stop too tight?) | 0% | 0% | 0% | 0% |
| Winner capture (R / MFE) | 0.86 | 0.89 | 0.85 | 0.82 |
| Exits stop / target / Friday or time | 67 / 20 / 13% | 77 / 16 / 7% | 63 / 25 / 13% | 66 / 17 / 17% |

**Reading:**
- **Stops are not too tight:** no winner first went −0.5R against.
- **Winners are captured well:** 82–89% of MFE.
- **The losses split into two groups:**
  1. about a third are **wrong-direction entries** that never go anywhere;
  2. a quarter to a third reach +1R and then hit the full stop.
- So management *could* matter (group 2), but the lifecycle round already tested breakeven, trailing, partials, time and stall exits per book across DISC/VAL/LOCK, and **none was proven**:
  - breakeven hurts since 2021;
  - the best locked-period effects are small, p ≥ 0.04 (enigma TIME_24, imbalance STALL_4) and don't survive correction;
  - the high "ML_EXIT" figures are in-sample (a flaw I found and corrected in that round) and must be ignored.
- **Verdict:** management is not the main cause. Entry quality is.

---

## Part 4: intelligence framework (Stage 3 skills, built and tested)

### 4.1 What exists
The deterministic skills are in `bensim_phase20_dev/skills/`:

| Skill | Status |
|---|---|
| Pip value, spread, commission, swap with triple-swap day, slippage, break-even, net expected return | Done |
| Currency strength and dispersion | Done |
| Liquidity, FVG and sweep state | Done |
| Expected move (6/12/24h; out-of-sample IC 0.36 / 0.32 / 0.27, calibrated) | Done |
| Regime, structure (BOS/CHoCH/swings), sessions, volatility, calendar, quality | Exist in `market_intel/` |

The specialist agents are Stage 4 and wait for your go (design: `docs/architecture/MULTI_AGENT_DESIGN_2026-11-06.md`).

### 4.2 Strategy-specific evaluation profiles (for the Strategy Health agent)

| Strategy | What its profile watches | Evidence-backed condition |
|---|---|---|
| fib618 | Fib zone, killzone / POI / sweep combination, structure, gold vs FX | none (0/5 pairs positive); confirmation decomposition still to run |
| Bennett | H4 + 30M agreement, session, invalidation | Overlap/Tokyo > London (descriptive, n small) |
| Flow ML | Session, volatility, expected move vs cost, rate gap, pair | **ATR expanding:** DISC t 2.6, VAL t 1.7, LOCK t 3.2, but not BH-significant → shadow test. **Late-hour (rollover) entries negative** (−2.1 bp) |
| Flow AM | 09:00 London open, EURUSD volatility, news, spread | "24h move with the trade > 1 sd" DISC t 2.2, VAL 2.2, LOCK 0.1 → not confirmed |
| imbalance / enigma | FVG quality, displacement, regime, retest | Trend regime better (descriptive); no filter confirmed |
| sflow | BOS/CHoCH timing vs placebo | Entry worse than placebo → logic needs redesign, not filters |
| parallax | Volatility percentile, range vs trend, BB width | Avoid high vol (DISC only, not confirmed) |
| CTA | H4/D1 trend agreement, currency strength, 24h move | **Confirmed loss-reducers, but CTA stays negative** |

---

## Part 6: external research and how it applies to us

1. **Menkhoff, Sarno, Schmeling & Schrimpf (2012), *Currency momentum strategies*, J. Financial Economics 106(3), 660–684.**
   - Up to 10% p.a. between past winner and loser currencies, but partly explained by transaction costs, with strong limits to arbitrage.
   - **Our data:** raw momentum ranking loses roughly its cost (R3), and CTA (D1 momentum) is strongly negative. That is consistent with "hard to exploit after costs", especially intraday and on 25 liquid pairs.
2. **Zhang (2022), *Dissecting currency momentum*, J. Financial Economics 144(1), 154–173.**
   - Currency momentum is mostly *factor* momentum (carry and dollar factors); idiosyncratic currency returns show little momentum.
   - **Testable for us:** a dollar-factor or carry-factor momentum signal, instead of pair momentum. Note that Flow ML's best features are rate gaps, close to a carry factor.
3. **Eriksen (2019), *Cross-sectional return dispersion and currency momentum*, J. Empirical Finance 53, 91–108.**
   - Dispersion is priced in momentum returns: winners gain when dispersion rises unexpectedly.
   - **Testable for us:** whether the R3 currency-ranking candidate (C1) works better after dispersion rises. Our `strength.py` now gives the inputs.
4. **Bailey, Borwein, López de Prado & Zhu (2017), *The probability of backtest overfitting*, J. Computational Finance 20(4).**
   - We apply CSCV PBO to the variant families above.
5. **Bailey & López de Prado (2014), *The deflated Sharpe ratio*, J. Portfolio Management 40(5), 94–107.**
   - With about 2,200 recorded trials, only Flow AM's full history clears a DSR near 0.95. This is why we don't treat any recent good stretch as proof.

---

## Part 7: real risk (today's prices)

The pip values come from `skills.cost`. The spec status is UNVERIFIED until the broker specification snapshot exists; the values are FX-rate-derived.

| Book | Lots | Stop | $ at the stop | % equity | vs 0.5% base ($496) |
|---|---|---|---|---|---|
| Flow ML EURUSD | 0.8 | 145 bp disaster stop (162 pips) | $1,296 | **1.31%** | 2.6× |
| Flow ML USDJPY / USDCAD | 0.8 | 145 bp | $1,160 | 1.17% | 2.3× |
| Flow ML EURJPY | 0.8 | 145 bp | $1,296 | 1.31% | 2.6× |
| Flow ML CADJPY | 0.8 | 145 bp | $824 | 0.83% | 1.7× |
| **Flow AM EURUSD** | **2.5** | 68 bp (76 pips) | **$1,899** | **1.91%** | **3.8×** |

- The flow stops are disaster stops that are rarely hit. But on a gap or a shock, fixed lots carry 2–4× the risk the other books carry.
- `risk/flow.py` (phase20) already contains a risk-based replacement, tested but not deployed.

**Live concurrency:**
- at most **5 open positions** at once;
- estimated summed stop risk up to **$3,690 (3.7% of equity, 2 Nov 11:00 UTC)**;
- up to **4 simultaneous short-USD legs** and 3 long-EUR / short-JPY legs.

**Proposed portfolio controls (not applied):**
1. Flow lots sized by risk at 0.5%, using `risk/flow.py`.
2. At most 2 legs per currency per side across all books (risk check 11 already implements this; DEMO profile has it off).
3. An aggregate open-risk cap of 2.5% of equity.
4. Duplicate-exposure warnings, e.g. EURUSD long + USDCHF short.

---

## Part 9: strategy health classification

| Rank | Strategy | Class | Why |
|---|---|---|---|
| 1 | **Flow ML** | **B: promising, unvalidated** | +1.58 bp, positive in all periods and all 5 pairs, DSR 0.54; the late-session hours lose |
| 2 | **Flow AM** | **D: deteriorating** | Strongest long history (DSR 0.93, 13/15 years), but 2023–26 is only +0.38 bp and 2024–26 +0.13 bp |
| 3 | **Bennett** | **F: insufficient data** (positive lean) | 205 trades, t 1.07, DSR 0.01 |
| 4 | **parallax** | **D: deteriorating** + live design mismatch | +0.13R to 2022, −0.04R since; live Friday close ≠ the tested 30-day hold |
| 5 | imbalance | E: no demonstrated edge | Negative overall; recent +0.02R to +0.06R is not significant |
| 6 | enigma | E: no demonstrated edge | Negative in every period |
| 7 | fib618 | E: no demonstrated edge | 0/5 pairs, negative in every period, DSR 0 |
| 8 | sflow | E: no demonstrated edge | **Entry worse than its placebo** |
| 9 | CTA | E: no demonstrated edge | −0.34R; filters reduce losses only |

No strategy is **A: historically validated**.

---

## Part 10: recommendations (need your approval; nothing has been applied)

1. **Strategies requiring no changes:** none qualifies as "validated, leave alone". Flow ML is closest; keep it as is, except its risk sizing (item 7).
2. **Strongest evidence-backed improvements:**
   - Flow ML: no new entries in the late hours (rollover, −2.1 bp) **+** the ATR-expansion filter, both as *shadow* tests first.
   - Flow lots sized by risk.
   - CTA: the trend-agreement filter. It only reduces losses.
3. **Further research:**
   - fib618 confirmation decomposition (one backtest batch, about 2–4h CPU);
   - Flow ML feature and calibration audit (needs the model inputs saved);
   - sflow timing redesign: the placebo result says the entry is late or inverted;
   - parallax and CTA re-tested **with the live Friday close**.
4. **Restricted allocation candidates:**
   - Flow AM: keep, but at risk-based size (0.5%), not 2.5 lots;
   - Bennett: keep at its half risk; small sample;
   - parallax: either remove the Friday close for D1 books or move it to shadow.
5. **No demonstrated edge, and candidates for SHADOW-only:** fib618, imbalance, enigma, sflow, CTA. Their engine-only exits since 10 Oct net **−$6,447**: fib618 −3,519, sflow −1,805, imbalance −1,696, enigma +573, CTA 0.
6. **Intelligence filters per strategy:** see §4.2. Only the CTA filters are confirmed. All others go to shadow first.
7. **Portfolio risk:** see Part 7 (per-currency cap, 2.5% aggregate cap, risk-based flow sizing).
8. **Forward shadow requirements:**
   - parameters frozen;
   - pre-set kill rules: after 300 trades, net ≤ 0 or t < 1.0;
   - every shadow strategy logs hypothetical trades with the same cost model;
   - real and shadow results are reported separately.
9. **Your manual closes:** log a reason for each one; Claude's manager book does this already. After about 50 closes we can tell whether discretionary exits are a real edge or a selection effect.
10. **Suggested DEMO operating configuration (proposal):**
    - **trade:** Flow ML (risk-sized), Flow AM (risk-sized), Bennett (half risk);
    - **shadow:** fib618, imbalance, enigma, sflow, CTA, parallax (until the Friday mismatch is resolved);
    - **keep:** guard, TradeGuard, broker stops, kill switch, news blackout, Friday close for intraday books;
    - **add:** the portfolio caps from Part 7.

Each item is independent and needs your explicit go.
