# session_liquidity_breakout — Design & Implementation Spec

**Status: design + implementation. Registered DISABLED, same zero-live-footprint pattern as `donchian_trend_follow`/`wyckoff`.**

**Hypothesis:** important session liquidity level → sweep/break → acceptance/displacement → retest of broken level → rejection/continuation → entry.

## 1. Why this is not `session_breakout` (read directly, not assumed)

`session_breakout`'s actual logic (`backend/mt5_strategies/families/_legacy.py::evaluate_session_breakout`), confirmed by direct re-read: price closes beyond a session/previous-day high/low (`ctx.m15_snapshot.session_levels`) — that's the entire trigger. No displacement check, no minimum break distance, no retest, no rejection, no BOS/CHoCH check, flat strength (64.0), flat 2.5×ATR target. EQH/EQL touch count and squeeze are recorded for observability only, never scored. This is genuinely a bare break-beyond-level check.

`session_liquidity_breakout` adds real, mandatory selectivity on top of the *same* level source, without a giant AND-chain:

| | `session_breakout` (unchanged, the control) | `session_liquidity_breakout` (new) |
|---|---|---|
| Level source | `detect_session_levels()` via `ctx.m15_snapshot.session_levels` | **same**, reused directly |
| Break confirmation | Close beyond level, nothing else | Close beyond level **+ mandatory displacement confirming it + minimum ATR-normalized break distance** |
| Entry timing | Always immediate | Three separately-tagged entry variants (below) computed from the same qualifying break event |
| Retest/rejection | None | Optional, explicitly compared |
| SMC evidence | Observability only (EQH/EQL) | Supporting, bounded (BOS, FVG/OB, premium/discount, EQH/EQL) — never mandatory |

## 2. Existing components reused (no new detectors)

`ctx.m15_snapshot.session_levels` (level source), `.displacements` (break confirmation), `.breaks` (BOS/MSS supporting evidence + `_recent_structure_break_against` for opposing evidence), `.liquidity_sweeps`/`.equal_levels` (`_eqh_eql_touch_count`), `.imbalances`/`.order_blocks` (`_zone_overlap`), `.dealing_ranges` (`_premium_discount_position`), `_wick_rejection_score`, `_find_level_retest_hold` (the same generalized helper built for `donchian_trend_follow`/`breakout.py`), `_dynamic_stop`, `_structural_take_profit`, `_opposing_structural_level`, `ctx.atr_m15`, `ctx.htf_trend_h1`. The only genuinely new code is the composition/sequencing logic and a small "no reclaim since the break" acceptance check (mirrors `_find_level_retest_hold`'s own "wicked/closed back through the level" test, applied to the immediate post-break window instead of a retest window).

## 3. Feature table

| Feature | Purpose | Core/Supporting/Opposing | Existing component | New code | Double-counting risk |
|---|---|---|---|---|---|
| Session/prev-day level break (close beyond) | Same trigger `session_breakout` uses | **CORE** | `ctx.m15_snapshot.session_levels`, identical level-selection logic to `session_breakout` | None (reused inline, same filter) | Low |
| Minimum ATR-normalized break distance | Avoid marginal-tick breaks | **CORE** | `ctx.atr_m15` | Trivial arithmetic | Low |
| Displacement confirms the break | "Acceptance" — real conviction, not just a close beyond | **CORE** | `m15_snapshot.displacements` | None | Low |
| No reclaim since the break (price hasn't closed back through the level) | Filters the "swept and failed" case the raw `LiquiditySweep` detector would itself tag as a *reversal*, not a continuation | **CORE** | Same bar-scan pattern as `_find_level_retest_hold`'s reclaim check | Small (new function, same shape) | Low |
| Entry mode: IMMEDIATE / RETEST / RETEST_REJECTION | The actual thing being compared | **CORE (mode dispatch)** | `_find_level_retest_hold`, `_wick_rejection_score` | None (composition) | N/A — see §4 |
| BOS/MSS with-trend nearby | Structural confirmation | **SUPPORTING** | `m15_snapshot.breaks` | None | Low |
| FVG/OB overlap at entry | Location quality | **SUPPORTING** | `_zone_overlap` | None | Low |
| Premium/discount favorable | Location quality | **SUPPORTING** | `_premium_discount_position` | None | Low |
| EQH/EQL touches at the level | Confirms real liquidity concentration | **SUPPORTING** | `_eqh_eql_touch_count` | None | Low |
| Opposing structure break nearby | Contradicts the setup | **OPPOSING** | `_recent_structure_break_against` | None | Low |

Deliberately excluded from CORE: HTF alignment (kept supporting, since `session_breakout` itself has none, and the design brief explicitly warns against stacking every SMC feature as a mandatory AND-chain).

## 4. Three entry variants, computed from ONE qualifying break event

Rather than three separate strategy functions (3x the compute), one evaluator determines the CORE break event once, then classifies up to three tagged candidates from it — mirroring the same "compute once, tag multiple outcomes" pattern already used for mean_reversion Path A/B in the evidence-validation harness:

- **`liquidity_immediate`**: CORE holds, entered right at/near the break bar (bars_since ≤ 5, same window `breakout.py`/`donchian` use).
- **`liquidity_retest`**: CORE holds AND `_find_level_retest_hold` confirms a completed retest-and-hold. Entered at the retest confirmation price.
- **`liquidity_retest_rejection`**: same as `liquidity_retest`, AND the retest confirmation bar's `_wick_rejection_score` clears a minimum threshold.

All three (when they qualify) are recorded with `setup_subtype` in their evidence, so the validation pass can compare all four populations (bare `session_breakout` + these 3) from data collected in one replay.

## 5. Validation plan

Same 6-month window, same 5 symbols (EURUSD/GBPUSD/USDJPY/AUDUSD/XAUUSD), `MT5` provider, point-in-time-safe, no persistence to production tables (all four populations are either counterfactual or already-real but re-evaluated standalone). Breakdown: N, expectancy R, PF, win rate, immediate-failure rate, +0.5R/+1R/+2R/+3R reach, MFE/MAE, by LONG/SHORT, by symbol, by session (Asian/London/NY, from `reference.session_name`), by regime, by setup_subtype, max DD. No walk-forward folds promised in advance beyond what the 6-month sample and available compute support — reported honestly either way, not forced.

## 6. Promotion

Registered `DISABLED` (zero live footprint) until validated. Only promoted if a specific variant shows robust, cost-aware, sample-sufficient improvement over the `session_breakout` control — scoped to exactly the variant/symbols/direction that earns it, not the whole strategy by default. `session_breakout` itself is not touched in any way — it stays the fixed control baseline.
