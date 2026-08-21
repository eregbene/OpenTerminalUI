# Strategy Diagnostic Audit, 2017-2026

**Scope:** Bensim's 12 MT5 forex strategies (`mtfai1`, `support_resistance_bounce`, `momentum`, `session_breakout`, `vwap_reversion`, `breakout`, `ema_trend`, `mean_reversion`, `smc_continuation`, `trend_pullback`, `liquidity_sweep_reversal`, `wyckoff`). READ-ONLY analysis. No live trading, activation flag, strategy/risk/execution code, or production table was touched while producing this document.

**Author's note on process:** this document is a diagnostic, not a verdict. Every number below traces to an actual SQL query or an actual call into `backend/historical_intelligence/strategy_robustness.py`, run on 2026-08-21 against the live Postgres instance backing `openterminalui-backend-1`. Where a question could not be answered honestly from available data, that is stated explicitly rather than estimated.

---

## 0. Methodology and limitations (read this before the tables)

**Corpus.** `historical_pattern_fingerprints` JOIN `historical_setup_outcomes`, filtered to `resolution_status='RESOLVED' AND data_quality != 'UNTRUSTED'` throughout (the same exclusion rule `statistics.py::pattern_statistics` and `strategy_robustness.py` already use). 10 canonical symbols (EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, NZDUSD, USDCAD, XAUUSD, EURJPY, GBPJPY). Corpus entry-time span: 2018-02-28 to 2026-08-13 for the 11 long-running families; **`wyckoff` is the one exception — its corpus entry-times run 2026-02-02 to 2026-07-31, i.e. six months, not the "~3 years" this audit was briefed to expect.** That briefing premise is corrected here rather than silently honored; §13 treats wyckoff's evidence base as six months, not three years.

**Three historical windows**, each a fresh, capped, lightweight re-pull (max 8,000 most-recent rows per strategy per window, 500-path bootstrap/Monte Carlo — the same lightweight settings validated earlier this session) run today directly against Postgres:
- **Recent**: 2023-08-22 → now
- **Prior 3y**: 2020-08-22 → 2023-08-22
- **Prior 6y**: 2017-08-23 → 2020-08-22 (real data only starts 2018-02-28)

The three JSON files referenced in this audit's brief (`/app/scratch_strategy_robustness_*.json`) had been evicted from the container's ephemeral filesystem by the time this audit began (the container had restarted since they were generated), so all three were **regenerated from scratch this session** using the same already-written scripts (`scratch_strategy_robustness_report*.py`), same methodology, same caps. The regenerated recent-window numbers match the briefing's rounded summary table to three decimal places, confirming methodological continuity.

**Walk-forward is NOT re-split per window.** `walk_forward_verdict()` calls `walk_forward.run_walk_forward()` — the same function `brokers/mt5/sequence_risk.py` reads as a live gate — which always evaluates the strategy's **full trusted corpus** (train/OOS split across all available history), not the specific 3-year window being reported alongside it. The train_n/oos_n/expectancy/edge_stability figures are therefore **identical across all three window tables for a given strategy** (verified directly: mtfai1's walk-forward `train_n=579398` appears unchanged in all three re-runs). Read walk-forward as a separate, whole-history signal, not a per-window one.

**PSR/DSR saturation.** Several strategies show PSR/DSR reading exactly `0.0` or `1.0`. This is a real floating-point saturation artifact of the PSR/DSR formula at extreme z-scores (e.g. mtfai1's recent-window Sharpe of -8.26 annualized drives PSR to `6.7e-05`, and `breakout`'s -54.7 Sharpe drives it to `2.4e-250`) — not literally "0% or 100% certainty." Treat these as "overwhelmingly rejects/supports a positive Sharpe," not as literal probabilities.

**mtfai1 is ~48% of the entire corpus** (966,197 of ~1.99M resolved rows) — it is the original, unrestricted, always-on legacy strategy (empty `regimes` tuple in `STRATEGY_FAMILIES`, i.e. no regime gate), so its footprint dwarfs the other 11.

**Confidence-band segmentation from the historical corpus is not usable — and the confidence engine itself does not calibrate well where it CAN be checked.** `bulk_replay.py` — the pipeline that built this corpus — always stamps `confidence_band=None` on every backfilled row (verified by reading the source: `entry_time=at, confidence_band=None, ...` in both of its write paths). Only the small number of fingerprints captured from genuinely *live* candidates carry a real band (`strong`/`valid_autonomous`/`observe_only`/`reject`/`very_strong` — Bensim's live categorical labels, a different taxonomy than the numeric 60-69/70-74/... bands `confidence_calibration.py` uses), and those subsets are tiny (tens to low hundreds of rows per strategy) — too small to trust per-strategy. Every strategy section's own "Confidence engine" note below points back here rather than repeating this.

A genuine, cross-strategy read IS available from `backend/brokers/mt5/confidence_calibration.py`'s own analytics over `MT5CandidateEvaluationORM` (n=12,031 resolved live/recent candidates, all 12 strategies pooled — a different, smaller, more-recent population than the historical fingerprint corpus, run today via that module's existing, unmodified functions):

