# Why Bensim's Forex strategies fail: root-cause investigation (2026-11-03)

**Read-only.** No strategy, parameter, sizing, risk, demo or dataset change. No new experiment was run.

**Inputs** (all already existing):
- P2 v1 trades `reports/experiments/p2_2026-11-03/trades_observed_account.csv` (Aug 3 – Nov 3 2026, 6 symbols, observed IC demo costs, Friday 20:00 UTC close = the live rule);
- the Time Machine run `logs/tm_check_2026-07-26_fx.csv` (Oct 29);
- the live reconciliation (`reports/reconciliation_2026-11-03/`);
- the registry (368 trials);
- long-run results recorded in earlier research (project memory/logs; cited as **L** where not re-verified in this session).

**New in this report:** MFE/MAE for every P2 trade, computed from the stored M5 bid bars (`analysis.py` → `anatomy.csv`, `trades_with_excursions.csv`).

## Bottom line
**No Bensim Forex strategy has a statistically defensible edge.** The failure is a combination, in this order of weight:
1. **No demonstrated predictive edge in the entries.** Win rates sit close to the break-even rate implied by each strategy's own geometry. 37–50 % of losing trades never move even 0.3R in favour. Every strategy with long-run history is flat or negative over years.
2. **Results are produced by rare large winners, not by a repeatable edge.** Removing each strategy's 5 best trades turns **every** 3-month result negative (fib618 +26.5 → −2.2R, enigma +10.3 → −11.5R, imbalance +5.0 → −27.8R). Far ATR targets make this structural.
3. **Multiple testing and selection.** 368 registered trials. The live configuration, symbol lists and "best" variants were chosen on the same recent windows they're judged on. Every positive figure comes from those windows; DSR ≈ 0 everywhere.
4. **Costs remove whatever thin margin is left.** Modelled spread + swap ≈ 0.06–0.09R per trade, plus a measured **≈ 0.10R per trade live execution shortfall** (price movement in the ~8 s before the order, the weekend-reopen spread). That's 0.15–0.2R per trade against pre-cost expectancies of −0.06 to +0.36R.
5. **Design / implementation problems that make live differ from what was tested.** These are real, but secondary:
   - D1 books (parallax, cta) are force-closed every Friday although designed for multi-week holds;
   - sflow sends sub-spread-scale stops;
   - flow trades fixed lots;
   - Friday-close and pair-slot rules were absent from all historical backtests until 2026-11-03.

**Not the cause** (checked): lookahead or data leakage in the shared `plan()` code, timezone conversion, bar indexing. All verified correct (system audit §5). Live reproduces the backtest's signals and lots exactly on shared trades (reconciliation: 38/38 lots equal, same stops and targets).

---

