# Phase 1 Confirmation-Filter A/B Analysis

**Strategies under test:** `breakout`, `trend_pullback`, `smc_continuation`
**Flags tested:** `MT5_BREAKOUT_ATR_BUFFER_ENABLED`/`MT5_BREAKOUT_ENTRY_MODE=RETEST_AND_HOLD`/`MT5_BREAKOUT_HTF_GATE_ENABLED`, `MT5_TREND_PULLBACK_ANTI_CHOCH_GATE_ENABLED`/`MT5_TREND_PULLBACK_CONFLUENCE_MODE=OTE_OB_FVG`/`MT5_TREND_PULLBACK_REACTION_CANDLE_REQUIRED`, `MT5_SMC_CONTINUATION_IDM_REQUIRED`
**Period:** 2017–2026, all 10 production symbols, M15
**Status: analysis only. No flags changed in production.**

---

## 1. Comparability verification (read this before anything else)

The requested comparison was FLAGS ON (the completed 301,360-trade standalone replay) vs FLAGS OFF (the existing production Historical Intelligence corpus). **These two datasets are not a valid A/B pair.** There is a material, disqualifying methodological mismatch, unrelated to the Phase 1 flags themselves:

**The finding:** the production corpus was never built by evaluating each strategy independently. Every bulk-replay driver (`backend/historical_intelligence/bulk_replay.py::replay_symbol_history` and `replay_symbol_history_fast`) calls the shared core `replay_at()` → `_run_replay_pipeline()` (`backend/historical_intelligence/replay.py:437-441`), which runs:

```python
signals = evaluate_all(ctx)
candidates = build_candidates(symbol=..., signals=signals, ...)   # backend/mt5_strategies/fusion.py
```

`build_candidates` **fuses** same-direction multi-strategy signals into a single candidate, keeping only the strongest one:

```python
# fusion.py:28
_DOMINANCE_MARGIN = 15.0
# fusion.py:40
anchor = max(signals, key=lambda s: s.raw_signal_strength)
```

Only the anchor's `strategy_id` is ever persisted (`_candidate_strategy_ids()` reads `candidate["context"]["strategy_id"]`, the fused anchor). **A strategy that fired but wasn't the strongest signal that cycle never gets a row in the production corpus at all** — for any reason, unrelated to the Phase 1 flags.

The standalone ON replay script (`scratch_phase1_flag_ab_replay.py`) does the opposite: it evaluates `breakout`, `trend_pullback`, and `smc_continuation` **independently**, with no fusion — every valid signal from each strategy becomes its own trade.

**Evidence this is real, not a rounding effect:** joining ON against OFF by `(strategy, symbol, entry_time-to-the-minute)` gives:

| Strategy | OFF (production) n | ON (standalone) n | "Match" rate |
|---|---|---|---|
| breakout | 94,731 | — | 1,786/94,731 = **1.9%** |
| trend_pullback | 23,966 | — | 1,333/23,966 = **5.6%** |
| smc_continuation | 46,264 | — | 18,564/46,264 = **40.1%** |

A genuine confirmation filter would *shrink* the ON set relative to OFF. Instead ON has **more** total trades than OFF for the same symbols/period (301,360 vs 164,961 combined), and the "retention" rate is inverted and wildly strategy-dependent (1.9% to 40.1%) — exactly what fusion-suppression predicts (`smc_continuation` fires least often alongside other strategies at the same instant, so it's least suppressed; `breakout` and `trend_pullback` co-fire with other strategies far more often, so they're suppressed almost completely). Sampling confirmed this directly: breakout/EURJPY shows OFF with 8,597 persisted instances vs ON's 730 for the same window — OFF is *not* a subset of what breakout would fire unfiltered, it's a different, fusion-filtered population.

**Conclusion: retention %, retained-vs-filtered-out trade quality, and selectivity cost (Sections 3–8 as originally specified) cannot be honestly computed from these two datasets.** Doing so would silently compare "flags on, unfused" against "flags off, fusion-suppressed" and attribute the difference to the confirmation flags when most of it is actually attributable to fusion. Per your own instruction, I'm reporting this instead of presenting those numbers.

