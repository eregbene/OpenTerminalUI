# BENSIM BACKTEST REPORT — Baseline v1 (2026-10-08)

**Bottom line: the data shows no edge.** Every tested combination of FVG, HTF structure, liquidity sweep, displacement, supply/demand, premium/discount and Wyckoff context loses money after realistic costs, on 7 instruments, over 8 years, in-sample and out-of-sample. Most are already negative *before* costs. No configuration has a positive expectancy that is statistically distinguishable from zero.

The engine itself was checked: random entries through the same execution code average **+0.02R ± 0.025** gross (zero, as they should), and a lookahead "oracle" averages **+0.69R**. The losses come from the setups, not from a biased simulator.

Code: `bensim_research/` (research only; nothing in the live MT5 path was touched). Raw numbers: `data/research/bensim_report_H.json` (8-year stack) and `bensim_report_M.json` (exact-spec M5 stack). Trade journals and the FVG research database: `data/research/artifacts/bensim/` (gitignored).

---

## 1–4. Strategy, data, instruments, timeframes

| | Stack H (primary) | Stack M (exact spec stack) |
|---|---|---|
| Bias / Location / Setup / Confirmation | D1 / H4 / H1 / M15 (+W1 context) | H4 / H1 / M15 / M5 (+D1 context) |
| Base data | FOREXSB M15 (UTC), with the 2022-08→2026-06 FOREXSB hole filled by MT5 MetaQuotes-Demo M15 converted server-clock→UTC | MT5 MetaQuotes-Demo M5, server-clock→UTC |
| Period | 2018-08 → 2026-08-13 (≈8 years) | EURUSD 2025-04 → 2026-08; others 2025-12 → 2026-08 (≈8 months) |
| Instruments | EURUSD, XAUUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, USDCAD | same |
| DEV / HOLDOUT | DEV < 2024-01-01; HOLDOUT 2024-01-01 → 2026-08 (≈32%, never used for any choice) | too short to split; reported whole |

**Why Stack H shifts the spec stack up one timeframe:** clean 5-minute history only exists for about 8–16 months, and the spec requires at least 12 months across several regimes. Stack H keeps the same 4-tier logic (bias → location → setup → lower-timeframe confirmation) on 8 years of data. Stack M runs the exact 4H/1H/15M/5M stack on the short window as a cross-check.

**Data cleaning** (per symbol, see `symbols` in the JSON): duplicates, off-grid, bad OHLC, spike (>25× median range) and weekend bars removed. Rows after 2026-08-14 were dropped because the candle table contains 2027-dated test rows. D1 bars roll at 22:00 UTC. Sessions use fixed UTC hours: Asia 00–07, London 07–12, Overlap 12–16, New York 16–21; DST is ignored, an error of up to 1h.

## 5–9. Methodology (all deterministic, all causal)

- **FVG** (`features.fvgs`): 3-candle gaps on every timeframe. Each FVG is a row carrying top, bottom, CE, size, ATR-normalised size, displacement (middle-candle body/ATR), body ratio and creation time. It becomes known at the close of candle 3. Lifecycle labels (first touch, 50%, full fill, body-close invalidation, MFE/MAE after touch, respected) are forward-looking research labels only and are never read by signals.
- **Structure** (`features.structure`): fractal pivots with k bars each side, recorded only at confirmation (pivot + k). HH/HL/LH/LL labels. A BOS is a close beyond the latest live swing in the trend direction; an MSS/CHoCH is the same break against the prior trend. External structure = location/bias timeframes; internal = setup/confirmation timeframes, so a bearish 5M swing never overrides the 4H trend.
- **Liquidity** (`features.liquidity`): untaken swing highs/lows on the setup timeframe (tier 1) and location timeframe (tier 2), Asian session high/low (tier 2), previous-day and previous-week high/low (tier 3). Equal highs/lows within 0.1 ATR add +1 tier. A **sweep** is a pierce that closes back inside; a **take** is a pierce that closes beyond.
- **Supply/demand** (`features.sd_zones`): the base candle before a run of ≤3 same-direction candles whose bodies total ≥1.5 ATR. Zone = distal wick to proximal body. Tracks freshness, first touch and violation (close beyond distal).
- **Wyckoff / regime** (`features.regime`): an approximation, not textbook phase detection. A range is efficiency ratio(30) < 0.3. Inside a range the phase is ACCUMULATION after a prior down-move > 3 ATR, DISTRIBUTION after an up-move, else RANGE. Outside ranges, MARKUP/MARKDOWN come from structure trend plus ER ≥ 0.3. Events: SPRING, UPTHRUST, SOS, SOW. Trend regime TREND_UP/DOWN/RANGE/TRANSITION and volatility percentile HIGH/LOW/NORMAL.
- **Premium/discount**: price position inside the location timeframe's latest external swing range.
- **Score**: initial unfitted weights (`strategy.WEIGHTS`), range 0–15.