## 1. Loss anatomy: where the R goes (P2 v1, live Friday rule, observed costs)
| | fib618 | enigma | imbalance | h1_fvg | pullback | orderflow | bennett | gf |
|---|---|---|---|---|---|---|---|---|
| trades | 92 | 24 | 80 | 41 | 1,065 | 11 | 5 | 2 |
| win rate | 35.9 % | 33.3 % | 22.5 % | 17.1 % | 30.5 % | 0 % | 60 % | 50 % |
| **break-even win rate at the median planned R:R** | 25.5 % | 30.9 % | 16.9 % | 31.9 % | 32.4 % | 16.6 % | 22.4 % | 47.4 % |
| average win / loss (R) | 2.51 / −0.95 | 3.19 / −0.95 | 3.70 / −0.99 | 2.17 / −1.01 | 1.87 / −0.99 | — / −1.01 | 2.45 / −1.00 | 1.09 / −0.22 |
| expectancy net (R) | +0.288 | +0.430 | +0.063 | −0.464 | −0.117 | −1.006 | +1.069 | +0.438 |
| **expectancy before costs (R)** | +0.358 | +0.524 | +0.128 | −0.392 | **−0.058** | −0.970 | +1.207 | +0.440 |
| profit factor | 1.47 | 1.68 | 1.08 | 0.44 | 0.83 | 0 | 3.66 | 5.06 |
| max drawdown (R) | 10.0 | 7.1 | 20.8 | 25.6 | 131.5 | 11.1 | 2.0 | 0.2 |
| max consecutive losses | 5 | 8 | 13 | 16 | 19 | 11 | 2 | 1 |
| average hold (h) | 12.5 | 11.3 | 3.8 | 6.7 | 2.5 | 5.1 | 18.2 | 19.8 |
| median stop / target (pips) | 17.3 / 48.6 | 13.1 / 38.2 | 15.4 / 76.1 | 16.1 / 43.7 | 14.6 / 28.8 | 79 / 343 | 13.8 / 65.3 | 317 / 347 |
| median planned R:R | 2.92 | 2.24 | 4.93 | 2.13 | 2.08 | 5.03 | 3.47 | 1.11 |
| exits: target / stop / time + Friday | 24 / 58 / 18 % | 25 / 63 / 13 % | 16 / 76 / 8 % | 12 / 81 / 7 % | 28 / 68 / 4 % | 0 / 100 / 0 % | 40 / 40 / 20 % | 50 / 0 / 50 % |
| median MFE / MAE (R) | 0.98 / 1.02 | 0.85 / 1.03 | 0.73 / 1.08 | 0.45 / 1.08 | 0.80 / 1.06 | 0.52 / 1.24 | 3.94 / 0.80 | 0.73 / 0.27 |
| **losers that never reached +0.3R** (entry wrong) | 37 % | 44 % | 37 % | 50 % | 42 % | 27 % | 0 % | — |
| losers that reached +1R, then the full stop | 29 % | 19 % | 31 % | 21 % | 21 % | 27 % | 50 % | — |
| winners that first went ≥ 0.5R against | 42 % | 75 % | 39 % | 57 % | 44 % | — | 67 % | 0 % |
| costs per trade (spread + commission + swap, R) | 0.069 | 0.093 | 0.066 | 0.072 | 0.059 | 0.036 | 0.138 | 0.002 |
| **total without the 5 best trades (R)** | **−2.2** | **−11.5** | **−27.8** | −33.1 | −134.7 | −6.1 | 0.0 | 0.0 |

**Reading it against the nine candidate causes:**
1. **Poor entries: yes, primary.**
   - A median MFE ≈ 0.5–1.0R with a median MAE ≈ 1.0–1.1R is close to the symmetric picture of random entries.
   - 37–50 % of losers never go 0.3R our way.
   - Pullback has **negative expectancy before costs** over 1,065 trades: its entries are no better than random, minus costs.
2. **Stop placement: secondary.** 19–31 % of losers reached +1R first and were later stopped. Many winners first went ≥ 0.5R against. Stops aren't obviously too tight (MAE ≈ 1R is what any stop produces), and moving them can't be judged without a new experiment (the 2026-10-10 management replays found that every management rule hurt; L).
3. **Unrealistic targets: yes, structural.** Targets at 2–5R (fib618/imbalance: next-bar open ± 4 ATR of the breakout bar, `strategy.py` L269) are reached 12–28 % of the time. The P&L is therefore a lottery on a few far hits (the top-5 row).
4. **Exit timing:** time/Friday exits are a small share (4–18 %) and on average slightly positive. Not a main loss source.
5. **Regime sensitivity:** splits are inconsistent and small (§4); no stable regime effect.
6. **Costs: material for thin edges** (0.06–0.14R modelled + ~0.10R live shortfall). Not the reason pullback/h1_fvg/orderflow fail: they're negative before costs.
7. **Trade frequency:**
   - Bennett (5) and gf (2) are too rare to ever validate. Bennett ≈ 20/yr, while 433 trades are needed (§5).
   - pullback is too frequent for its 2R geometry net of costs.
8. **Overlap:** fib618/enigma share trades. The single biggest win of both was the same GBPUSD short at the ECB + PPI release (forensic §1, §4).
9. **Backtest vs demo: present but small per trade** (−0.10R/trade on shared trades). Large only when rules were missing historically (weekend hold −2.32R on one trade).