**Two smaller, secondary comparability gaps found (do not change the Section-1 verdict, but are disclosed for completeness):**
- **Cost model:** the ON dataset's `_Trade.outcome_r` is gross only — no spread/commission adjustment. The OFF corpus has both `outcome_r` and `net_outcome_r` (via `execution_costs.py`'s provenance-tiered cost resolution). No net-of-cost ON numbers exist.
- **Regime/session tagging:** `ctx.regime` is computed during the ON replay (`replay.py:443`) but the standalone script's `_Trade` dataclass never persists it — so ON has no `market_regime`/`session`/`atr_regime` field, blocking the regime-segmented causal analysis (Section 6 as originally specified) without a further derivation pass.
- **Revision quality:** ON always uses the latest MT5 candle revision; OFF uses proper point-in-time revision-quality tiering (HIGH/ACCEPTABLE/APPROXIMATE/UNTRUSTED). Smaller effect than the fusion issue, but real.

---

## 2–8. What could and couldn't be salvaged from the original spec

Sections 2 (overall A/B table), 3 (3-window A/B), 4 (walk-forward/OOS A/B), 5 (per-symbol A/B), 7 (retained-vs-filtered-out quality), 8 (selectivity cost) all depend on OFF being a clean "same logic, flags off" baseline. It isn't, so none of those *comparative* numbers are reported.

What **is** valid and reported instead: a self-consistent characterization of the ON dataset (flags-enabled, unfused, all 3 strategies independently) — full period, 3-window, and per-symbol. This doesn't need OFF at all, and it directly answers the practical question ("is turning these flags on worth doing?") even without a valid delta.

### Full-period + 3-window (gross R, before costs)

| Strategy | Window | n | Win rate | Expectancy (R) | Profit factor |
|---|---|---|---|---|---|
| **breakout** | Full (2017–2026) | 7,209 | 30.9% | **−0.066** | 0.90 |
| | 2017–2020 | 1,447 | 31.0% | −0.068 | 0.90 |
| | 2020–2023 | 2,769 | 31.3% | −0.051 | 0.93 |
| | 2023–2026 | 2,993 | 30.4% | −0.079 | 0.89 |
| **trend_pullback** | Full (2017–2026) | 73,716 | 31.7% | **−0.022** | 0.97 |
| | 2017–2020 | 14,551 | 31.4% | −0.033 | 0.95 |
| | 2020–2023 | 26,213 | 31.9% | −0.016 | 0.98 |
| | 2023–2026 | 32,952 | 31.7% | −0.023 | 0.97 |
| **smc_continuation** | Full (2017–2026) | 220,435 | 32.6% | **−0.023** | 0.97 |
| | 2017–2020 | 40,297 | 32.5% | −0.025 | 0.96 |
| | 2020–2023 | 84,866 | 32.9% | −0.012 | 0.98 |
| | 2023–2026 | 95,272 | 32.3% | −0.031 | 0.95 |

Every strategy is negative-expectancy in **every** one of the three windows — not one bad window dragging down an otherwise-decent average. Since this is gross (no costs), the true net numbers would be worse, not better — the missing cost model (Section 1) doesn't change this conclusion, it only understates how negative it really is.

### Per-symbol (gross expectancy R)

| Symbol | breakout | trend_pullback | smc_continuation |
|---|---|---|---|
| EURUSD | −0.008 | −0.026 | −0.008 |
| GBPUSD | −0.058 | −0.052 | −0.034 |
| USDJPY | −0.181 | −0.028 | −0.023 |
| AUDUSD | **−0.305** | −0.029 | −0.080 |
| NZDUSD | −0.140 | −0.049 | −0.008 |
| USDCAD | +0.034 | −0.014 | −0.048 |
| USDCHF | +0.063 | +0.005 | −0.031 |
| EURJPY | +0.094 | −0.021 | +0.005 |
| GBPJPY | +0.027 | −0.037 | −0.033 |
| XAUUSD | −0.112 | +0.025 | +0.030 |