## 10–12. Entry, stop, target, management

- **Entry models.** A: limit at the FVG near edge (first touch). B: limit at 50%/CE. C (confirmed): after first touch, the confirmation timeframe must sweep one of its own swings, break structure with a displacement candle (body ≥ 0.8 ATR), and print an FVG within 2 bars; entry is at market on that close. C is cancelled if price closes 0.5 ATR beyond the FVG's far edge or 24 confirmation bars pass. The setup FVG is tradable for 24 setup bars.
- **Stops.** A: sweep low / leg origin. B: structural swing. C: FVG edge + 0.25 ATR. D: demand/supply distal. CONF: the confirmation-timeframe sweep extreme (model C).
- **Targets.** Fixed 1.5/2/2.5/3R; nearest liquidity with min RR 1.5–3 (rejected if the nearest pool pays less); partial 50% at liquidity + runner to the next pool with BE; adaptive (strong trend + displacement ≥ 1.5 ATR → second pool + structure trail; range → nearest pool + BE).
- **Management.** BE at +1R on/off; ATR trail; structure trail; 288-bar time stop.
- **Execution.** Limit fills require trading through the limit by half the spread. On the fill bar only the stop is checked. A bar touching both stop and target is a loss. Gaps through the stop fill at the open. These rules are covered by `bensim_research/test_bensim.py`, which also proves HTF bars are invisible until closed and that features and candidates don't change when future bars are appended.

## 13–15. Risk, costs

- **Risk** (`backtest.portfolio`): 0.5% per trade; one position per symbol; max 2% open risk; max 1% same-direction risk per currency (so two USD-short trades max); daily −2% and weekly −4% halts on *realised* P&L. Optional adaptive mode: NORMAL → CAUTION 0.75× (DD ≥ 3% or HIGH-vol regime) → REDUCED 0.5× (DD ≥ 6%) → HALTED 7 days (DD ≥ 10%), then the drawdown baseline resets and trading resumes at REDUCED. It never adapts to the last few results. Equity is additive % of starting balance (no compounding).
- **Costs.** Round-trip spread + commission per symbol, reused from the BSI V3 validation (`COST_PIPS`: EURUSD 0.9 pip … XAUUSD $0.30). Scenarios: optimistic 0.75× spread with no slippage; base 1.0× + 0.2 pip (gold $0.10) slippage on market entries and stops; conservative 1.5× + 0.5 pip (gold $0.30).

## 16–23. Headline numbers (Stack H, base costs)

| Configuration | Trades | Win % | Exp. net R | Exp. gross R | PF | t | Max DD (R) |
|---|---|---|---|---|---|---|---|
| T1 FVG alone (A, FVG stop, 2R) | 50,216 | 31.1 | **−0.243** | −0.077 | 0.70 | −38 | 12,192 |
| CORE = HTF + structure + sweep, entry C, 2R | 477 | 31.1 | **−0.164** | −0.091 | 0.78 | −3.6 | 96 |
| **Bensim Baseline v1** (FULL filters, C, CONF stop, liquidity TP 1.5R) | **13** | 23 | **−0.40** | −0.36 | — | −1.0 | — |
| Best DEV pick: CORE continuation, C, 2R | 176 | — | −0.074 | — | — | — | 26 (DEV) |

**Portfolio, CORE entry C, 0.5% risk** (450 trades accepted, 27 skipped by risk rules): net **−32%** over 8 years (−3.7%/yr), max DD **35.9%**, average DD 18.9%, Sharpe −0.75, Sortino −1.26, Calmar −0.10, worst day −1.7%, worst week −2.2%, worst month −3.8%, 14 losses in a row. Average MFE 1.06R vs MAE 0.96R; average hold 63 M15 bars (~16h). The adaptive risk mode cut the drawdown to 24.8% but still lost 21.5%. It reduces damage; it does not create an edge.

## 24–28. Detailed results

**Experiment ladder (Q1–Q7, DEV → HOLDOUT net expectancy, entry A / 2R):**

| Filter added alone to FVG | DEV | HOLDOUT |
|---|---|---|
| T1 FVG alone | −0.243 | −0.241 |
| + HTF trend | −0.238 | −0.252 |
| + structure (BOS/MSS in leg) | −0.234 | −0.224 |
| + supply/demand | −0.220 | −0.232 |
| + liquidity sweep | −0.241 | −0.207 |
| + Wyckoff aligned | −0.229 | −0.220 |
| + displacement ≥ 1 ATR | **−0.186** | **−0.155** |
| + London/NY | −0.230 | −0.227 |