## 2. Comparison against the noise baseline (existing data only)
Time Machine **bensim mode** (null model: 26 weeks of the same hour-of-week, drift removed, same symbol, direction, entry, stop, target), from the Oct 29 run `tm_check` (Jul 26 – Oct 26 2026, FX). Week-block bootstrap, 4,000 paths:

| Strategy | Trades | Real target-hit | Noise P(target first) | Excess | 95 % CI |
|---|---|---|---|---|---|
| fib618 | 75 | 30.7 % | 19.2 % | **+11.5 pts** | **[+6.1, +17.6]** |
| enigma | 22 | 31.8 % | 21.4 % | +10.5 | [−15.3, +33.4] |
| imbalance | 55 | 20.0 % | 13.5 % | +6.5 | [−2.0, +16.3] |
| Bennett | 3 | 33.3 % | 15.7 % | +17.6 | [−14.8, +82.5] |

**Interpretation (critical):**
- **This window is the one fib618's live configuration was chosen on** (2-of-3 confirmations, symbols). It was also mined in at least 6 further splits. An in-sample excess there is expected under selection.
- **Long-run evidence points the other way:** fib618 over 25 pairs × 14 years is −0.048R, 0/15 years positive (L).
- **Two biases in this comparison:**
  - The TM horizon (to the Friday close) is **shorter** than these trades' backtest horizon (they held weekends, legacy run). That **inflates** the real hit rate relative to noise.
  - The TM's own target odds track the R:R geometry: AUC 0.56 vs 0.59 for 1/(1+RR); partial correlation with R 0.078, t 0.96 (system audit §7).
- **The other strategies have no comparison** (pullback, h1_fvg, orderflow, gf, sflow, parallax, cta, flow). That needs the TM run on their trades: Experiment E1 below, **not run**.

## 3. Statistical reliability
- **DSR ≈ 0 for every strategy with n ≥ 20.** The threshold SR* ≈ 0.44 per trade comes from 368 registered trials; fib618's per-trade Sharpe is ≈ 0.22.
- **PBO 0.50** (P2 v1, 8 strategies): the in-sample best is a coin flip out of sample.
- **Holm:** only orderflow is significant, and negative (p 0.0096).
- **Concentration:** every positive 3-month result depends on its 5 best trades (table §1).
- **Out-of-sample / long-run evidence on file (L):**
  - fib618: 14y 25 pairs −0.048R; unseen Apr–Jul 2026 −0.045R.
  - imbalance base: 5y +0.048R t 0.96.
  - enigma: 5y ≈ break-even.
  - Bennett: setup DB negative after costs.
  - pullback: −0.17R t −3.8.
  - orderflow: variants 0 to −0.22R.
  - sflow (JeaFx structure): fails unseen FXCM 2012-26, 25 pairs.
  - parallax: +0.13R 2012-2021, ~0 since 2021 (decayed).
  - cta: positive only 2012-14.
  - flow ML (rate-aware): +1.58 bp/trade walk-forward 2016-26, +0.86 since 2021 (small vs cost).
  - EURUSD morning short: ~1 bp/day net, 2022-heavy.
  - ML signal search: the edge was a rollover-cost artefact.
- **Selection bias:** symbols were dropped after negative results (live.py L42: AUDUSD/USDJPY removed 2026-10-13). The 2-of-3 confirmation was picked on Jul–Oct 2026.

## 4. Market conditions (diagnostic only; not filters)
P2 v1 splits (n per cell in the report files):
- **fib618:**
  - London 07-12 +1.32R (17 trades; contains the +12.99R ECB trade), London/NY 12-16 +0.06 (47);
  - range +0.47 vs trend +0.10;
  - low vol +0.38 vs high vol +0.09;
  - Sep +0.65, Oct +0.05;
  - GBPUSD +0.91, EURUSD +0.57, USDJPY −0.41, XAUUSD −0.29.