- **Band monotonicity fails.** `calibration_reliability_report()` returns `band_monotonic_with_confidence=False`. Avg realized R by confidence band: 60-69 → +0.099R, **70-74 → -0.234R**, 75-79 → -0.047R, 80-84 → -0.102R, 85-89 → **+0.622R**, **90+ → -0.482R (n=27)**. A well-calibrated engine would show avg R rising monotonically with confidence; instead the 70-74 band is worse than 60-69, and the 90+ band (its most-confident bucket) is the single worst-performing band in the whole table, though on a thin sample (n=27, `sample_label="low_confidence"` by this codebase's own convention — not enough to overturn anything on its own, but not a result that supports "confidence 90+ is trustworthy" either).
- **Overall confidence-vs-outcome correlation is weak: r=0.060** (confidence-vs-MFE r=0.109, confidence-vs-positive-outcome r=0.039) — confidence tracks realized R only faintly.
- **Component-level effectiveness (9 components, `component_effectiveness_report()`, same n=12,031):** `structure_confluence` (r=0.133) and `trend_multi_timeframe` (r=0.082) are the strongest positive contributors. **`signal_freshness` correlates *negatively* with outcome (r=-0.210)** — fresher signals perform worse, the opposite of its intended direction. `reward_risk_quality` is also mildly negative (r=-0.044). Three components — `strategy_performance`, `symbol_performance`, `execution_conditions` — show **no variance at all** in this sample (`avg_score` pinned at 60.0, 60.0, and 100.0 respectively) and therefore no computable correlation: these three components are not actually differentiating candidates in the live-tracked data, whatever their intended function.

This is a real, cross-strategy finding, not specific to any one strategy in §§2-13 below — it means a strategy that looks weak in aggregate cannot currently be assumed "salvageable via its highest-confidence occurrences," because the confidence engine's own ranking does not reliably separate good occurrences from bad ones in the population where it's checkable. It also means confidence should not be over-trusted as a filter for the specific improvement ideas proposed later in this document (e.g. §6's vwap_reversion management fix) without separately validating that a confidence-based restriction actually helps for that strategy.

**Counterfactual methodology (used throughout every strategy's "Entry-vs-management decomposition" subsection below).** Fixed-R-target counterfactuals use the corpus's own path-aware `reached_1r`/`reached_1_5r`/`reached_2r`/`sl_hit` booleans (computed by the real walk-forward outcome resolver, never reconstructed from MFE/MAE alone): a trade counts as a *T*-R win if it actually touched +*T*R at any point (`reached_Xr`); if it never reached *T* but did hit the original SL, it counts as a -1R loss under that counterfactual; trades that neither reached *T* nor hit SL (e.g. actual TP < *T* was hit, or the setup timed out) are excluded from that counterfactual's average and reported separately as "undetermined" — coverage is shown for every counterfactual so a low-coverage number (this bites hardest on the 2R counterfactual) is never presented with false precision.

**Live-managed (ATM) real-position data is very thin.** `adaptive_position_states` closed positions exist **only from 2026-08-07 to 2026-08-21** — a two-week window — across every strategy. Applying this codebase's own sample-size convention (`confidence_calibration.py::sample_label`: n<20 "insufficient", n<50 "low_confidence", n<100 "preliminary", n≥100 "increasingly_useful"), only `mtfai1` (n=176) clears "increasingly_useful"; everything else (n=2 to 40) is insufficient-to-preliminary. Every ATM-vs-static comparison below is labeled with its real n and should be read as a *very early read*, not a settled verdict.

**Critical timing caveat — the corpus predates today's strategy-engine changes.** The historical corpus for the 11 non-wyckoff strategies was last (re)replayed **2026-08-20, ~07:48 UTC**. Three strategy-engine commits landed on **2026-08-21** (today, after that replay): `8362841` "Phase 1 modularization + Phase 2 engine-wide regime/session/spread gates", `8e72305` "Phase 3 — structural TP propagation, IDM breakout gate, vwap anchor fix, momentum deprecation", and `mtfai1`-specific gates from 2026-08-17→19 (HH/LL structure confirmation, EQH/EQL liquidity confirmation, standalone-confirmation kill switch, trade-frequency caps, re-entry blocking, cross-account concurrent-exposure blocking — all visible live in tonight's cycle logs). **None of these are reflected in any number in this document.** All findings describe strategy behavior as encoded through 2026-08-20. `momentum`'s hard code-level deprecation guard (`MT5_MOMENTUM_STRATEGY_ENABLED`, default False, added today) in particular means the corpus's momentum numbers describe a strategy that, as of hours ago, no longer generates any live signal at all regardless of activation status.

**Multiple-comparisons discipline (§2 mtfai1, §4 momentum, §5 session_breakout in particular).** For the three persistent-negative strategies, only the small number of a-priori meaningful splits (session, symbol, regime, direction, ATR regime — 5-6 splits, not a grid search) are checked, and any positive-looking corner is reported with its own n rather than presented as a discovered edge. Where nothing survives, that is stated plainly.

**Live activation ground truth (verified today, both layers).**

| Strategy | `STRATEGY_FAMILIES` default | `MT5_STRATEGY_ACTIVATION_*` override | Effective `activation_status()` | Other gates |
|---|---|---|---|---|
| mtfai1 | ACTIVE_MT5 (unrestricted, no regime gate) | ACTIVE_MT5 | **ACTIVE_MT5** | none tripped |
| support_resistance_bounce | ACTIVE_MT5 | SHADOW_MT5 | **SHADOW_MT5** | none |
| momentum | ACTIVE_MT5 | none | ACTIVE_MT5 by the activation registry, **but hard-blocked** by a separate code-level circuit breaker (`MT5_MOMENTUM_STRATEGY_ENABLED`, default False, added 2026-08-21) that returns `STRATEGY_DEPRECATED_PENDING_REBUILD` before any signal logic runs | effectively **DISABLED** as of today |
| session_breakout | ACTIVE_MT5 | SHADOW_MT5 | **SHADOW_MT5** | none |
| vwap_reversion | ACTIVE_MT5 | none | **ACTIVE_MT5** | none |
| breakout | ACTIVE_MT5 | none | **ACTIVE_MT5** | none |
| ema_trend | ACTIVE_MT5 | none | **ACTIVE_MT5** | none |
| mean_reversion | ACTIVE_MT5 | none | **ACTIVE_MT5** | none |
| smc_continuation | ACTIVE_MT5 | none | **ACTIVE_MT5** | none |
| trend_pullback | ACTIVE_MT5 | none | **ACTIVE_MT5** | none |
| liquidity_sweep_reversal | ACTIVE_MT5 | none | **ACTIVE_MT5** | none |
| wyckoff | DISABLED (explicit, by design — zero live footprint pending validation) | none | **DISABLED** | none |

No strategy currently has a tripped circuit breaker (checked directly via `circuit_breaker.is_tripped()` for all 12).

**A second, independent, already-running evidence system agrees with several findings below.** `strategy_performance_monitor` (added 2026-08-18, `backend/mt5_strategies/performance_monitor.py`) recomputes real 14-day $P&L/expectancy every 6h and writes a `PENDING_REVIEW` recommendation — never auto-applied — when live evidence disagrees with current activation. Its **most recent snapshot (computed today) already recommends demoting `mtfai1` from ACTIVE_MT5 to SHADOW_MT5**: 140 real solo closed trades in the last 14 days, expectancy_r=-0.0924, realized $-1,104.12, win_rate=42.1% — below its -0.05R demotion threshold. This recommendation sits `PENDING_REVIEW`, un-actioned, at the time of this audit. (Note: `adaptive_position_states.strategy_id` also holds 36 legacy-cased `"MTFAI1"` rows the monitor's exact-match query misses — the true recent solo trade count is ~176, not 140; direction of the finding is unaffected.) No other strategy's automated recommendation currently disagrees with its activation (all others show `INSUFFICIENT_SAMPLE` under the 14-day/n≥20 bar, or already match).

---

## 1. Summary table

| Strategy | Core Edge | Entry Quality | Management | Best Regime | Weak Regime | Recent Trend | Root Cause | Recommendation |
|---|---|---|---|---|---|---|---|---|
| mtfai1 | Weak/negative | Mixed — 48% reach +1R before losing it back | Roughly neutral (fixed-R counterfactuals ≈ actual) | REVERSAL regime, HIGH/EXTREME ATR | BREAKOUT regime, SHORT direction | Improving marginally (2023 worst, 2024-26 flat-to-slightly-positive) but still net negative and now flagged by the automated monitor | Combination: weak core signal + unrestricted regime scope (only strategy with no regime gate) | KEEP_WITH_FILTERS (regime-gate it) or SHADOW_ONLY — automated monitor already recommends demotion |
| support_resistance_bounce | Decayed | Good historically (67% reach +0.25R) | Roughly neutral | EURJPY/GBPJPY, LOW ATR | GBPUSD, recent (2025-26) | Sharp decay: +0.19R (2023) → -0.06R (2025) → -0.31R (2026, n=5698) | Decayed edge, not implementation (single strategy_version spans whole corpus) | SHADOW_ONLY (already there) |
| momentum | No credible edge | Poor — 41% immediate-failure rate, worst of all 12 | Neutral-to-slightly-positive (actual ≈ cf1.5) | None found that survives sample scrutiny | All — negative in every symbol, session, regime | Collapsing (-0.10R in 2018-22 → -0.70R in 2025 → -0.78R in 2026) | Implementation + genuine decay both visible; already hard-deprecated today | RETIRE (already effectively done, 2026-08-21) |
| session_breakout | No credible edge | Poor — 42% immediate-failure rate | Neutral (actual ≈ cf1) | None credible after checking session/symbol/regime | Every session, every regime checked | Negative in 8 of 9 years; worst in 2025 (-0.59R) | No core edge — never positive in 9 years across any obvious split | RETIRE (already SHADOW_MT5; downgrade recommendation stands) |
| vwap_reversion | **Entry has real edge; exit destroys it** | Good — reached_1r 56.5%, best MFE-reach profile outside mean_reversion/wyckoff | **Poor — actual -0.060R vs all fixed-R counterfactuals positive (+0.13 to +0.15R)** | EURUSD, NORMAL/HIGH ATR | XAUUSD, LOW ATR | Decayed from +0.39R (2020) to marginal negative recently, but entry-only counterfactuals stay positive throughout | Management/target problem, not entry problem | IMPROVE_MANAGEMENT (highest-value target for redesign work) |
| breakout | No credible edge | Poor — 45% immediate-failure rate | Neutral (actual tracks all cf's) | None credible | Every regime/session negative | Collapsed: +0.02R (2020-23) → -0.75R (recent) | Genuine, broad decay — no rescuing subset found | RETIRE |
| ema_trend | No credible edge | Poor — 43% immediate-failure rate | Slightly positive vs cf1/cf1.5 (actual better) but still net negative | HIGH/EXTREME ATR, USDCAD | LOW ATR, EURUSD | Steady decline, then collapse (-0.84R 2025, -0.96R 2026 — near-total loss) | Genuine decay, regime-independent | RETIRE |
| mean_reversion | **Robust, consistent** | Strong — 74% reach +0.25R, immediate-failure only 26% (2nd-best) | Efficient — actual (+0.27R) sits between cf1 (+0.20) and cf1.5 (+0.31) | RANGING (as designed), all sessions similar | GBPJPY only clear negative symbol; EXTREME ATR weakest bucket | Improving further: +0.93R in 2026 (n=1286) | Genuine, well-explained edge (mean reversion in ranging regime) | KEEP |
| smc_continuation | Marginal/regime-dependent | Moderate | Efficient (actual close to cf1-cf2 band) | TRENDING regime (+0.21R) | BREAKOUT regime (-0.03R) | Improving (2026: +0.46R, n=1282, still low-n) | Regime-dependent edge, correctly weak overall | KEEP_WITH_FILTERS (restrict to TRENDING) |
| trend_pullback | Robust, improving | Good — 67% reach +0.25R | Efficient | USDCHF strongest; all-TRENDING by design | NZDUSD/GBPJPY negative, LOW ATR weak | Strongly improving (+1.31R 2025 n=805, +0.68R 2026 n=603) | Genuine, well-explained edge, still building live track record | KEEP |
| liquidity_sweep_reversal | Regime/symbol-dependent, decayed | Moderate | **Actual (-0.039R) beats every fixed-R counterfactual** — best-managed exits of all 12 | NZDUSD/EURJPY/USDCAD strongly positive | USDCHF/AUDUSD/EURUSD strongly negative | Decayed since 2020 peak; small sample now (n=137 in 2026) | Symbol-mix + genuine decay; management is actually a bright spot here | RESEARCH_REQUIRED (restrict to positive symbols first) |
| wyckoff | **Insufficient evidence, extreme skew** | Cannot separate from skew — reached_1r 56%, but expectancy driven by tail | N/A — real coverage too thin | USDJPY only (avg_r +3.03, n=1036) | GBPUSD, EURUSD (avg_r -0.51, -0.50) | Only 6 months of history (not 3 years); OOS >> train by an implausible 2728% — a symbol-concentration artifact, not edge growth | Single-symbol concentration masquerading as a whole-strategy edge | INSUFFICIENT_EVIDENCE — do not promote past SHADOW without symbol-level validation |

---

## 2. mtfai1

**Live status:** ACTIVE_MT5 (real money, all 4 accounts). Automated performance monitor **currently recommends demotion to SHADOW_MT5** (see §0). No regime gate — the only strategy of the 12 with an empty `regimes` tuple, i.e. it fires in every market condition.

### Historical-window performance

| Window | n (capped) | Gross Exp R | Net Exp R | PF gross/net | PSR/DSR | ESS | MC P95 DD (R) | Verdict | Walk-fwd |
|---|---|---|---|---|---|---|---|---|---|
| 2023-2026 (recent) | 8,000 | -0.0585 | -0.0750 | 0.912 / 0.889 | 6.7e-05 / 6.7e-05 | 4,251 | 966.6 | FRAGILE | FAIL (full corpus) |
| 2020-2023 | 8,000 | -0.2212 | -0.2413 | 0.682 / 0.660 | 9.0e-55 / 9.0e-55 | 6,531 | 2,043.7 | FRAGILE | FAIL |
| 2017-2020 | 8,000 | -0.0020 | -0.0200 | 0.997 / 0.968 | 0.447 / 0.447 | 5,263 | 503.2 | FRAGILE | FAIL |

Full corpus (all 966,197 resolved rows, 2018-02-28 → 2026-08-13): avg_r = **-0.0282**. Walk-forward (full corpus, train n=579,398 / OOS n=386,242): train -0.0189R → OOS -0.0426R, **directional_edge=NEUTRAL_EDGE, edge_stability=FAILED_OOS**. mtfai1 has never been solidly positive in any measured slice of its 8-year history.

### MAE/MFE (full corpus, n=966,197; 359,892 wins / 606,303 losses)

- Winners: avg MFE +2.60R, avg MAE only +0.35R (winners rarely give back much before the peak).
- Losers: avg MFE only +0.45R, avg MAE -1.77R (losers show real weakness early — not "close calls that reversed").
- reached_0.25r 69.8%, reached_0.5r 61.1%, reached_1r 47.9%, reached_2r 12.3%.
- **immediate_failure_rate 30.2%** — nearly a third of entries move hard against the position with no favorable excursion first. This is a genuine entry-quality signal, not purely a management issue.
- 10.7% of all trades reach +1R and still end up losing — real but secondary giveback.

### Symbol segmentation (full corpus)

| Symbol | n | avg_r | win_rate |
|---|---|---|---|
| XAUUSD | 92,372 | **+0.095** | 42.4% |
| EURUSD | 117,729 | +0.075 | 41.0% |
| USDJPY | 109,533 | +0.010 | 38.5% |
| GBPUSD | 124,242 | -0.003 | 38.0% |
| USDCAD | 87,314 | -0.076 | 35.5% |
| AUDUSD | 90,158 | -0.086 | 35.2% |
| EURJPY | 84,808 | -0.053 | 36.4% |
| USDCHF | 86,736 | -0.122 | 33.7% |
| **NZDUSD** | 87,556 | **-0.165** | 32.1% |

Genuine but modest symbol dispersion (~0.26R spread top-to-bottom on n>85k each side — statistically real, not noise, but not large enough to flip the strategy's overall sign by itself).

### Regime / session / direction segmentation (full corpus)

- **By regime_broad:** REVERSAL best (+0.012R, n=325,603), RANGING flat (-0.0003R), TRENDING slightly negative (-0.015R), **BREAKOUT worst (-0.103R, n=187,298)**, UNKNOWN worst-of-all (-0.107R).
- **By session:** all four sessions cluster tightly negative-to-flat (-0.007R to -0.061R) — session is not a meaningful lever for mtfai1.
- **By ATR regime:** HIGH (+0.027R) and EXTREME (+0.001R) beat NORMAL (-0.022R) and LOW (-0.062R) — mtfai1 does marginally better in more volatile conditions.
- **By direction:** LONG +0.023R vs SHORT **-0.079R** — a real, large asymmetry (n≈485k each side). Shorts are the weaker half of this strategy by a wide margin.

None of these splits, even stacked (e.g. LONG + REVERSAL + XAUUSD), would plausibly clear ~+0.15R with adequate n to matter economically — this is genuine broad weakness, not a hidden pocket.

### Entry-vs-management decomposition

| Counterfactual | Coverage | Expectancy |
|---|---|---|
| Actual (full corpus) | 100% | -0.028R |
| Fixed 1R | 100% | -0.041R |
| Fixed 1.5R | 100% | -0.033R |
| Fixed 2R | 74.5% (undetermined 25.5%) | -0.506R |

Actual, presumably-variable exit geometry is **slightly better** than either fixed-1R or fixed-1.5R alternatives — mtfai1's exit design is not the problem; a naive fixed target would not rescue it. Reaching for 2R specifically is a bad idea (steep negative, though only 74.5%-covered).

### ATM-managed real trades (n=176, 2026-08-07 → 2026-08-21, insufficient→low sample under this codebase's own convention)

avg_managed_r = **-0.075**, median -0.252, win_rate 40.3%. Directionally consistent with the historical corpus (negative), though this is only two weeks of real, ATM-managed data and should not be treated as an independent confirmation — it is the same underlying weak edge showing up again, not new evidence.

### Confidence engine

mtfai1 is not broken out separately in the cross-strategy confidence-calibration report (see §0 for the aggregate, which is itself weakly calibrated — band non-monotonic, overall r=0.06). No strategy-specific confidence read is available from the historical corpus (`confidence_band` is null there — see §0).

### Time-decay / implementation history

`strategy_version` is a single value (`replay-v2-point-in-time-integrity`) across the entire 2018-2026 corpus — the replay engine used **one consistent logic definition for the whole backtest**, so year-to-year swings are not a version-mixing artifact. However, mtfai1's *live* code has changed substantially this week, independent of the corpus: `01c2772`/`e4fa1cc`/`3770370`/`b079b86`/`849afc7` (2026-08-17→18, activation-gate wiring, trade-frequency caps, standalone-confirmation kill switch, HH/LL and EQH/EQL structure confirmation) and `52dbdc9`/`5d55bf3` (2026-08-19, re-entry blocking, cross-account concurrent-exposure blocking) — none of which the corpus (last replayed 2026-08-20 07:48, i.e. *after* these commits landed but the replay logic itself doesn't necessarily exercise every new live-only gate identically) can be assumed to fully reflect. Treat mtfai1's forward behavior as partially untested by this audit.

### Classification

**IMPLEMENTATION_PROBLEM leaning NO_CREDIBLE_EDGE.** The strategy has never been solidly positive in 8+ years of history, in the full corpus or in either regenerated 3-year window; its one structural advantage (no regime gate) is also its biggest liability (it fires into BREAKOUT regimes, its worst bucket, at full frequency). Direction (LONG vs SHORT) and regime (REVERSAL vs BREAKOUT) are the two most credible levers, but neither is large enough on its own to flip the sign.

**Recommendation: KEEP_WITH_FILTERS if the direction/regime split is implemented and revalidated, otherwise SHADOW_ONLY.** The already-running automated monitor independently reached the same demotion conclusion from real 14-day P&L. **Risk of the proposed filter:** LONG-only + REVERSAL-only would cut mtfai1's volume roughly 60-70%, and its historically-large footprint (~half the corpus) means this filter is itself only validated in-sample against the same data it was mined from — it should be walk-forward-validated on its own before being trusted, not deployed on today's finding alone.

---

## 3. support_resistance_bounce

**Live status:** SHADOW_MT5 (demoted 2026-08-17, per commit history).

### Historical-window performance

| Window | n | Gross Exp R | Net Exp R | PF gross/net | PSR/DSR | ESS | MC P95 DD (R) | Verdict | Walk-fwd |
|---|---|---|---|---|---|---|---|---|---|
| 2023-2026 | 8,000 | -0.1461 | -0.1764 | 0.791 / 0.755 | 0.000 / 0.000 | 5,377 | 1,525.0 | FRAGILE | PASS (full corpus, STRONG) |
| 2020-2023 | 8,000 | +0.2870 | +0.2390 | 1.525 / 1.415 | 1.000 / 1.000 | 3,430 | 102.0 | ROBUST | PASS |
| 2017-2020 | 8,000 | +0.2100 | +0.1700 | 1.367 / 1.284 | 1.000 / 1.000 | 3,713 | 128.6 | ROBUST | PASS |

Full-corpus walk-forward: train n=168,656 (+0.0435R) → OOS n=112,364 (**+0.1068R** — OOS actually beats train), `edge_stability=STRONG`, but `directional_edge=UNSTABLE` — the sign-agreement classifier (which requires both windows to agree, not just the retention-fraction check) does **not** call this a stable positive edge, which matches the visible year-to-year swings below.

### MAE/MFE (full corpus, n=281,159; 106,067 wins / 175,092 losses)

- Winners: avg MFE +2.74R, avg MAE only +0.22R.
- Losers: avg MFE +0.53R, avg MAE -2.06R.
- reached_0.25r 67.4%, reached_1r 51.8% — a genuinely strong entry profile, second only to mean_reversion/wyckoff/vwap_reversion among the 12.
- immediate_failure_rate 32.6% — moderate, not the worst.

### Symbol segmentation

Best: GBPJPY +0.212R (n=28,005), EURJPY +0.191R (n=28,043), NZDUSD +0.152R (n=24,715). Worst: **GBPUSD -0.097R (n=36,516, its single largest symbol)**, USDCAD -0.055R (n=22,080). Genuinely wide dispersion (~0.31R top-to-bottom, all n>20k) — support/resistance bounce logic works meaningfully better on JPY/commodity-adjacent pairs than on GBPUSD specifically.

### Regime / session / ATR segmentation

By regime_broad: RANGING (as designed) +0.031R (n=218,606); UNKNOWN (regime undetectable) an outsized **+0.201R** (n=62,553) — plausible but worth treating cautiously since "UNKNOWN" regime rows are a residual bucket, not a designed target. By ATR: LOW +0.176R (best, matches the strategy's design intent) vs NORMAL -0.024R. By session: LONDON/NY_OVERLAP mildly best (+0.09R), all sessions positive-to-flat. By direction: **SHORT +0.141R vs LONG -0.026R** — a real asymmetry, same pattern seen in mtfai1.

### Entry-vs-management decomposition

| Counterfactual | Coverage | Expectancy |
|---|---|---|
| Actual | 100% | +0.069R |
| Fixed 1R | 100% | +0.036R |
| Fixed 1.5R | 100% | +0.092R |
| Fixed 2R | 90.0% | -0.059R |

Actual sits between cf1 and cf1.5 — reasonably efficient exit capture, roughly matching a ~1.2R-equivalent fixed target. Reaching for 2R is a bad idea here too.

### Time-decay analysis — this is the headline finding for this strategy

Yearly avg_r: 2018 -0.031, 2019 +0.023, 2020 +0.114, 2021 +0.066, 2022 +0.020, 2023 **+0.191**, 2024 +0.162, **2025 -0.057 (n=13,620), 2026 -0.315 (n=5,698)**. A strategy that was robustly positive for 6 straight years broke down abruptly starting 2025. `strategy_version` is a single consistent value across the whole span — **this is not an implementation-change artifact; it is a genuine, recent market-behavior shift** (matches the briefing's independent finding). The 2026 sample (n=5,698, through Aug) is materially worse than 2025, suggesting the decay is ongoing, not a one-year blip.

### ATM-managed real trades (n=28, 2026-08-11→17, insufficient sample)

avg_managed_r -0.116, win_rate 32.1% — consistent with the 2025-26 corpus decay, though n=28 in a 6-day window is far too thin to add independent weight.

### Classification

**DECAYED_EDGE.** Six years of genuine, real, walk-forward-supported robustness (2018-2024) followed by a sharp, version-independent breakdown starting 2025 that has continued into 2026. GBPUSD is a persistently weak symbol worth excluding regardless of the timing question; SHORT-only is a real, large lever (+0.141R vs -0.026R LONG).

**Recommendation: SHADOW_ONLY (already there — correct call).** Do not reinstate to ACTIVE_MT5 without either (a) evidence the 2025-26 regime shift has reversed, or (b) a SHORT-only, ex-GBPUSD restriction re-validated out-of-sample. **Risk of restricting now:** the SHORT/GBPUSD-exclusion filter is itself mined from the same full-history data being used to diagnose it — it needs its own OOS check before being trusted, and given the strategy is currently decaying, a filter tuned on 2018-2024 data may not survive whatever changed in 2025.

---

## 4. momentum

**Live status:** Nominally ACTIVE_MT5 in the `STRATEGY_FAMILIES` registry, but **hard-blocked today (2026-08-21)** by a dedicated code-level circuit breaker (`MT5_MOMENTUM_STRATEGY_ENABLED`, default False) — `evaluate_momentum()` now returns `STRATEGY_DEPRECATED_PENDING_REBUILD` before any RSI/MACD computation runs, independent of the activation registry. The strategy generates zero live signals as of this audit.

### Historical-window performance

| Window | n | Gross Exp R | Net Exp R | PF gross/net | PSR/DSR | ESS | MC P95 DD (R) | Verdict | Walk-fwd |
|---|---|---|---|---|---|---|---|---|---|
| 2023-2026 | 8,000 | -0.7607 | -0.7791 | 0.164 / 0.160 | 0.000 / 0.000 | 1,717 | 6,352.7 | FRAGILE | FAIL |
| 2020-2023 | 8,000 | -0.0090 | -0.0330 | 0.986 / 0.950 | 0.274 / 0.274 | 3,351 | 580.9 | FRAGILE | FAIL |
| 2017-2020 | 8,000 | -0.0970 | -0.1200 | 0.853 / 0.822 | 0.000 / 0.000 | 3,059 | 1,224.0 | FRAGILE | FAIL |

Full-corpus walk-forward: train -0.117R → OOS **-0.223R**, `directional_edge=NEGATIVE_EDGE` — one of only two strategies (with breakout) the classifier calls reproducibly negative rather than merely unstable.

### MAE/MFE (full corpus, n=228,908; 72,183 wins / 156,725 losses)

**immediate_failure_rate 40.9% — the worst of all 12 strategies.** reached_0.25r only 59.1%, reached_1r 41.8% — both among the lowest. Losers' median MFE is 0.014R (essentially never move favorably at all before losing). This is a genuine entry-quality failure, not a management problem — the signal itself frequently identifies moments where price is about to reverse, not continue.

### Symbol / session / regime segmentation

Negative on **every single symbol** (best: XAUUSD -0.023R n=18,616; worst: EURUSD -0.336R n=20,127). Negative on **every session** (best LONDON -0.105R; worst OTHER -0.381R). Only two regime_broad buckets exist for momentum (TRENDING -0.195R, BREAKOUT -0.111R) — both negative. By ATR: EXTREME/HIGH least-bad (-0.088R/-0.094R) but still solidly negative.

### Entry-vs-management decomposition

| Counterfactual | Coverage | Expectancy |
|---|---|---|
| Actual | 100% | -0.159R |
| Fixed 1R | 100% | -0.165R |
| Fixed 1.5R | 100% | -0.147R |
| Fixed 2R | 82.5% | -0.478R |

All four numbers cluster tightly negative — no exit-design choice rescues this strategy. The problem is the entry.

### Time-decay

Yearly avg_r: 2018-22 hovers -0.07R to -0.13R (mildly negative, arguably tolerable), then **collapses**: 2023 -0.070, 2024 -0.193, **2025 -0.701 (n=11,698), 2026 -0.778 (n=5,950)**. A genuinely deteriorating strategy, not merely a chronically-weak one.

### Any credible positive subset? (persistent-negative check)

Checked (a-priori, not mined): by session (5 checked, all negative), by symbol (10 checked, all negative), by regime (2 checked, both negative), by direction (LONG -0.121R n=101,852 vs SHORT -0.190R n=127,056 — both negative, LONG merely less bad), by ATR regime (4 checked, all negative). **No credible positive subset exists.** Even the least-bad corner (XAUUSD, LONG, HIGH-ATR) would need to be independently re-queried with a joint filter to have any n at all, and given every single dimension checked individually is negative, a joint intersection is very unlikely to flip sign with adequate n. Not pursued further per the audit's explicit anti-p-hacking instruction.

### Classification

**NO_CREDIBLE_EDGE, with a secondary IMPLEMENTATION_PROBLEM component** (the code's own docstring calls this "a code-level deprecation circuit breaker... the audit's directive is retire-or-rebuild, and a rebuild is explicitly out of scope" — i.e. Bensim's own engineering already reached this conclusion independently, today, before this document was written).

**Recommendation: RETIRE (already effectively done as of 2026-08-21).** No action needed from this audit; documenting for completeness. If a rebuild is ever pursued, the immediate-failure-rate finding (40.9%) says the entry trigger itself needs redesign, not just tighter filters.

---

## 5. session_breakout

**Live status:** SHADOW_MT5.

### Historical-window performance

| Window | n | Gross Exp R | Net Exp R | PF gross/net | PSR/DSR | ESS | MC P95 DD (R) | Verdict | Walk-fwd |
|---|---|---|---|---|---|---|---|---|---|
| 2023-2026 | 8,000 | -0.3757 | -0.3989 | 0.539 / 0.521 | 1.8e-98 / 1.8e-98 | 1,751 | 3,515.4 | FRAGILE | FAIL |
| 2020-2023 | 8,000 | -0.0440 | -0.0700 | 0.938 / 0.904 | 0.005 / 0.005 | 3,130 | 909.5 | FRAGILE | FAIL |
| 2017-2020 | 8,000 | -0.0520 | -0.0810 | 0.927 / 0.890 | 0.0012 / 0.0012 | 3,497 | 874.4 | FRAGILE | FAIL |

Full-corpus walk-forward: train -0.132R → OOS -0.129R, `edge_stability=FAILED_OOS`, `directional_edge=NEGATIVE_EDGE` (degradation only +1.9%, i.e. consistently, reproducibly negative rather than unstable).

### MAE/MFE (full corpus, n=97,661; 25,652 wins / 72,009 losses)

**immediate_failure_rate 42.0% — second-worst of all 12** (after momentum). reached_0.25r 58.0%, reached_1r 43.5% — both weak. 17.2% of trades reach +1R and still lose (worse giveback than most strategies).

### Symbol/session/regime/direction dependence, and any credible positive subset (persistent-negative check)

- **By symbol:** every single symbol negative except GBPJPY (+0.145R, n=6,519) and EURJPY (+0.002R, n=7,133, essentially breakeven). Worst: EURUSD -0.325R (n=10,056), GBPUSD -0.207R (n=13,332, its largest symbol).
- **By session:** every session negative (best NY_OVERLAP -0.070R, worst ASIAN -0.184R).
- **By regime_broad:** both buckets negative (BREAKOUT -0.170R — its designed target regime — UNKNOWN -0.049R).
- **By direction:** both negative (LONG -0.094R, SHORT -0.163R).
- **By ATR:** HIGH least-bad (-0.044R, n=9,520) but still negative; LOW worst (-0.213R).

**GBPJPY is the one genuinely positive corner** (+0.145R, n=6,519 — a real sample, not noise). Every other combination checked is negative. This matches the briefing's own illustrative example almost exactly ("London breakout + high volatility + EURUSD" was hypothesized; the real data instead points to GBPJPY specifically, not EURUSD, and not clearly tied to London/high-vol). A GBPJPY-only version is the single most defensible restriction found for this strategy, but it has not been cross-checked against session/regime jointly (that would fragment n too far to trust) — reported honestly as one un-stacked positive corner, not a validated combined filter.

### Time-decay

Never positive in any full year 2018-2024 (worst -0.192R in 2024, best -0.011R in 2022). **2025 -0.589R (n=3,928)** was the single worst year in the strategy's history, with 2026 recovering somewhat to -0.135R (n=3,832) but still negative.

### Entry-vs-management

Fixed 1R (-0.130R) ≈ actual (-0.130R) — essentially no information gained; exit design is neither helping nor hurting.

### Classification

**NO_CREDIBLE_EDGE at the whole-strategy level; one un-stacked positive symbol corner (GBPJPY) worth flagging but not yet validated as a standalone filter.** Never positive in 9 years in aggregate, and every session/regime/direction split checked is negative except that one symbol.

**Recommendation: RETIRE at the whole-strategy level; if kept, SHADOW_ONLY restricted to GBPJPY pending its own OOS validation** — do not promote based on this single-symbol finding alone (classic single-comparison risk: 1 positive result out of 10 symbols checked is roughly the base rate you'd expect from noise at these effect sizes, so this needs independent confirmation before being trusted as a real, symbol-specific edge rather than the corpus's own version of a lucky draw).

---

## 6. vwap_reversion — highest-value finding of this audit

**Live status:** ACTIVE_MT5, no override.

### Historical-window performance

| Window | n | Gross Exp R | Net Exp R | PF gross/net | PSR/DSR | ESS | MC P95 DD (R) | Verdict | Walk-fwd |
|---|---|---|---|---|---|---|---|---|---|
| 2023-2026 | 8,000 | -0.0665 | -0.0921 | 0.915 / 0.886 | 0.0015 / 0.0015 | 2,618 | 1,217.5 | FRAGILE | PASS (ACCEPTABLE) |
| 2020-2023 | 8,000 | -0.2240 | -0.2760 | 0.735 / 0.690 | 0.000 / 0.000 | 4,186 | 2,376.9 | FRAGILE | PASS |
| 2017-2020 | 8,000 | +0.1890 | +0.1460 | 1.245 / 1.181 | 1.000 / 1.000 | 3,338 | 317.4 | ROBUST | PASS |

Full-corpus walk-forward: train **-0.111R → OOS +0.015R** (degradation +113%, i.e. OOS is *better* than train), `edge_stability=ACCEPTABLE`, `directional_edge=UNSTABLE`.

### MAE/MFE (full corpus, n=96,089; 18,675 wins / 77,414 losses) — the strongest MFE profile of any strategy checked

- Winners: avg MFE **+4.79R** (by far the largest of any strategy — vwap_reversion winners run further than any other strategy's), avg MAE only +0.28R.
- reached_0.25r **71.2%**, reached_0.5r 66.7%, reached_1r **56.5%** — second only to wyckoff/mean_reversion.
- immediate_failure_rate 28.8% — one of the lower rates.
- 37.1% of trades reach +1R and still end up losing — the **highest giveback rate of any of the 12 strategies** (mean_reversion, by contrast, is 9.3%). This one number is the whole story: entries are excellent, but whatever happens after +1R is reached is destroying value at a far higher rate than any other strategy.

### Entry-vs-management decomposition — the decisive evidence

| Counterfactual | Coverage | Expectancy |
|---|---|---|
| **Actual** | 100% | **-0.060R** |
| Fixed 1R | 100% | **+0.130R** |
| Fixed 1.5R | 100% | **+0.151R** |
| Fixed 2R | 99.1% | **+0.140R** |

**Every single fixed-R alternative checked is solidly positive, while the actual realized outcome is negative.** This is the clearest entry/management split found anywhere in this audit: the entry signal has genuine, robust predictive value (confirmed independently by the MFE/reached-rate profile above), and whatever the current exit/target logic actually does is converting a real edge into a loss. This is not a close call — the gap is 0.19-0.21R, roughly 3x the strategy's own current negative expectancy.

### Symbol / regime / session segmentation

Best: EURUSD +0.497R (n=8,848, its smallest symbol by volume — worth noting), USDCAD +0.192R (n=6,243), GBPJPY +0.093R. Worst: **XAUUSD -0.312R (n=13,612, its largest symbol)**, GBPUSD -0.182R, USDJPY -0.167R. By ATR: NORMAL +0.058R and HIGH +0.036R both positive; **LOW -0.157R** is where most of the volume sits (n=50,008 of 96,089) and where the strategy loses the most.

### Time-decay

2018 -0.16R, 2019 -0.30R, **2020 +0.39R**, 2021 -0.23R, **2023 +0.18R**, 2024 -0.05R, 2025 -0.14R, 2026 -0.08R — genuinely volatile year-to-year (not a clean monotonic decay like support_resistance_bounce), consistent with `directional_edge=UNSTABLE`. This makes the entry/exit decomposition finding above more important, not less: the year-to-year noise in the *actual* series may itself partly be an artifact of exit-timing variance rather than a genuinely shifting entry edge.

### Classification

**MANAGEMENT_PROBLEM — the clearest case of the whole audit.** Entry quality is demonstrably good (best-in-class MFE profile, second-best reach-rate); the realized outcome is negative purely because of exit/target design, evidenced by every fixed-R counterfactual beating the actual by 0.19-0.21R.

**Recommendation: IMPROVE_MANAGEMENT — highest-priority redesign candidate in this audit.** Concretely: investigate why 37% of trades that reach +1R still end up losing (partial-profit-taking or a tighter trailing stop past +1R is the obvious first experiment, consistent with the fixed-1R/1.5R counterfactuals both beating actual by a wide margin). Also worth an XAUUSD-specific review (largest symbol, most negative) and reducing exposure in LOW-ATR conditions (largest bucket, most negative). **Risk of the proposed change:** this conclusion rests on the reached_Xr/sl_hit counterfactual approximation (not a full tick-level replay of an actual "exit at +1R" rule), and a redesigned exit rule would need its own forward validation — a naive "always exit at first touch of +1R" rule could itself be an overfit read of this specific counterfactual construction rather than a true optimum; the counterfactual only shows headroom exists, not the exact rule to capture it.

---

## 7. breakout

**Live status:** ACTIVE_MT5, no override.

### Historical-window performance

| Window | n | Gross Exp R | Net Exp R | PF gross/net | PSR/DSR | ESS | MC P95 DD (R) | Verdict | Walk-fwd |
|---|---|---|---|---|---|---|---|---|---|
| 2023-2026 | 8,000 | -0.7465 | -0.7693 | 0.188 / 0.182 | 2.4e-250 / 2.4e-250 | 1,280 | 6,295.0 | FRAGILE | FAIL |
| 2020-2023 | 8,000 | +0.0220 | -0.0110 | 1.032 / 0.984 | 0.902 / 0.902 | 3,395 | 475.5 | FRAGILE | FAIL |
| 2017-2020 | 8,000 | -0.1800 | -0.2090 | 0.760 / 0.730 | 0.000 / 0.000 | 2,796 | 1,837.1 | FRAGILE | FAIL |

Full-corpus walk-forward: train -0.150R → OOS **-0.234R**, `directional_edge=NEGATIVE_EDGE` — reproducibly negative, like momentum.

### MAE/MFE (full corpus, n=94,731; 23,413 wins / 71,318 losses)

immediate_failure_rate 45.1% — worst of all 12 strategies (worse even than momentum). reached_0.25r 54.9%, reached_1r 40.2% — both weak.

### Symbol / session / regime

Negative on 8 of 10 symbols; the two exceptions are USDCAD (+0.008R, essentially breakeven, n=9,206) and GBPJPY (+0.096R, n=8,562). Worst: EURUSD -0.474R (n=9,732). By regime_broad: BREAKOUT itself -0.209R (its designed target, and worst regime), TRENDING -0.149R, UNKNOWN -0.034R. All sessions negative.

### Entry-vs-management

| Counterfactual | Coverage | Expectancy |
|---|---|---|
| Actual | 100% | -0.184R |
| Fixed 1R | 100% | -0.197R |
| Fixed 1.5R | 100% | -0.192R |
| Fixed 2R | 97.5% | -0.208R |

Tightly clustered negative — no exit choice rescues this strategy; the entry itself lacks edge.

### Time-decay

2018 -0.24R, oscillates -0.08R to -0.19R through 2023, then **collapses**: 2024 -0.275R, **2025 -0.700R (n=3,955), 2026 -0.708R (n=2,292)**. Genuine, severe recent decay (its own recent-window PSR of 2.4e-250 reflects how extreme this collapse is).

### Any credible positive subset? (broad-negative strategy, not one of the three named persistent-negatives, but treated the same way for honesty)

GBPJPY (+0.096R, n=8,562) and USDCAD (≈breakeven, n=9,206) are the only two symbols that aren't clearly negative — neither is large enough or positive enough to justify keeping the strategy live on that basis alone, especially given the severe 2024-26 collapse dominates recent evidence regardless of symbol.

### Classification

**NO_CREDIBLE_EDGE / DECAYED_EDGE (both apply — was marginal 2020-23, has since collapsed).**

**Recommendation: RETIRE.** The 2024-26 collapse is severe and broad (PF gross 0.19 in the recent window — losing roughly 5x more than it wins), immediate-failure rate is the worst of all 12, and no fixed-R exit alternative helps. If revisited later, GBPJPY is the only symbol with any positive signal, but on its own volume (n=8,562, one symbol) it does not justify keeping the strategy active strategy-wide.

---

## 8. ema_trend

**Live status:** ACTIVE_MT5, no override.

### Historical-window performance

| Window | n | Gross Exp R | Net Exp R | PF gross/net | PSR/DSR | ESS | MC P95 DD (R) | Verdict | Walk-fwd |
|---|---|---|---|---|---|---|---|---|---|
| 2023-2026 | 8,000 | -0.7791 | -0.7980 | 0.159 / 0.153 | 1.9e-271 / 1.9e-271 | 830 | 6,551.5 | FRAGILE | FAIL |
| 2020-2023 | 8,000 | -0.0920 | -0.1150 | 0.868 / 0.839 | 0.000 / 0.000 | 2,914 | 1,158.4 | FRAGILE | FAIL |
| 2017-2020 | 8,000 | +0.0900 | +0.0660 | 1.142 / 1.101 | 1.000 / 1.000 | 2,338 | 282.1 | ROBUST | FAIL |

Full-corpus walk-forward: train -0.050R → OOS **-0.254R** (degradation -410%), `directional_edge=UNSTABLE`. Note the briefing's own 2017-2020 ROBUST read already carried a walk-forward FAIL caveat — this strategy has never had genuinely stable OOS support even in its best-looking window.

### MAE/MFE (full corpus, n=90,856; 26,319 wins / 64,537 losses)

immediate_failure_rate 43.3%. Only one regime_broad bucket exists for ema_trend (TRENDING, by design) — meaning there is no regime lever to pull; the whole strategy lives in one regime bucket, and that bucket is worth -0.131R.

### Symbol / session

Best: USDCAD +0.002R (essentially flat, n=9,025), XAUUSD -0.027R. Worst: **EURUSD -0.458R (n=6,831)**. All sessions negative; NY and OTHER worst (-0.25R/-0.29R), LONDON/NY_OVERLAP least-bad (-0.06R). By ATR: HIGH/EXTREME (-0.04R/-0.02R) far better than LOW (-0.28R) — a real, exploitable-looking split, but not enough to flip the strategy positive even in the best ATR bucket.

### Entry-vs-management

| Counterfactual | Coverage | Expectancy |
|---|---|---|
| Actual | 100% | -0.131R |
| Fixed 1R | 100% | -0.174R |
| Fixed 1.5R | 100% | -0.145R |
| Fixed 2R | 100% | -0.127R |

Actual is comparable to or slightly better than the fixed alternatives — exit design is not the problem here.

### Time-decay

Oscillates -0.19R to +0.09R through 2023, then **near-total collapse**: 2024 -0.14R, **2025 -0.84R (n=4,041), 2026 -0.96R (n=1,883, win_rate only 1.3%)**. The 2026 figure (98.7% loss rate) is one of the most extreme numbers in this entire audit.

### Classification

**DECAYED_EDGE / NO_CREDIBLE_EDGE.** Marginal at best historically (and never walk-forward-stable even then), now in a severe, ongoing collapse with a near-total 2026 loss rate.

**Recommendation: RETIRE.** No symbol, session, or ATR split found comes close to rescuing a strategy whose most recent full year (2026, n=1,883) shows a 1.3% win rate.

---

## 9. mean_reversion — deep inspection per the brief's explicit request

**Live status:** ACTIVE_MT5, no override.

### Historical-window performance

| Window | n | Gross Exp R | Net Exp R | PF gross/net | PSR/DSR | ESS | MC P95 DD (R) | Verdict | Walk-fwd |
|---|---|---|---|---|---|---|---|---|---|
| 2023-2026 | 8,000 | +0.5563 | +0.5226 | 2.474 / 2.333 | 1.000 / 1.000 | 1,716 | 70.0 | ROBUST | PASS (STRONG) |
| 2020-2023 | 8,000 | +0.2330 | +0.2000 | 1.461 / 1.381 | 1.000 / 1.000 | 2,919 | 95.6 | ROBUST | PASS |
| 2017-2020 | 8,000 | +0.3950 | +0.3460 | 1.894 / 1.750 | 1.000 / 1.000 | 2,532 | 68.5 | ROBUST | PASS |

Full-corpus walk-forward: train +0.185R → OOS **+0.401R**, `edge_stability=STRONG`, `directional_edge=POSITIVE_EDGE` — the only one of the 12 strategies with both edge_stability=STRONG and directional_edge=POSITIVE_EDGE at the whole-corpus level, and it holds in every window re-run this session. Recent-window MC drawdown (P95, R-space) is 70.0 — one to two orders of magnitude smaller than every negative-edge strategy's, and recovery_factor +8.04.

### Where the edge comes from

Designed for RANGING/LOW_VOLATILITY regimes (`regimes=("ranging","low_volatility")` in `STRATEGY_FAMILIES`), and the data matches the design: RANGING +0.265R (n=39,987), and it is the single most symbol-consistent strategy in the audit — **8 of 10 symbols positive**, several strongly so (AUDUSD +0.581R n=4,745, EURUSD +0.551R n=6,250, GBPUSD +0.329R n=6,531). Sessions are similarly consistent (+0.23R to +0.36R across all five, no weak session). MFE/MAE profile is the best-behaved of any strategy: winners reach +0.25R 74.3% of the time, immediate_failure_rate only 25.7% (2nd-lowest of 12), and only 9.3% of trades that reach +1R still end up losing (the lowest giveback rate of all 12, vs vwap_reversion's 37.1% — the sharpest possible contrast within this audit).

### Where it's weaker

- **GBPJPY is the one clearly negative symbol: -0.155R (n=5,159)** — a real, credible sample, not noise, and worth excluding.
- **EXTREME ATR regime is the weakest bucket (+0.088R, n=2,268)** — still positive, but the edge compresses substantially outside its designed LOW/NORMAL-volatility home turf.
- Entry-vs-management: actual (+0.272R) sits between fixed-1R (+0.203R) and fixed-1.5R (+0.306R) — a well-behaved, efficient exit, not over- or under-capturing relative to a sensible fixed target. The fixed-2R counterfactual (+0.034R, only 73.7% coverage) confirms reaching for 2R is not where this strategy's edge lives — it is a shorter-duration, higher-hit-rate strategy by design (win_rate ~51% across the corpus, well above every negative strategy's ~25-38%).

### Drawdown behavior, cost sensitivity, dependence on a small period

Max drawdown (recent window, R-space bootstrap p95) is 70.0R against a positive terminal-R distribution entirely above zero at every percentile checked (p5 = +3,997R over 8,000 trades) — the strategy's own worst-case simulated paths in this session's Monte Carlo never went net negative. Cost drag is modest and consistent across windows (+0.034R recent, +0.034R prior-3y) — net PF stays above 2.3 in the recent window even after costs. Yearly trend: **not** dependent on one lucky period — positive in every full year 2018-2025 (range +0.18R to +0.52R) and continuing to improve (2026: **+0.93R, n=1,286**, though that partial year should be read as still-building evidence, not a new steady-state).

### Confidence engine relationship

Not separable at the corpus level (confidence_band is null there, per §0); the aggregate cross-strategy confidence-calibration read (§0) shows the engine is weakly calibrated overall (band non-monotonic, overall r=0.06), so mean_reversion's edge should be read as coming from the strategy's own logic, not from confidence-based filtering on top of it.

### Classification

**ROBUST_CORE_EDGE.** This is the strongest, most consistently explainable strategy in the audit: a designed-for-ranging-markets logic that performs as designed (best in RANGING/LOW_VOLATILITY, weakest in EXTREME_ATR), consistent across 8 of 10 symbols and all five sessions, walk-forward POSITIVE_EDGE + STRONG at the whole-corpus level, improving rather than decaying, and with an efficient (not over- or under-managed) exit profile.

**Recommendation: KEEP.** Only structural refinement suggested by the evidence: exclude or down-weight GBPJPY (the one clearly negative symbol, real n) and consider reduced size in EXTREME_ATR conditions (weakest bucket, though still positive). **Risk of overfitting this refinement:** GBPJPY is one symbol out of ten checked — a single negative symbol among an otherwise-uniform positive strategy is plausible as genuine symbol-specific weakness (support_resistance_bounce and vwap_reversion both also show GBP-pair weakness, a cross-strategy pattern that adds some credibility) but should still be spot-checked against a symbol-specific walk-forward before excluding it live.

---

## 10. smc_continuation

**Live status:** ACTIVE_MT5, no override.

### Historical-window performance

| Window | n | Gross Exp R | Net Exp R | PF gross/net | PSR/DSR | ESS | MC P95 DD (R) | Verdict | Walk-fwd |
|---|---|---|---|---|---|---|---|---|---|
| 2023-2026 | 8,000 | +0.0579 | +0.0367 | 1.089 / 1.056 | 0.9999 / 0.9999 | 2,276 | 330.1 | FRAGILE (bootstrap CI crosses zero) | PASS (STRONG) |
| 2020-2023 | 8,000 | +0.0973 | +0.0737 | 1.153 / 1.114 | 1.000 / 1.000 | 2,127 | 253.2 | ROBUST | PASS |
| 2017-2020 | 8,000 | -0.2370 | -0.2600 | 0.682 / 0.659 | 0.000 / 0.000 | 2,066 | 2,316.1 | FRAGILE | PASS |

Full-corpus walk-forward: train +0.024R → OOS +0.078R, `edge_stability=STRONG`, but `directional_edge=UNSTABLE` — matches the pattern of a real-but-modest, regime-dependent edge rather than a robust whole-strategy one. Note the recent-window verdict flipped to FRAGILE (bootstrap CI includes zero, `p_positive=0.962` — close but not quite the 0.95 PSR bar) even though PSR itself is 0.9999 — a genuine borderline case, not manufactured.

### Regime dependence — the key finding

**By regime_broad: TRENDING +0.210R (n=13,867) vs BREAKOUT -0.027R (n=32,397, the larger bucket).** This is a real, credible, meaningfully-sized split — smc_continuation ("continuation" is in the name) genuinely performs better when the market is already trending than when it is breaking out, and the BREAKOUT bucket (70% of its volume) is what drags the whole-strategy number down toward zero.

### Symbol / session / MAE-MFE

Best symbols: XAUUSD +0.272R (n=4,565), EURJPY +0.137R (n=3,638). Weakest: AUDUSD -0.077R (n=3,961), GBPJPY -0.055R (n=3,713) — real but modest dispersion, no symbol is dramatically better or worse. reached_0.25r 67.2%, immediate_failure_rate 32.8% — a middling, unremarkable entry profile. Entry-vs-management: actual (+0.044R) sits inside the cf1 (+0.021R)/cf2 (+0.050R) band — an efficient, unremarkable exit, not a source of either strength or weakness.

### Time-decay

Volatile, not monotonic: 2018 +0.15R, 2019 -0.17R, 2020 -0.06R, 2021 +0.20R, 2022 +0.07R, 2023 +0.13R, 2024 -0.07R, 2025 +0.08R, **2026 +0.46R (n=1,282)** — an improving recent trend, but the year-to-year noise (matches `directional_edge=UNSTABLE`) means the 2026 figure alone should not be over-read.

### Classification

**REGIME_DEPENDENT_EDGE.** The whole-strategy number (marginal, borderline-FRAGILE in the recent window) masks a real, credibly-sized split: solidly positive in TRENDING (+0.21R, n=13,867), roughly breakeven-to-negative in BREAKOUT (-0.03R, n=32,397, most of its volume).

**Recommendation: KEEP_WITH_FILTERS — restrict to TRENDING regime.** This is exactly the pattern the audit brief anticipated finding for a "supposedly weak" strategy. **Risk of overfitting:** this is one un-stacked regime split (not combined with symbol/session), checked as one of the small number of a-priori meaningful dimensions rather than mined — reasonably low overfitting risk — but it still needs its own forward walk-forward check restricted to TRENDING before being trusted as the final filter (the current whole-strategy walk-forward numbers do not isolate the TRENDING subset).

---

## 11. trend_pullback

**Live status:** ACTIVE_MT5, no override.

### Historical-window performance

| Window | n | Gross Exp R | Net Exp R | PF gross/net | PSR/DSR | ESS | MC P95 DD (R) | Verdict | Walk-fwd |
|---|---|---|---|---|---|---|---|---|---|
| 2023-2026 | 5,211 | +0.2792 | +0.2492 | 1.477 / 1.413 | 1.000 / 1.000 | 1,679 | 128.1 | ROBUST | PASS (ACCEPTABLE) |
| 2020-2023 | 8,000 | +0.2020 | +0.1710 | 1.331 / 1.271 | 1.000 / 1.000 | 2,866 | 151.1 | ROBUST | PASS |
| 2017-2020 | 7,462 | +0.0050 | -0.0380 | 1.007 / 0.945 | 0.615 / 0.615 | 2,684 | 541.6 | FRAGILE | PASS |

Full-corpus walk-forward: train n=14,375 (-0.026R) → OOS n=9,578 **(+0.313R)**, `edge_stability=ACCEPTABLE`, `directional_edge=UNSTABLE` (train and OOS disagree in sign — flagged honestly rather than treated as confirmatory, even though the OOS figure itself is strong).

### Where the edge comes from / consistency

Only one regime_broad bucket exists (TRENDING, by design — `regimes=("trending_up","trending_down")`). Symbol dispersion is real but not extreme: best USDCHF +0.403R (n=3,746), worst NZDUSD -0.183R (n=2,340) and GBPJPY -0.130R (n=1,684) — 2 of 10 symbols negative, 8 positive-to-flat. MAE/MFE: reached_0.25r 67.1%, immediate_failure_rate 32.9% — a good, mean_reversion-adjacent profile. By ATR: NORMAL best (+0.169R), LOW the one clearly weak bucket (-0.051R, n=5,828).

### Entry-vs-management

| Counterfactual | Coverage | Expectancy |
|---|---|---|
| Actual | 100% | +0.110R |
| Fixed 1R | 100% | +0.014R |
| Fixed 1.5R | 100% | +0.081R |
| Fixed 2R | 100% | +0.120R |

Actual tracks close to the fixed-2R figure — this strategy's current exit design is already capturing close to what a well-chosen fixed target would, i.e. exit design is a non-issue here.

### Drawdown / dependence on a small period

Recent-window MC P95 drawdown 128.1R against a fully-positive terminal-R distribution (p5 = +1,042R over the window's 5,211 trades) — genuinely low-risk relative to its size. Yearly avg_r is volatile early (2018 -0.14R, 2020 -0.23R, 2021 -0.18R) then **turns decisively positive from 2022 on**: +0.41R, +0.06R, +0.11R, **+1.31R (2025, n=805), +0.68R (2026, n=603)**. Genuinely improving, but genuinely dependent on a *recent* period — the 2018-2021 years were mixed-to-negative, and the strategy's strong walk-forward/robustness reads are being driven disproportionately by 2022 onward. This should be read as "a strategy that has become robust in its recent history," not "a strategy robust across its full lifetime."

### Classification

**ROBUST_CORE_EDGE, with an honest caveat about period-dependence.** The `directional_edge=UNSTABLE` flag (train negative, OOS strongly positive) and the 2018-2021 mixed years both argue for describing this as a strategy that has become genuinely strong recently, rather than one that has always been strong.

**Recommendation: KEEP.** Genuinely good exit efficiency, reasonable symbol consistency (8/10 positive), improving trend. **Risk of overfitting:** none of the segmentation splits found here are being proposed as new filters (the strategy's current unfiltered performance is already good) — the only caution is not to over-extrapolate the spectacular 2025 figure (+1.31R, n=805) as the new steady-state; 2026's more moderate +0.68R (n=603, still strong) is the more representative recent read.

---

## 12. liquidity_sweep_reversal

**Live status:** ACTIVE_MT5, no override.

### Historical-window performance

| Window | n | Gross Exp R | Net Exp R | PF gross/net | PSR/DSR | ESS | MC P95 DD (R) | Verdict | Walk-fwd |
|---|---|---|---|---|---|---|---|---|---|
| 2023-2026 | 1,472 | -0.2983 | -0.3284 | 0.617 / 0.590 | 3.0e-14 / 3.0e-14 | 668 | 582.3 | FRAGILE | FAIL |
| 2020-2023 | 4,567 | -0.0420 | -0.0780 | 0.940 / 0.894 | 0.030 / 0.030 | 1,240 | 630.0 | FRAGILE | FAIL |
| 2017-2020 | 2,934 | +0.0970 | +0.0600 | 1.155 / 1.091 | 1.000 / 1.000 | 936 | 171.1 | ROBUST | FAIL |

Full-corpus walk-forward: train n=5,380 (-0.014R) → OOS n=3,586 (-0.077R), `edge_stability=FAILED_OOS`, `directional_edge=UNSTABLE`. This is the smallest-sample of the 11 long-running strategies (full corpus n=8,973), which limits statistical confidence throughout this section.

### Symbol dependence — the dominant finding

Dramatic, credible dispersion: **NZDUSD +0.475R (n=1,291)**, EURJPY +0.685R (n=790), USDCAD +0.273R (n=1,043) vs **USDCHF -0.542R (n=1,138)**, EURUSD -0.412R (n=737), AUDUSD -0.341R (n=1,021), XAUUSD -0.272R (n=802), GBPUSD -0.241R (n=941). This is the widest symbol spread found anywhere in this audit (over 1.2R top-to-bottom), and every bucket has n in the 700-1,300 range — small in absolute terms but not "noise-small" at this effect size. 4 of 10 symbols are genuinely positive, 6 negative to strongly negative.

### MAE/MFE

reached_0.25r 65.0%, immediate_failure_rate 35.0% — middling. By regime_broad: REVERSAL -0.031R (n=5,094, its designed target), RANGING -0.046R (n=3,865) — both mildly negative, no regime rescues the aggregate. By direction: SHORT +0.042R vs LONG -0.135R — a real asymmetry.

### Entry-vs-management — a genuine bright spot

| Counterfactual | Coverage | Expectancy |
|---|---|---|
| **Actual** | 100% | **-0.039R** |
| Fixed 1R | 100% | -0.128R |
| Fixed 1.5R | 100% | -0.048R |
| Fixed 2R | 93.9% | -0.146R |

**Actual beats every fixed-R alternative checked** — the opposite pattern from vwap_reversion. Whatever exit logic liquidity_sweep_reversal currently uses is already extracting more value than any simple fixed target would. This strategy's problem is not management; management is comparatively its strongest attribute.

### Time-decay

2018 +0.22R, 2019 -0.28R, 2020 +0.20R, 2021 -0.09R, 2022 +0.23R, 2023 -0.11R, 2024 -0.26R, 2025 -0.65R (n=284), 2026 -0.64R (n=137) — noisy through 2022, then a real recent decline, though the 2025-26 samples are small (n=284, n=137) and should not be weighted as heavily as the earlier, larger years.

### Classification

**REGIME_DEPENDENT_EDGE leaning DECAYED_EDGE — but with the most genuinely-defensible symbol restriction found in this audit.** The whole-strategy number is negative and walk-forward-failed, but 4 of 10 symbols (NZDUSD, EURJPY, USDCAD, and to a lesser extent GBPJPY at -0.122R which is closer to neutral than the worst names) show real, credible, moderate-sample positive results, while the rest are clearly negative. This is not a single lucky corner — it's roughly 40% of the symbol space showing a coherent pattern.

**Recommendation: RESEARCH_REQUIRED — restrict to NZDUSD/EURJPY/USDCAD/GBPJPY and re-run walk-forward on that subset before any activation change.** Do not simply flip this to KEEP_WITH_FILTERS on today's evidence: n per symbol (700-1,300) is meaningfully smaller than every other strategy's per-symbol samples in this audit, and the whole-strategy walk-forward already FAILED — a symbol-restricted walk-forward, not just a symbol-restricted expectancy table, is needed before trusting this. **Risk of overfitting:** real — with only ~9,000 total trades split across 10 symbols, picking "the 4 best" symbols by their own historical performance is exactly the kind of after-the-fact selection this audit was told to resist; flagging it as RESEARCH_REQUIRED rather than a direct recommendation is the deliberately conservative call here.

---

## 13. wyckoff — treated separately per the brief

**Live status:** DISABLED (by design, zero live footprint, pending validation — per the registration commit's own docstring).

### Sample size and evidence base — corrects the audit's own briefing premise

The briefing described wyckoff as having "~3 years of evidence." The actual corpus does not support that: `historical_pattern_fingerprints` entry-times for wyckoff run **2026-02-02 to 2026-07-31 — six months**, all replayed in a single backfill run on 2026-08-17 (`created_at` 14:31-16:37 that day). Full-corpus n = 3,062. Effective sample size (AR(1)-adjusted, recent-window run) is **141.9** — a large haircut from the raw 3,062, reflecting substantial serial correlation (lag-1 autocorrelation 0.91 in the recent-window bootstrap, the highest of any of the 12 strategies) — i.e. the raw trade count considerably overstates how much independent evidence actually exists here.

### Symbol breadth — the central finding

Only **5 of 10 canonical symbols** have any wyckoff fingerprints at all (USDJPY, GBPUSD, EURUSD, XAUUSD, GBPJPY) — half the symbol space has zero evidence. Within those five:

| Symbol | n | avg_r |
|---|---|---|
| USDJPY | 1,036 | **+3.03** |
| GBPUSD | 905 | **-0.51** |
| EURUSD | 842 | **-0.50** |
| XAUUSD | 155 | +0.23 |
| GBPJPY | 124 | -0.35 |

**USDJPY alone (34% of the sample) carries an avg_r of +3.03 — over four times the whole-strategy average — while GBPUSD and EURUSD (57% of the sample combined) are both solidly negative.** The whole-strategy "ROBUST" verdict is a direct arithmetic consequence of one symbol's extreme outlier performance, not a broadly-shared edge.

### Regime breadth

`by_regime_broad`'s UNKNOWN bucket shows avg_r **+5.91** (n=424) — an even more extreme figure than USDJPY's, and a strong signal that a small number of very-large-R trades are driving the aggregate. REVERSAL (n=1,373, its largest designed-target regime) is actually **-0.33R**, and BREAKOUT (n=362) is **-0.77R**. Only RANGING (+0.51R, n=903) is both positive and not obviously skew-driven.

### The walk-forward "PASS" is very likely a skew artifact, not evidence of edge growth

Full-corpus walk-forward: train n=1,789 (+0.063R) → OOS n=1,221 (**+1.785R**), a **-2,728% "degradation"** (i.e. OOS wildly outperforms train) that earns `edge_stability=STRONG` and `directional_edge=POSITIVE_EDGE` under the classifier's mechanical rules. Given the symbol concentration above (one symbol driving the whole aggregate) and the extreme lag-1 autocorrelation (0.91), the far more plausible explanation is that a cluster of USDJPY (or UNKNOWN-regime) outlier trades landed disproportionately in the OOS half of this six-month corpus, not that the strategy's edge genuinely grew 28x out-of-sample. **This audit does not treat wyckoff's walk-forward PASS as reliable evidence.**

### MAE/MFE and entry-vs-management

reached_0.25r 74.7%, reached_1r 56.4% — a strong-looking reach profile, but n=3,062 total and heavily concentrated in one symbol means this cannot be cleanly separated from the same skew. Entry-vs-management counterfactuals (cf1 +0.128R, cf1.5 +0.182R, cf2 +0.194R) are all far below the actual +0.731R — meaning the actual result is being driven by trades running well past 2R (consistent with the USDJPY tail), not by a generally-efficient exit design across the whole sample.

### Classification

**INSUFFICIENT_EVIDENCE.** Per the brief's explicit instruction: do not classify wyckoff as proven merely because recent results are strong. Six months of history (not three years), half the symbol space entirely untested, and a whole-strategy positive average driven by one symbol's extreme tail — none of this supports a ROBUST classification, regardless of what the mechanical PSR/DSR/walk-forward outputs say in isolation.

**Recommendation: leave DISABLED; if promoted, promote to SHADOW_MT5 restricted to USDJPY only, and only after (a) the corpus is extended to the other 5 untested symbols, and (b) a walk-forward re-run specifically on non-USDJPY data to see whether any edge survives outside the one symbol currently carrying the entire result.** Do not use the current whole-strategy PSR=1.0/DSR=1.0/walk-forward-STRONG reading as justification for promotion — those numbers are real outputs of the formulas but do not mean what they would mean for a strategy without this degree of symbol concentration.

---

## 14. Cross-window comparison (all three regenerated windows, gross expectancy R, verdict)

All three windows were regenerated fresh this session (see §0) using identical methodology (8,000-row cap, 500-path bootstrap/Monte Carlo). Figures match the audit brief's own rounded baseline table to two-to-three decimal places throughout, confirming the regeneration is methodologically consistent with what was run earlier this session.

| Strategy | 2017-2020 | 2020-2023 | 2023-2026 (recent) | Pattern |
|---|---|---|---|---|
| mtfai1 | -0.002 FRAGILE | -0.221 FRAGILE | -0.059 FRAGILE | Never positive in any window |
| support_resistance_bounce | +0.210 ROBUST | +0.287 ROBUST | -0.146 FRAGILE | Robust 6 years, decayed recently |
| momentum | -0.097 FRAGILE | -0.009 FRAGILE | -0.761 FRAGILE | Never positive, recent collapse |
| session_breakout | -0.052 FRAGILE | -0.044 FRAGILE | -0.376 FRAGILE | Never positive in 9 years |
| vwap_reversion | +0.189 ROBUST | -0.224 FRAGILE | -0.067 FRAGILE | Robust once, decayed; entry edge persists per §6 counterfactuals |
| breakout | -0.180 FRAGILE | +0.022 FRAGILE | -0.747 FRAGILE | Never solidly positive, recent collapse |
| ema_trend | +0.090 ROBUST (wf FAIL even then) | -0.092 FRAGILE | -0.779 FRAGILE | Steady decline into near-total 2026 collapse |
| mean_reversion | +0.395 ROBUST | +0.233 ROBUST | +0.556 ROBUST | Robust all 3 windows, improving |
| smc_continuation | -0.237 FRAGILE | +0.097 ROBUST | +0.058 FRAGILE (borderline) | Improving then plateauing; TRENDING-only edge is real |
| trend_pullback | +0.005 FRAGILE | +0.202 ROBUST | +0.279 ROBUST | Improving, robust last 6 years |
| liquidity_sweep_reversal | +0.097 ROBUST | -0.043 FRAGILE | -0.298 FRAGILE | Robust once, decayed; symbol split is real |
| wyckoff | no data (strategy did not exist) | no data | +0.732 ROBUST (n=3,062, 6 months only) | Single-symbol-concentration artifact — see §13 |

---

## 15. Tiering

**Tier A — strongest evidence (KEEP as-is):**
- **mean_reversion** — robust across all 3 windows, walk-forward STRONG + POSITIVE_EDGE, consistent across 8/10 symbols and all sessions, efficient exit design, improving trend, well-explained mechanism (ranging/low-vol regime match).
- **trend_pullback** — robust in 2 of 3 windows (weak-but-not-negative in 2017-2020), walk-forward ACCEPTABLE, efficient exit design, strongly improving 2022 onward, though genuinely more dependent on its recent period than mean_reversion.

**Tier B — useful but conditional (KEEP_WITH_FILTERS / RESEARCH_REQUIRED):**
- **smc_continuation** — real, credible TRENDING-regime edge (+0.21R) masked by a larger, weak BREAKOUT-regime bucket (-0.03R) dragging the aggregate to borderline/FRAGILE.
- **vwap_reversion** — the audit's clearest management-fixable case: entry edge is demonstrably real (best-in-class MFE, second-best reach-rate, every fixed-R counterfactual solidly positive) but current exit design converts it to a loss (-0.06R actual vs +0.13-0.15R fixed-R alternatives).
- **liquidity_sweep_reversal** — a genuine, non-trivial symbol split (4 of 10 symbols positive with 700-1,300 n each) inside an overall-decayed, walk-forward-failed strategy; needs its own symbol-restricted walk-forward before any activation change.
- **mtfai1** — direction (LONG vs SHORT) and regime (REVERSAL vs BREAKOUT) splits are real but neither alone flips the sign; already independently flagged for demotion by the automated performance monitor.

**Tier C — research/redesign required:**
- **liquidity_sweep_reversal** *(also listed in Tier B — genuinely straddles both: promising evidence, but not yet validated enough to act on)*.

**Tier D — retirement candidates (RETIRE or already-effectively-retired):**
- **momentum** — no credible edge in any of the 6-10 splits checked; already hard-deprecated in code as of today (2026-08-21).
- **session_breakout** — never positive in 9 years in aggregate; only one un-stacked positive symbol corner (GBPJPY) found, not yet validated as a real, standalone edge.
- **breakout** — severe, broad 2024-26 collapse; no exit-design or symbol/regime split rescues it.
- **ema_trend** — marginal at best historically, walk-forward-unstable even in its best window, now in a near-total 2026 collapse (98.7% loss rate).

**Separately classified — insufficient evidence, not yet ready to tier:**
- **wyckoff** — six months of history (not the three years this audit was briefed to expect), half the symbol space untested, and a whole-strategy "ROBUST" reading that is an arithmetic consequence of one symbol's extreme outlier performance. Left DISABLED; do not promote past SHADOW_MT5/USDJPY-only without corpus expansion and a non-USDJPY-specific validation pass.

---

*Generated 2026-08-21. All queries read-only against `openterminalui-backend-1`'s Postgres instance; no production table was written, no activation flag or strategy/risk/execution code was modified. Scratch analysis scripts used: `scratch_strategy_robustness_report*.py` (pre-existing, re-run), `scratch_diagnostic_segmentation.py`, `scratch_diagnostic_atm_vs_static.py`, `scratch_diagnostic_recommendations_and_confidence.py` (new, this session) — all in the repo root, left in place per this session's established convention.*