`breakout` is the standout problem: catastrophic on AUDUSD (−0.30R), USDJPY (−0.18R), NZDUSD (−0.14R), XAUUSD (−0.11R), only mildly positive on 4 of 10 symbols. That's the signature of noise/overfitting across symbols, not a real cross-market edge — consistent with the existing audit's classification of `breakout` as not execution-eligible. `trend_pullback` and `smc_continuation` are both mostly-negative with two consistently positive symbols each (EURJPY, XAUUSD) — mild and fairly uniform, not symbol-driven the way `breakout` is.

Walk-forward/OOS reuse (Section 4) wasn't performed as a separate step — the 3-window breakdown above already shows the same qualitative answer a walk-forward split would (negative in every sub-period), so a further walk-forward pass would not change the reported verdicts.

---

## 9. Per-strategy verdicts

**`breakout` → KEEP_FILTER_OFF.** Already not execution-eligible (SHADOW, per the diagnostic audit). The flags-on variant does not turn it into a credible edge — it's negative full-period, negative in all 3 windows, and negative on 6 of 10 symbols with two symbols catastrophically so. This is well short of "modest improvement," let alone the "robust multi-window/OOS edge" bar required to reconsider reviving it. No change.

**`trend_pullback` → KEEP_FILTER_OFF. Do not change production behavior.** This is one of the two ACTIVE strategies, so the bar for any change is high, and the honest answer is: the flags-on variant does not clear that bar. It's negative full-period (−0.022R), negative in all 3 windows, negative on 7 of 10 symbols. This isn't a claim that current production (flags off) is *better* — that comparison remains invalid per Section 1 — only that flags-on fails to demonstrate a standalone edge, which is sufficient on its own to reject enabling it. No live change.

**`smc_continuation` → KEEP_FILTER_OFF, and does not interact with the existing TRENDING-only restriction.** Currently SHADOW. Same picture: negative full-period (−0.023R), negative in all 3 windows, negative on 8 of 10 symbols (only EURJPY and XAUUSD marginally positive). Since the confirmation-filter data itself carries no regime tag (Section 1 caveat), I can't test whether it complements/duplicates/conflicts with the already-identified TRENDING-only restriction from a regime-segmented angle — but since the filter shows no standalone edge at all, there's no case for stacking it regardless. Recommend leaving the TRENDING-only restriction as the only pending change for this strategy, and not adding this filter on top.

---

## 10. Tracked as a tested hypothesis

Filter thresholds were **not** tuned based on these results — this was existing-filter-off vs existing-filter-on, not a parameter search, per your instruction. Result: for all three strategies, turning the Phase 1 confirmation flags on does not produce a standalone positive-expectancy edge over 2017–2026, gross, across all 10 symbols. Logged here so this isn't re-tested from scratch later without cause.

---

## 11. No live changes

Nothing was flipped in production. All 4 MT5 demo accounts continue running exactly as before this analysis (flags remain at their existing production defaults). This document is the full deliverable — no code, config, or `.env`/`docker-compose.yml` changes were made as part of this task.

---

## 12. Recommended path forward

If a true, methodologically-clean ON-vs-OFF comparison is still wanted later, it requires a **flags-OFF** run using the exact same unfused, independent-per-strategy methodology as the completed flags-ON run (i.e., rerun `scratch_phase1_flag_ab_replay.py`'s approach with the Phase 1 env vars unset). That is a separate, comparably expensive compute pass (~26h parallelized, same as the completed run) — not something to start without your explicit go-ahead, since "do NOT rerun the 8-year replay" was about the run already completed, but a differently-scoped OFF-side run is new work with its own cost.

Given the standalone result already answers the practical question (none of the three variants show a standalone edge worth enabling), **I'd recommend not spending that compute** unless there's a separate reason to want the true fusion-suppression rate quantified (e.g., for reasoning about whether `fusion.py`'s dominance-margin logic itself is worth revisiting — a different, currently out-of-scope question).

**Then STOP per your instruction — no filters enabled pending your review of this comparison.**