- **imbalance:** London +0.89 (15), NY 16-21 −0.73; high vol −0.26 vs low vol +0.27; month signs alternate.
- **enigma:** range +1.00 (13) vs trend −0.24 (11); cells of 2–7 trades.
- **pullback:** **negative in every session, regime, volatility state and month** (cells of 72–660 trades). The most informative result: no condition rescues it.
- **h1_fvg:** negative in every cell except USDJPY (9 trades).

**No condition is consistent across strategies.** The splits with positive cells are small and include the same exceptional trades. They're hypotheses at most (R6), never filters on this data.

## 5. Backtest vs demo (reconciliation, 2026-10-13 → 11-03)
- **Same code, same signals:** 38 shared trades with identical lots, stop and target.
- **Live is 0.10R/trade worse on shared trades** with the same exit type. The live entry comes ~8 s after the bar close (median +0.2 pip worse, slippage vs quote ≈ 0). The **Sunday reopen spread** cost 34 pips once.
- **Manual closes:** −0.90R over 9 trades.
- **Missing rule history:** the Friday close only live from 2026-10-19; books from 10-20.
- **Missed signals:** 1 during an engine/terminal outage (2026-10-18). 2 unexplained (XAUUSD 10-26, at the 48-bar expiry edge).
- **Pair slots / priority:** 6 backtest trades skipped (+1.55R in the backtest). The six-position limit never bound.
- **Net effect:** backtest +8.72R vs live +3.24R on shared trades. **Execution isn't what makes the strategies fail.** It only removes the thin in-sample margin.

## 6. Implementation vs hypothesis (code read; nothing changed)
| Strategy | Hypothesis (from its docstring) | Implementation finding | Consistency |
|---|---|---|---|
| fib618 | breakouts continue after a 61.8 % pullback | fib level **frozen at the breakout bar** (strategy.py L237-239): later extension makes the entry a shallower retracement. Target = **next-bar open ± 4 ATR**, not tied to the leg (L269), so R:R 2.1–13.6. The "killzone" confirmation forces 85/92 trades into 07-10/12-15 UTC | logical, but the target design creates lottery-like P&L |
| imbalance | the breakout's imbalance zone gets retested | same Donchian signal as fib618 (correlated), 4 ATR target, R:R median 4.9 | as above |
| enigma / H1 FVG | open HTF FVG + LTF shift | target = nearest liquidity ≥ 1.5R else 2R (fvg_strategy.py L95-96) | consistent; small n (enigma) / negative (H1) |
| Bennett | SMC OTE + M5 CHoCH, ≥ 2.75R | ~9 stacked conditions; 23 of 37,713 checks survive to the last filters (funnel) | consistent but **untestable at its frequency** |
| pullback | EMA20/50 pullback continuation | fixed 1.2/2.5 ATR | consistent; **no edge before costs** |
| orderflow | OB liquidity / fake-out | 11/11 losers | consistent; rejected |
| gf | D1/12H trend + 4H CHoCH | 2 trades | untestable at its frequency |
| sflow (live book) | H1 BOS zone retest | **no minimum stop**: 1.26-pip stop with 19.39 lots sent live (−1.27R realized vs −1R planned) | implementation gap |
| parallax / cta (live books) | D1 setups held 30 / 120 days | **force-closed every Friday** by `live.close_all` | live ≠ tested strategy |
| flow ML / AM | rate-gap model / EURUSD morning drift | fixed lots (0.8 / 2.5), outside max-open and currency checks; spread feature from FXCM | small walk-forward edge on a different spread world |

No lookahead, wrong-timeframe or timezone bug was found in the shared engine (system audit §5, verified).

