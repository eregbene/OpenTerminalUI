# Round 3: consolidated research report (2026-11-06)

**Bottom line:** no statistically defensible trading edge was found. Of everything tested in rounds 1–3, nothing meets the PROVEN bar and nothing tradable meets the PROMISING bar either. Only the move-size model counts as PROMISING, and it predicts size, not direction. Three directional ideas look interesting enough for a frozen forward paper test: they were consistent in recent years, but they all failed the pre-registered checks (§D).

The plan was written down in `PREREG.md` before any result, including a log of the coding fixes. All files are in `D:\Devops\trading\OpenTerminalUI\reports\round3\`. No live, DEMO or engine changes were made during this round.

## Setup

**Data:** `r3_panel.pkl`, built by `r3_build.py`:
- 25 FXCM pairs, H1, 2012-01 to 2026-10;
- 2,194,618 rows × 121 columns;
- real spreads.

**Feature check:** every new feature passed a truncation test (bar i is identical when later bars are removed).

**Reused code (found via graphify, not rewritten):**
- `indicators.structure` (BOS/CHoCH);
- `indicators.fvgs`;
- `indicators.equal_levels`;
- `indicators.day_levels`;
- `fxcm_history.load`.

**Splits:**

| Split | Years | Use |
|---|---|---|
| DISC | 2012–18 | Choosing anything |
| VAL | 2019–22 | First confirmation |
| LOCK | 2023–26-10 | Final confirmation |

**Costs:**
- the real spread, half at entry and half at exit (so the wide rollover spreads are included);
- plus 0.3 bp slippage;
- plus 0.5 bp per day of swap.

**Statistics:**
- week-clustered t;
- one open trade per pair per rule;
- Benjamini–Hochberg correction over every discovery-period test.

**Experiment registry:**

| File | Contents |
|---|---|
| `r3_registry.csv` | All 550 rule tests, all splits |
| `r3_ml_results.json` | Areas E, G, H |
| `r3_trades_results.json`, `r3_trade_conditions.csv` | Area F |
| `r3_ccyrank_results.json` | Currency-ranking reproduction |

## A. Discovery ranking

| Rank | Finding | Type | Evidence | Grade |
|---|---|---|---|---|
| 1 | **Volatility predicts move size** | Predictive (size only) | Out-of-sample rank correlation 0.34 in both test periods; volatility expansion doubles the chance of a top-5% move | PROMISING, not a direction edge |
| 2 | **Short-term reversal of the last bar** | Predictive, below cost | The first split of the interpretable tree is the last bar's body; fading a 2-ATR bar beats following it by about 1.6 bp, while costs are about 1.8 bp | Real but not tradable |
| 3 | **Model currency ranking**, 2 strongest vs 2 weakest, 00/12 UTC, 12h | Predictive in 2019–22 | 2019–22: +2.94 bp, t 3.7, PF 1.23, positive every year. 2023–26: +0.39 bp, t 0.5. At 06/18 UTC: +0.7 bp even in 2019–22 | Failed the final period |
| 4 | **Interactions matter more than single variables** | Structural | 2019–22: the full model reached IC 0.031, the same model with no interactions 0.003. 2023–26: 0.014 vs 0.010 | Descriptive |
| 5 | **Fade a high-volatility 24h overextension** (C04 reversed) | Unconfirmed | 2012–18: ≈0 (t 0.0–0.4). 2019–22: +13 bp at 12h (t 1.5). 2023–26: +6.5 bp (t 1.7). Positive in 80% of years | Failed (discovery period) |
| 6 | **Quarter-end 15:00 UTC reversal of the 120h move** (D07 reversed, 24h) | Unconfirmed, flow story | +1.9 / +7.8 / +9.5 bp across the three periods (t 0.4 / 1.6 / 1.7). About 100 trades a year | Failed (discovery period) |
| 7 | Scoring: the top decile of the score is the only one that is net positive | Ordinal | +1.2 / +0.8 bp, but trading it without overlap gives +1.2 bp (t 1.2) and +0.3 bp (t 0.5) | Not enough to beat costs |

**Failed: all 550 rule tests (55 rules × 2 directions × 5 outcomes).** The best discovery-period p was 0.041, and after correction nothing survives. Both directions of almost every rule lose roughly the round-trip cost (about 1.4–2.1 bp), so the rules carry almost no direction information.

**Gross direction information by area (12h, before costs):**
- **A, currency strength:** raw momentum ranking, and all ten conditional versions, are flat to slightly negative before costs. Only the *model-predicted* ranking showed anything (row 3). Strength does not identify reversals either; the reversed versions also lose.
- **B, structure, liquidity and FVGs:**
  - BOS and CHoCH (internal and swing);
  - sweeps of the previous day, previous week, Asian, London and equal highs/lows;
  - sweep + displacement, sweep + CHoCH;
  - failed breakouts and previous-day breakouts;
  - FVG first retest, FVG retest + H4 trend, FVG with displacement, sweep + FVG retest.

  All of these are within ±1 bp before costs, and none is consistent across the three periods.
- **C, volatility:** compression breakouts, Bollinger closes, momentum acceleration and ATR expansion all fail. Only the overextension fade (row 5) shows anything.
- **D, sessions and time:**
  - Asian-range breakout at the London open, the London move at the New York open, the week-open gap, Friday afternoon and month-end all fail.
  - Quarter-end is row 6.
  - The rollover hour is the most expensive hour of the day.
- **I, new patterns:** streaks, NR7 breakouts, wick rejections, a pair lagging its currency's strength, and trend acceleration all fail. A12 (exhaustion divergence) fired almost never (0.001% of hours), so it is reported as insufficient sample.

## B. Strategy candidates

There are no validated strategies. These are the candidates for **forward paper testing only**:

| | C1: model currency ranking | C2: high-volatility overextension fade | C3: quarter-end reversal |
|---|---|---|---|
| Entry | 00:00 and 12:00 UTC: an HGB model (round-3 features, fixed settings, retrained yearly on all earlier data) scores each currency. Buy the 2 strongest against the 2 weakest, using whichever of those pairs exist among the 25 (up to 4). | H1 close where the volatility percentile is above 0.8 and the 24h move is above 2.5 sd. Trade against the move. | Last calendar day of the quarter, 15:00 UTC. Trade against the sign of the 120h move, on all 25 pairs. |
| Exit | 12h time exit | 24h time exit | 24h time exit |
| Stop / target | None. A 1 ATR stop / 2 ATR target bracket was tested and is worse. | None. The bracket is ≈0R. | None |
| Costs | About 1.4–1.8 bp in the test, net of the real spread | Same | Same |
| Net expectancy | 2019–22: +2.94 bp (t 3.7, PF 1.23). 2023–26: +0.39 bp (t 0.5) | 2012–18: +3.4 bp. 2019–22: +18.9 bp. 2023–26: +15.9 bp (t 0.4 / 1.6 / 1.7). 770 / 468 / 333 trades | 2012–18: +1.9 bp. 2019–22: +7.8 bp. 2023–26: +9.5 bp (t 0.4 / 1.6 / 1.7) |
| Statistical status | Failed the final period | Failed discovery; post-hoc | Failed discovery; post-hoc |

- **The ML models (area E):**
  - The best result per period does not repeat. The full gradient-boosting model made +2.95 bp in 2019–22 (p 0.06) but −0.55 bp in 2023–26. Random forest was +0.96 bp in 2019–22 but +2.45 bp in 2023–26 (p 0.02). That is one lucky pass out of 12 model × period tests.
  - The candles-only baseline was +1.12 / −0.77 bp.
- **Portfolio (area H):**
  - Plain momentum strongest-vs-weakest loses −2.1 bp per trade (2019–22) and −2.8 bp (2023–26).
  - A cap of 4 open trades and 2 per currency halves the drawdowns but also cuts the per-trade result. In 2019–22 the full model's trades fell from +2.95 to +0.76 bp, because the cap mostly drops simultaneous good signals.
  - Currency exposure is already spread out: the largest single currency is about 16% of trades, mostly USD and JPY.

## C. Intelligent module architecture (specification only, based on the evidence)

| # | Component | What the research supports | Specification |
|---|---|---|---|
| 1 | Market data intelligence | Required | H1 panel with real per-hour spreads, daylight-saving-aware sessions, causal joins only. Daily macro data from the next day. COT from report + 7 days. Events from release time. |
| 2 | Currency strength engine | Model ranking only, under forward test | 8-currency score from the model's pair predictions (not raw momentum, which is negative). Decision grid 00/12 UTC. |
| 3 | Market structure engine | Descriptive only | Keep `indicators.structure` as context and for chart display. **Not** a signal: there is no direction value net or gross. |
| 4 | Liquidity and imbalance engine | Descriptive only | Levels (previous day/week, Asian, London, equal highs/lows, FVG zones) for planning and invalidation. No entries from them. |
| 5 | Volatility and opportunity engine | **Best-supported component** | Expected-move model, already IC 0.34 out of sample. Use it to block trades whose expected move cannot cover cost, and for sizing and target distance. |
| 6 | Direction prediction engine | Weak | HGB with interactions. AUC only 0.51–0.52. Probabilities are over-confident (predicted 0.44–0.57 vs realised 0.48–0.54) and need recalibration. |
| 7 | Probability and scoring engine | Ordinal only | E[net] = (2p − 1) × E\|move\| − cost. P(target) and P(stop) are calibrated (top decile predicts 35.6% vs 34.0% realised) but carry little information (AUC 0.54 / 0.52). Bands TRADE / MONITOR / REJECT: REJECT is reliably negative (−1.2 / −1.6 bp per hour, t −7.5 / −10.5). TRADE is positive but not significant. |
| 8 | Risk and trade management | Keep it simple | Fixed time exits. Breakeven, trailing, ML exits and brackets were never proven (round-2 lifecycle + C04 bracket). Block wide-spread hours. |
| 9 | Portfolio intelligence | Exposure caps | Max 2 trades per currency, reported by currency. Don't expect caps to add return. |
| 10 | Decision engine | Veto-first | The clear evidence is negative: what NOT to trade (low expected move vs cost, rollover hour, REJECT band). Positive entries only from forward-validated candidates. |

## D. Forward paper testing (frozen, no live orders, no combining)

1. **C1:** model currency ranking, 2v2, 00/12 UTC, 12h. Model settings exactly as in `r3_ml.HGB`, retrained once a year on all prior data.
2. **C2:** high-volatility overextension fade (`vol_pct > 0.8`, `|ret_24| > 2.5`, against, 24h).
3. **C3:** quarter-end 15:00 UTC reversal, 24h. Only about 4 events a year, so this needs years of forward data.
4. **Veto filter:** the opportunity score's REJECT band, used to log what would have been blocked in the existing books.

**Rules for all four:**
- Shadow logging only.
- Parameters frozen as above.
- Kill criteria set in advance: after 300 trades (C1, C2), net ≤ 0 or t < 1.0.

## E. Verdict

1. **Did we discover a statistically defensible trading edge?** No. Nothing tradable passes the corrected, three-period validation.
2. **What is genuinely predictive?** Move size, from volatility (strongly). Short-term reversal, weakly and below cost. Model-based currency ranking in 2019–22 only.
3. **What is merely descriptive?** Market structure (BOS/CHoCH), liquidity sweeps, FVGs, session levels, COT, cross-asset data, economic events, and regime clusters.
4. **What is profitable after realistic costs?** Nothing that has been validated.
5. **What stays stable across market conditions?** The move-size prediction, and the fact that both directions lose the cost. The best conditions for direction flip between periods.
6. **Does combining strength, structure, liquidity and volatility help?**
   - In a model, interactions carry most of the little signal there is: in 2019–22 the IC is 0.031 with interactions and 0.003 without.
   - As hand-written combined rules, no.
   - The net gain over candles alone is +0.005 IC in 2019–22 and −0.007 in 2023–26, so the combination is not consistently better.
7. **Can the existing 9,279-trade strategy be improved?**
   - **Not with these filters.** Of 29 conditions, none met the bar on 2012–18 trades (n ≥ 200, |t| > 2), so no filter was selected.
   - **ML trade filter (it backfires):** AUC 0.57–0.58, but it selects high win-rate trades with worse expectancy. In 2023–26 its "best half" made −0.048R and its "worst half" +0.046R.
   - **Baseline:** −0.066R per trade in 2012–18, −0.049R in 2019–22, −0.010R in 2023–26.
   - **Not with management changes:** stop, target and management changes were tested in the lifecycle round and none was proven.
8. **Is an opportunity-scoring engine justified?** Yes as a **veto and position-sizing tool**, built on expected move vs cost. No as an entry generator.
9. **What should be forward-paper-tested first?** C1, the model currency ranking, plus the REJECT-band veto log. C2 alongside. C3 as a long-running log.
10. **What remains unresolved?**
    - **Execution data:** tick-level and order-book data are missing, so true slippage is unknown.
    - **Rates:** G10 2-year yields beyond USD/EUR/JPY/CAD.
    - **Holding time:** time to target or stop could not be estimated, because the panel stores only outcomes.
    - **Untouched data:** no truly untouched historical data is left. The forward test is the only clean judge.