Stacking filters (L1 → L8) never crosses zero with either entry A or C. Drop-one from FULL also shows no component whose removal clearly hurts: each removal stays in the −0.1 to −0.4R range, within noise of the full set.

- **Q1 FVG alone:** no edge. −0.077R gross, −0.243R net. An FVG almost always gets revisited (95% touched, 92% fully filled within 200 bars on every timeframe), so "price returns to the imbalance" is true, but trading that return isn't profitable.
- **Q2 HTF filter:** no improvement (−0.238 vs −0.243).
- **Q3 Structure:** no improvement.
- **Q4 Supply/demand:** +0.02R, within noise.
- **Q5 Liquidity sweep:** no improvement in DEV; slightly better in holdout, but still −0.21R.
- **Q6 Wyckoff:** no measurable value. The Wyckoff-aligned subset is −0.229R; removing the Wyckoff filter from FULL changes nothing meaningful.
- **Q7 MTF confirmation (entry C):** the only consistent improvement. FVG-only goes from −0.243 to −0.109 (DEV) and −0.061 (holdout). It cuts trade count ~7× and is still negative. With every filter stacked on top (FULL + C) it gets *worse* (−0.44R, n=72): too few trades and worse selection.
- **Q8 Entry model:** C confirmed > A first touch > B 50%/CE on the CORE set (−0.20 / −0.26 / −0.29 DEV). Without the confirmation-timeframe sweep requirement, C had the best DEV figure (−0.15) but a worse holdout (−0.18).
- **Q9 Stop model:** the wider stops (D supply/demand −0.11, A sweep −0.13, B structural −0.14) beat the FVG-edge stop C (−0.26) **only because costs are a smaller fraction of risk** (avg cost 0.05R vs 0.15R). Gross expectancy is negative for all four.
- **Q10 Target model:** fixed 1.5–2R beat every liquidity-based target. Liquidity targets (−0.41 to −0.54R), partial + runner (−0.31 to −0.51R) and adaptive (−0.35 to −0.51R) were all clearly worse: the nearest untaken pool that pays ≥ 1.5R is usually too far and is hit only 12–19% of the time. BE at 1R and trailing didn't help either. BE reduced the A-entry loss slightly (−0.25 vs −0.32 at 3R) but hurt entry C.
- **Q11 Trade frequency:** FVG-alone ≈ 13–17 setups/week per instrument. CORE + confirmation ≈ 0.11–0.19/week per instrument (~1/week across 7). **The full Baseline v1 produced 13 trades in 8 years across all 7 instruments** (≈ 0.005/week per instrument). Requiring every confluence filters almost everything out; it does not give "a few high-quality setups per week".
- **Q12 Max drawdown:** see the portfolio paragraph above (CORE-C: 35.9% at 0.5% risk). Monte Carlo trade-order shuffles of the same trades: median DD 41%, 95th percentile 48%. 100% of shuffles end negative.
- **Q13 Costs:** no configuration survives even the *optimistic* scenario (T1 −0.19R, CORE-C −0.17R). Most are negative gross, so costs only widen the loss.
- **Q14 Out-of-sample:** walk-forward (24-month train / 6-month test, choosing from 22 pre-declared configs) gave mean in-sample −0.03R vs **OOS −0.123R (n=1,426, t=−3.4)**. The selected config changed in almost every fold, a sign there's no stable winner. The single DEV-best config (CORE continuation, C, 2R: DEV −0.12R, n=116) scored +0.014R on the holdout (n=60, t=0.08). That is indistinguishable from zero, and still negative over the full period.
- **Q15 EURUSD vs XAUUSD:** neither works. XAUUSD is the *worst* instrument for confirmed entries (CORE-C −0.44R, n=83). EURUSD CORE-C is −0.07R (n=47). USDCAD is the only symbol mildly positive for CORE-C (+0.12R, n=66), which is what you'd expect from noise across 7 symbols.
- **Q16 Regimes:** negative in RANGE, TRANSITION, TREND_UP and TREND_DOWN; in every Wyckoff phase; and in every year 2018–2026 for CORE-C (best year 2025: −0.001R).

**Sessions (CORE-C):** Asia +0.02R (n=156) is the only non-negative session; London −0.19, Overlap −0.33, New York −0.31. Restricting to London/NY made confirmed entries worse (FULL+C: −0.44 → −0.67R DEV) and barely changed first-touch entries.
**News (CORE-C):** no filter −0.164; avoiding the 08:30 ET window/NFP −0.159; allowing news-displacement setups −0.186. News filtering doesn't matter. Caveat: the DB only has economic calendar history from 2026-08, so this used a proxy (08:30 New York weekday release slot ± 30 min plus first-Friday NFP), not real event times.
**Continuation vs reversal (CORE-C):** continuation −0.07R (n=176), reversal −0.22R (n=301). Reversal setups are clearly worse.