## 7. Ranking by evidence (not by profit)
| Rank | Strategy | Evidence | Conclusion |
|---|---|---|---|
| 1 | **pullback** | 1,065 trades, negative before costs and in every condition; earlier 955 trades t −3.8 | **reject**: the clearest evidence in the project, that this entry has no edge |
| 2 | **orderflow** | 11/11 losers, Holm p 0.0096 (negative); earlier variants ≤ 0 | **reject** |
| 3 | **fib618** | 14y 25 pairs −0.048R; unseen Apr–Jul 2026 negative; 3m +0.29R only with its top-5 winners; noise excess +11.5 pts only in the selection window | **reject as an edge claim**; keep only as the subject of R1 (news) |
| 4 | **imbalance** | 5y t 0.96; 3m relies on top-5 (+32.9R of +5.0R); live −0.47R / 9 | **reject** |
| 5 | **h1_fvg** | 41 trades −0.46R, negative in nearly all cells; no long-run data | **reject** |
| 6 | **sflow** | fails unseen FXCM 2012-26 (L); live −3.84R / 8; stop-size bug | **reject** (and fix the live stop rule if it keeps trading) |
| 7 | **parallax / cta** | decayed after 2021 / 2012-14 only (L); incompatible with the live Friday close | **reject** for live |
| 8 | **enigma** | 3m +0.43R / 24 but −11.5R without the top 5; 5y ≈ break-even | **investigate only via a pre-registered long-run test**; no edge now |
| 9 | **flow ML / EURUSD AM** | small positive walk-forward (+0.86 bp/trade since 2021; ~1 bp/day AM, 2022-heavy) (L); live +$1,319 / 25 | **investigate**: the only FX items with out-of-sample positive history, but the size is comparable to costs; needs IC-cost validation |
| 10 | **Bennett, gf** | 5 and 2 trades | **can't be evaluated** at current frequency; reject for live until enough data exists |
| — | manager (discretionary) | 1 trade | not a strategy |
| — | crypto momentum (paper, not FX) | the only item recorded as passing an untouched period (2022-26) (L) | outside this investigation's scope |

## 8. What would establish an edge (none of this is done)
1. **Pre-registered tests on never-used data**, with every rule frozen, live-identical costs (observed spread + 0.10R shortfall + swap) and the live Friday rule:
   - FXCM 2012-2023, eligibility per §6 of the second-stage review;
   - IC Markets 2021-10 → 2024-03 for pairs with data.
2. **Excess over the matched noise baseline** (TM null, same geometry and horizon) significant **after** Holm/DSR over all strategies tested, plus the same sign in most years and pairs.
3. **Robustness to removing the top 1–5 % of trades** (a strategy that dies without its best 5 trades has no repeatable edge).
4. **Enough trades:** roughly ≥ 400–1,400 depending on the effect size (pre-registrations R1/R2 v2, P3 v2).

## 9. Experiments that would help, NOT run (each needs approval)
| ID | Hypothesis | Method | Data | Expected output |
|---|---|---|---|---|
| E1 | strategy target-hit and R exceed the matched noise null | TM null for every P2 trade of all 8 strategies (2,000 replays, the trade's own horizon incl. the Friday close, observed costs); Holm over 16 tests. This is R2 on the P2 window | P2 trades + DB M5 | excess hit / R per strategy with CI |
| E2 | the long-run verdicts hold under live-identical rules | rerun fib618, imbalance, enigma, pullback, H1 FVG with the observed-account profile + Friday close on FXCM 2012-2023 (no XAU) | FXCM M5 (eligible years) | per-year expectancy, DSR, top-trade robustness |
| E3 | fib618/imbalance P&L is driven by news-window far targets | R1 v2 as pre-registered | IC M5 + calendar 2021-10 → 2026-06 (calendar refresh needed after 10-18) | news vs no-news expectancy, with and without the top 1 % |
| E4 | entries carry information beyond random entries at the same time of day | for each strategy, random-direction entries at the same timestamps with the same geometry, 1,000 permutations | P2 / E2 trades | distribution of permuted expectancy vs actual |

## Data limitations
- The P2 window is 3 months and already heavily used for selection.
- Long-run results marked (L) come from earlier sessions and weren't re-run here.
- The economic calendar ends 2026-10-18.
- IC spread is observed for one session only.
- USDCAD M5 only from 2024-02-06.
- FXCM 2024-2026 is incomplete and has no XAUUSD.
- The TM comparison exists for 4 strategies and one window only.