## 29. Monte Carlo (CORE-C portfolio, 10,000 runs)

| | Median DD | 95th % DD | P(DD ≥ 20%) | Losing streak p95 | P(final < 0) |
|---|---|---|---|---|---|
| Shuffle order | 41.5% | 48.2% | 100% | 20 | 100% |
| + slippage + 10% of winners failed | 65.2% | 74.4% | 100% | 23 | 100% |

## 30–32. Walk-forward, out-of-sample, parameter stability

- Walk-forward and holdout: see Q14.
- **FVG size (ATR-normalised) threshold** for T1 0.0 → 0.8: −0.243 → −0.208 → −0.172 → −0.146 → −0.117 → −0.115 → −0.101 → −0.087R. This is the one smooth, monotonic effect: bigger gaps are less bad. It's almost entirely a **cost effect** (avg cost 0.17R → 0.06R as stops widen); gross stays at about −0.02 to −0.08R. It never crosses zero.
- **Score threshold** 5 → 12: flat at −0.18 to −0.23R for entry A. For entry C it gets *worse* above 9. The confluence score doesn't rank setups.
- **Swing k** 2/3/4: CORE-C −0.20 / −0.22 / −0.33R. No stable region; larger k only removes trades.
- No parameter region is positive, so there is nothing to call robust.

## FVG research database (Phase 5/48)

370,872 FVGs (D1 3.5k, H4 17.9k, H1 65.9k, M15 283.5k), 2018–2026, 7 instruments (`fvg_research.csv.gz`):

- About 95% are touched, 93% reach 50%, 92% fill fully and 88% are invalidated by a body close through them, typically within 2 bars. Fresh, unfilled FVGs are rare.
- "Respected after touch" (moves 1 ATR in the FVG direction before a close through it) is about 48–53% on every timeframe. That is a coin flip. It is the same with or without HTF alignment (48.3% vs 48.4% on M15), similar across regimes and Wyckoff phases (46–56%), and identical across symbols.
- It does rise with size (33% for gaps < 0.1 ATR → 77% for > 1 ATR), but part of that is mechanical: a bigger gap leaves more room before its far edge is broken. The trade-level test above shows the size effect doesn't turn into a profitable entry.
- London and Overlap FVGs are respected more (54–58%) than New York and off-hours ones (38–46%). This is the most promising lead in the data, but it didn't survive into trade expectancy.

## 33. Failure modes identified

1. **FVGs get filled, not respected.** A 3-candle imbalance is revisited ~95% of the time and broken through ~88% of the time. Entering on the revisit is close to a coin flip, and a coin flip at 2R with a tight stop loses to costs.
2. **Costs dominate tight FVG-edge stops** (0.15–0.17R per trade). Every "improvement" that widens the stop looks better net for that reason alone.
3. **Stacking confluence removes trades without improving selection.** Baseline v1 made 13 trades in 8 years. The FULL + C confirmed variant is more negative than CORE + C. The score has no ranking power.
4. **Liquidity targets are too far.** The nearest untaken pool paying ≥ 1.5R is hit 12–19% of the time.
5. **Wyckoff (approximation), sessions and news filters add nothing measurable.**
6. **Walk-forward selection doesn't persist:** the "best" config changes every fold and loses OOS.

## 34. Recommended next experiment

1. **Don't deploy Bensim v1 or any variant here, even on demo.** There is nothing to forward-test.
2. If FVG research continues, the only directionally consistent leads are (a) a larger FVG size (≥ 0.4 ATR), (b) London/Overlap creation and (c) lower-timeframe confirmation entries. Test those three *together as one pre-registered hypothesis* on Stack H DEV with entry C and a 1.5–2R fixed target, then score once on the holdout. Expect it to stay near zero gross. If it doesn't clear +0.1R gross on DEV with n ≥ 200, close the FVG line of research.
3. Re-run Stack M (exact 4H/1H/15M/5M) once 12+ months of clean M5 data exist (around 2026-12 for most pairs). The 8-month result (T1 −0.31R, CORE-C −0.50R) is directionally the same as Stack H.
4. Higher-value alternatives for the next session: finish the donchian walk-forward with costs (the only strategy in the repo with a positive gross record), or fix the outcome-label time bug before any new ML work.

---
*Reproduce:* `python -m bensim_research.run --stack H` (~2 min/symbol at idle priority), `python -m bensim_research.report --stack H`; same with `--stack M`. Tests: `python -m pytest bensim_research/test_bensim.py`.
