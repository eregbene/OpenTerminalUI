# Evidence-Based Architecture Design: mean_reversion & trend_pullback

**Status: design/audit only. No code changed, nothing deployed, no new backtest run.**

Scope: investigate whether `mean_reversion` and `trend_pullback` should evolve from their current single-trigger binary gates into a multi-evidence scoring architecture — reusing Bensim's existing SMC/market-structure engine as a major evidence layer — while staying clearly distinct from `mtfai1` and `smc_continuation`. This document proposes an architecture; it does not implement one.

---

## 1. What Bensim already computes (inventory, with exact source)

Everything below already exists and is already attached to every `StrategyContext` — this is a reuse inventory, not a proposal to build anything new.

**From `backend.market_structure.engine.analyze_bars` → `ctx.m15_snapshot` (a `MarketStructureSnapshot`), also computed for H1/H4:**

| Evidence | Field | Notes |
|---|---|---|
| HH/HL/LH/LL swing structure | `m15_snapshot.swings` (`SwingPoint.swing_type`: `"high"`/`"low"`, `.bar_index`, `.price`) | Raw swing points; sequential comparison of consecutive same-type swings is how HH-vs-LH etc. would be derived (not itself pre-labeled HH/HL/LH/LL — that classification exists at the `TrendState`/H1-H4 trend level, see below) |
| Trend classification with reasoning | `m15_snapshot.trend_states` (`TrendState.state: TrendLabel`, `.evidence: list[str]`) | This is what feeds `ctx.htf_trend_h1`/`htf_trend_h4` — a real structural trend classifier, separate from EMA20/50 |
| BOS / CHoCH / MSS | `m15_snapshot.breaks` (`StructureBreak.break_kind`: BOS/CHOCH/MSS, `.direction`, `.bar_index`, `.break_distance_atr`, `.continuation_direction`) | Three distinct enum values, but MSS is not an independently-detected concept: `classify_trend`'s break logic (`structure.py`) labels a break BOS when it's with-trend; when it's against-trend, it's CHoCH if unaccompanied, or **MSS specifically when that same bar also carries a qualifying displacement** — i.e. MSS = "CHoCH confirmed by displacement," not a third detector. `break_distance_atr` is a ready-made break-strength/quality metric, currently unused by any strategy |
| Displacement | `m15_snapshot.displacements` (`DisplacementEvent.bar_index`, `.direction`, `.magnitude_atr`, `.body_ratio`, `.created_imbalance`) | Already used by `smc_continuation` (mandatory) |
| Liquidity sweeps | `m15_snapshot.liquidity_sweeps` (`LiquiditySweep.side`, `.swept_price`, `.penetration`, `.bar_index`) | Already used by `liquidity_sweep_reversal` (mandatory) and `smc_continuation` (optional IDM precondition) |
| EQH/EQL | `m15_snapshot.equal_levels` + `m15_snapshot.equal_level_sweeps` | Confirmation-only helper already exists: `_eqh_eql_touch_count()` (`_shared.py:182`) — OOS-validated positive for `support_resistance_bounce`/`smc_continuation` specifically, sign-unstable for `breakout`/`liquidity_sweep_reversal` |
| FVG / imbalances | `m15_snapshot.imbalances` (`ImbalanceZone.direction`, `.status` incl. `"mitigated"`, `.midpoint`, `.consequent_encroachment`, price_low/high) | Used by `trend_pullback` (behind `OTE_OB_FVG` flag, off by default) and `smc_continuation` (mandatory retracement zone) |
| Order blocks | `m15_snapshot.order_blocks` (`OrderBlock.direction`, `.status` incl. `"mitigated"`/`"invalidated"`, price_low/high, `.source_break_id`) | Same callers as FVG above |
| Premium/discount | `m15_snapshot.dealing_ranges` (`DealingRange.normalized_current_position: float, 0–1`) + `m15_snapshot.premium_discount_zones` (`PremiumDiscountZone.zone_name`) | **Computed but currently used by ZERO strategies.** `normalized_current_position` is a ready-made continuous 0–1 evidence input (0=discount, 1=premium) |
| Support/resistance | `m15_snapshot.liquidity_levels` (`LiquidityLevel.level`, `.side`, `.touch_count`, `.tolerance`) | SMC-native (swing-derived), not a separate rolling-window S/R calc. Mandatory trigger for `support_resistance_bounce`; available generically |
| OTE (Fibonacci 61.8–78.6%) | `_ote_zone_for_direction()` (`_shared.py:260`) | Reusable helper, derived from `m15_snapshot.swings`, no new detector needed |
| Structural rejection candle | `_reaction_candle_confirms()` (`_shared.py:317`) | Reusable, currently used by `support_resistance_bounce` (mandatory) and available behind `trend_pullback`'s (off) reaction-candle flag |
| No opposing CHoCH/MSS | `_recent_structure_break_against()` (`_shared.py:299`) | Reusable, already built for `trend_pullback`'s anti-CHoCH gate (flag off by default) |
| Sweep-precedes-move | `_liquidity_sweep_precedes()` (`_shared.py:333`) | Reusable, built for `smc_continuation`'s IDM precondition |

**From `StrategyContext` directly:**

| Evidence | Field | Notes |
|---|---|---|
| EMA20/50 | computed inline per-strategy (`backend.core.technicals.ema`) | Not yet a shared context field — each caller computes its own |
| RSI | computed inline per-strategy (`backend.core.technicals.rsi`) | Same — not a shared context field |
| ATR (M15) | `ctx.atr_m15` | Shared, used everywhere for stop/target sizing |
| HTF trend | `ctx.htf_trend_h1`, `ctx.htf_trend_h4` | bullish/bearish/transitional, from the market-structure trend classifier |
| Regime (categorical, hard-gated) | `ctx.regime` | From `adaptive_management.service.detect_regime` — this is what `STRATEGY_FAMILIES[...]["regimes"]` + `regime_compatible()` hard-gate on today |
| Regime (ADX-based, continuous, currently inert) | `ctx.market_regime`, `ctx.adx_m15`, `ctx.atr_expansion_ratio` | A **second, independent** Phase-2 regime classifier. Already wired to a bounded strength-bonus pattern for `mean_reversion` specifically (`_regime_strength_bonus`, `_shared.py:383`), but gated behind `MT5_REGIME_FILTER_ENABLED` which **defaults to False** — this mechanism exists and is unused in production today |
| VWAP | not a shared field | Computed inline only inside `vwap_reversion.py`; would need light extraction to reuse cleanly |
| Squeeze state | `ctx.squeeze_state`, `ctx.squeeze_momentum_value` via `_squeeze_evidence()` | **Explicitly evidence-only by design contract** — a 2026-08-17 chronological OOS validation found squeeze's apparent predictive value sign-flips out-of-sample. `_shared.py`'s own docstring: "must NEVER be used to adjust strength, gate a signal, or otherwise influence a trading decision." Any new design must keep it observability-only, full stop. |
| Historical Intelligence | `backend/historical_intelligence/entry_intelligence.py::evaluate_historical_intelligence`, called from `autonomous.py::_apply_historical_intelligence` | Confirmed: this runs **after** a candidate already exists, as a post-hoc ±10 ranking adjustment during candidate ranking — not a pre-candidate evidence input. Nothing in this design treats it otherwise; HI stays exactly where it is in the pipeline. |

---

## 2. What the three adjacent strategies already own (so the new designs don't duplicate them)

| Strategy | Mandatory core | Regime gate | What it must NOT be re-created as |
|---|---|---|---|
| `liquidity_sweep_reversal` | Sequential chain: sweep → displacement (same direction) → CHoCH/MSS (same direction) within 5 bars | `unstable_transition`, `reversal`, `ranging` | The mean_reversion redesign must not become "sweep→displacement→CHoCH is the trigger" — that chain already exists and is a different strategy |
| `support_resistance_bounce` | Price within ATR-scaled tolerance of a `LiquidityLevel` + a rejecting candle, no RSI involved at all | none (Phase-3 HTF check is optional/off) | mean_reversion must not become "price-near-level + rejection candle" as its *mandatory* trigger — S/R can only be *supporting* evidence for it |
| `smc_continuation` | H4 trend (bullish/bearish) → M15 BOS with-trend → displacement within 1 bar → (optional IDM sweep) | none (deliberately unrestricted) | trend_pullback must not become "H4 trend + BOS + displacement + FVG/OB zone" — that IS smc_continuation. trend_pullback's identity has to stay M15/H1-horizon, EMA-defined local trend, not H4 macro-structure |
| `mtfai1` | Fast/slow SMA crossover on M15, confirmed by H1/H4 close-vs-average; no RSI. SMC is used only as two confirmation *gates* (swing-trend agreement, EQH/EQL proximity block) and for stop/target levels — never BOS/CHoCH/displacement/FVG/OB/dealing-range | none (`regimes: ()`, deliberately unrestricted, "must not change") | Neither redesign should start looking like this — mtfai1's primary trigger is pure MA-crossover; its SMC usage is confirmation-only, structurally the simplest strategy in the engine |

This four-way separation is the design constraint every table below is built to satisfy.

---

## 3. Proposed architecture — `mean_reversion`

**Current implementation:** RSI(14) ≤ 30 or ≥ 70 is the sole, binary trigger. Regime-gated to `ranging`/`low_volatility` at the orchestrator level (hard block, not scored).

**Proposed identity, unchanged:** a *statistical price-extension* strategy — it fires because price has stretched further than usual, not because a structural sequence completed. This is the property that keeps it distinct from `liquidity_sweep_reversal` (structure-sequence-triggered) and `support_resistance_bounce` (location-triggered). SMC evidence's role here is **reversal-location and reversal-confirmation quality**, never the trigger itself.

| Layer | Condition | Existing Bensim component |
|---|---|---|
| **Mandatory core** | Price stretch present: RSI(14) ≤ 30/≥ 70 **OR** a materially large deviation from EMA20/VWAP normalized by ATR (a softer, second way to detect "stretched," see §3.1) | `backend.core.technicals.rsi`; EMA/VWAP deviation is new arithmetic on existing inputs, no new detector |
| **Mandatory core** | Regime not actively hostile: `ctx.market_regime != TRENDING_STRONG` (soft form of the existing disable rule, see §3.2) | `_regime_strategy_disabled` pattern, `_shared.py:374` (already exists for exactly this case) |
| **Supporting — SMC (reversal-location)** | Price sits in a discount zone for a LONG reversal / premium zone for a SHORT (`dealing_ranges.normalized_current_position` near 0 or 1) | `m15_snapshot.dealing_ranges` — **unused today, first strategy to consume it** |
| **Supporting — SMC (reversal-location)** | A liquidity sweep just occurred on the side that would trap the move being faded (sell-side sweep before a LONG reversal, buy-side before a SHORT) | `m15_snapshot.liquidity_sweeps`, same field `liquidity_sweep_reversal`/`smc_continuation` already read — new *composition*, not new detection |
| **Supporting — SMC (reversal-location)** | EQH/EQL touch count ≥ 2 on the side being faded | `_eqh_eql_touch_count()` — reused exactly as-is, same confirmation-only contract already validated for two other strategies |
| **Supporting — SMC (reversal-location)** | Price overlaps an opposing-direction order block or FVG (unmitigated) | `m15_snapshot.order_blocks` / `m15_snapshot.imbalances`, same fields `smc_continuation`/`trend_pullback` already read |
| **Supporting — SMC (reversal-confirmation)** | A CHoCH/MSS *in the reversal direction* has just formed (optional, lighter-weight than requiring the full sequential chain) | `m15_snapshot.breaks`, filtered the same way `_recent_structure_break_against()` already filters — but scored, not gated |
| **Supporting — SMC (reversal-confirmation)** | A displacement candle in the reversal direction just printed | `m15_snapshot.displacements`, same field `smc_continuation` reads |
| **Supporting — other indicator** | Structural rejection candle at the extreme | `_reaction_candle_confirms()` |
| **Supporting — other indicator** | Nearby `LiquidityLevel` (S/R) coincides with the stretch, i.e. RSI extreme AND at a known level, is stronger than either alone | `m15_snapshot.liquidity_levels` — used as *context*, never as the trigger (that stays `support_resistance_bounce`'s exclusive territory) |
| **Supporting — other indicator, observability only** | `squeeze_state`/`squeeze_momentum_value` | `_squeeze_evidence()` — attached to the evidence dict for record-keeping, **never scored**, per its existing hard constraint |
| **Opposing evidence** | `ctx.market_regime == TRENDING_STRONG` with strong ADX | `ctx.adx_m15`/`ctx.market_regime`, existing fields |
| **Opposing evidence** | HTF (H1) trend strongly agrees with the direction being faded (fading into a strong HTF trend is the textbook way mean-reversion loses) | `ctx.htf_trend_h1` |
| **Opposing evidence** | No nearby structural level, no sweep, no OB/FVG at all — a "naked" RSI extreme with zero locational support | Absence of the supporting-SMC items above |

### 3.1 — Should RSI 30/70 stay mandatory, or become one component of a score?

**Recommendation: keep it mandatory, but widen what counts as "stretch."** Two reasons:

1. RSI 30/70 is not an arbitrary threshold that's obviously too tight — the historical corpus shows mean_reversion firing 52,633 times over 8 years (a healthy, regularly-reachable rate), and its 8-year audit performance (+0.273R pooled, positive every year 2018–2026) is entirely a track record of this exact trigger. Making RSI purely optional/scored risks producing a strategy whose live behavior is no longer the thing that was validated.
2. However, RSI is not the only legitimate way to detect "stretched." A price that has deviated sharply from its EMA20 or from session VWAP by more than ~1.5–2x its own ATR is stretched in the same statistical sense RSI is trying to capture, just measured differently — and can legitimately fire *before* RSI crosses 30/70 (RSI can lag a fast, sharp move). Proposal: mandatory core = "RSI extreme **OR** EMA/VWAP deviation ≥ a bounded ATR multiple," not a full replacement of RSI, an **additional legitimate way to satisfy the same underlying condition** (statistical stretch), each independently reachable.

This directly answers the "does this let Bensim catch reversion opportunities before RSI hits 30/70" question: yes, via the EMA/VWAP-deviation branch of the mandatory core — but that branch is still a *hard, bounded, comparably strict* mandatory condition, not "RSI becomes a soft 15-point bonus and everything else picks up the slack." A pure statistical stretch with strong SMC confirmation (sweep + discount-zone + reaction candle) should be able to qualify without RSI ever reaching 30/70 — but only through this second explicit trigger path, not by loosening RSI itself.

### 3.2 — Regime: keep the hard gate, or soften it?

Unlike `trend_pullback` (see §4.4), **do not soften mean_reversion's regime restriction on the `ctx.regime` axis.** From the earlier live audit: mean_reversion's compatible regime (`ranging`/`low_volatility`) already occurs in ~28% of cycles — a healthy fraction, not the near-total suppression `trend_pullback` suffers (~4%). The `ctx.regime`-based gate is not the bottleneck here; RSI reaching an extreme is. Softening the regime gate would not meaningfully increase volume and would add exposure to a class of loss (mean-reverting into a real trend) the current design deliberately avoids.

What *should* change: the currently-inert `ctx.market_regime` (ADX-based) axis — `TRENDING_STRONG` disable and `CHOP_RANGING` boost are already coded (`_regime_strategy_disabled`/`_regime_strength_bonus`) but sit behind `MT5_REGIME_FILTER_ENABLED=False`. This is a second, complementary, already-built regime signal that isn't currently contributing anything — worth actually turning on and folding into the evidence score (as an opposing-evidence check, not a new gate) rather than leaving it dormant.

---

## 4. Proposed architecture — `trend_pullback`

**Current implementation:** mandatory chain = EMA20 vs EMA50 relationship decides direction → price inside a 1×ATR band around the EMA20/50 zone (or, behind an off-by-default flag, an OB/FVG/OTE confluence zone) → H1 HTF non-conflict. Hard-gated by the orchestrator to `ctx.regime ∈ {trending_up, trending_down}` **before** any of the above ever runs.

**Live-audit finding this design directly responds to:** on a same-instant snapshot across all 10 symbols, the strategy's own EMA+HTF logic found complete, valid setups on 8/10 symbols — every one discarded by the outer regime gate before it was ever scored. That gate is empirically the dominant suppressor, and it duplicates a trend judgment the strategy already makes for itself via EMA20/50 + HTF.

| Layer | Condition | Existing Bensim component |
|---|---|---|
| **Mandatory core (unchanged)** | EMA20 vs EMA50 relationship determines direction | inline `backend.core.technicals.ema`, unchanged |
| **Mandatory core (unchanged)** | Price inside the EMA20/50 pullback zone (±1×ATR band) | `_in_ema_zone()`, unchanged |
| **Mandatory core (unchanged)** | H1 HTF trend does not conflict | `ctx.htf_trend_h1`, unchanged |
| **Supporting — SMC (trend integrity)** | M15 swing sequence agrees with the EMA-implied direction (HH/HL forming for LONG, LH/LL for SHORT) | `m15_snapshot.swings`, same sequencing logic `_ote_zone_for_direction()` already performs |
| **Supporting — SMC (trend integrity)** | A recent M15 BOS *in the local-trend direction* exists (not H4 — that would collapse into smc_continuation's own definition) | `m15_snapshot.breaks`, filtered to M15 + local direction only |
| **Supporting — SMC (pullback completion)** | The retracement overlaps an unmitigated FVG or order block in the trend direction | `m15_snapshot.imbalances` / `m15_snapshot.order_blocks` — already reachable today behind the `OTE_OB_FVG` flag; proposal makes this a *scored bonus in EMA_ZONE mode too*, not an alternate exclusive mode |
| **Supporting — SMC (pullback completion)** | The retracement lands inside the OTE 61.8–78.6% zone | `_ote_zone_for_direction()`, already built, already used behind the same flag |
| **Supporting — SMC (pullback completion)** | A liquidity sweep occurred during the retracement, on the side that would trap counter-trend participants | `_liquidity_sweep_precedes()` — reusable, same trapping-side logic `smc_continuation`'s IDM precondition already uses, applied to the pullback window instead of a pre-BOS window |
| **Supporting — SMC (pullback completion)** | Retracement lands in discount (for LONG) / premium (for SHORT) — a pullback that's *already at premium* in an uptrend is a much weaker continuation setup | `m15_snapshot.dealing_ranges.normalized_current_position` — unused today, same field proposed for mean_reversion |
| **Supporting — SMC (continuation confirmation)** | A displacement candle back in the trend direction has printed (resumption starting) | `m15_snapshot.displacements` |
| **Supporting — SMC (continuation confirmation)** | A fresh BOS/MSS in the trend direction, after the pullback low/high | `m15_snapshot.breaks` |
| **Supporting — regime (soft, not gate)** | `ctx.market_regime`/`ctx.adx_m15` favor a real trend (higher ADX, directionally consistent) | `ctx.adx_m15`, `ctx.atr_expansion_ratio`, `ctx.market_regime` — the currently-inert Phase-2 axis, extended to `trend_pullback` (today it only touches `mean_reversion`/`breakout`) |
| **Opposing evidence** | A CHoCH/MSS **against** the trend direction within a recent lookback | `_recent_structure_break_against()` — already built, currently gated behind an off-by-default flag as a hard reject; proposal reuses it as a **scored penalty** rather than (or in addition to) a hard reject |
| **Opposing evidence** | No reaction candle at all confirming the pullback has actually turned | `_reaction_candle_confirms()` — same helper, already available behind an off flag |
| **Opposing evidence** | `ctx.regime` explicitly hostile (e.g. `breakout` regime firing against the local trend direction, or the ADX axis reading `CHOP_RANGING`) | `ctx.regime`, `ctx.market_regime` |

### 4.1 — Replacing the outer `TRENDING` hard gate

**Proposal: remove `trend_pullback` from `regime_compatible()`'s hard-gate table** (i.e., `STRATEGY_FAMILIES["trend_pullback"]["regimes"]` → `()`, matching `mtfai1`'s and `smc_continuation`'s pattern of not being regime-restricted at the orchestrator level), and **replace it with `ctx.regime` and `ctx.market_regime` contributing to the opposing/supporting evidence score instead** — never blocking evaluation outright. The strategy's own mandatory core (EMA20/50 relationship + H1 alignment) already *is* a trend requirement; the orchestrator's separate classifier duplicating that judgment, with a stricter bar, is what's actually suppressing volume.

**What this is not:** this is not "loosen trend_pullback until it fires as often as mtfai1." mtfai1 has *no* trend requirement in its `STRATEGY_FAMILIES` entry at all (empty tuple by original design, unrelated to trend judgment quality). trend_pullback's proposed design still requires EMA20/50 alignment + H1 non-conflict as a hard mandatory core — it just stops requiring a *second, independent, stricter* trend judgment from a different classifier on top of that.

**Honest limitation, stated plainly:** I cannot tell you whether this change would improve or hurt trend_pullback's net performance without a new backtest, because the entire existing 23,966-trade historical corpus for this strategy was built under this exact same `TRENDING`-only restriction (`regime_broad = 'TRENDING'` for 100% of historical rows, confirmed by direct query) — there is no historical counterfactual data for "trend_pullback outside TRENDING regime" to check against. This document proposes the architecture; validating it (ideally via SHADOW forward evidence, not a new backtest) is a separate, later, explicitly-authorized step.

---

## 5. What NOT to add (redundancy / double-counting check)

Per your instruction not to add every available indicator, here's what was considered and excluded, and why:

- **VWAP for trend_pullback**: excluded. VWAP deviation is conceptually a mean-reversion signal (distance from a fair-value anchor), not a trend-continuation one — adding it here would blur trend_pullback toward mean_reversion's own territory. VWAP deviation is proposed for mean_reversion only (§3.1), where it's conceptually native.
- **EQH/EQL for trend_pullback**: excluded as a *separate* bonus. It already lives inside the OTE/OB/FVG confluence check (an EQH/EQL pool and an FVG/OB in the same zone would double-count the same underlying "this level matters" signal). Not proposed as an independent line item.
- **Requiring the full liquidity_sweep_reversal chain (sweep→displacement→CHoCH) inside mean_reversion**: excluded outright — this is literally a different strategy's exact identity; including it as a scored *bonus* (not mandatory) avoids the duplication while still letting the evidence layer notice when a "textbook reversal" happens to coincide with a stretch.
- **Requiring ALL SMC conditions simultaneously for either strategy**: explicitly rejected per your instruction — every SMC item above is additive/supporting, never another mandatory AND-clause. Stacking mandatory SMC conditions on top of the existing mandatory core would only make both strategies rarer, the opposite of the goal for trend_pullback and unnecessary for mean_reversion (which isn't volume-starved on the regime axis, only on the RSI-extreme axis).
- **`lorentzian_features`/`trendline_pivots`**: excluded. Both exist in `StrategyContext` but are explicitly flagged "not read by any existing strategy" pending their own OOS validation gate (same bar squeeze_momentum was held to and failed) — not proposed here; introducing them without that validation would repeat the exact mistake the codebase's own conventions are designed to prevent.
- **Full mandatory SMC displacement+BOS+FVG chain for trend_pullback (i.e., converging with smc_continuation)**: excluded — this is the central distinctness risk called out in §2, avoided by keeping SMC strictly supporting/opposing, never promoted to the mandatory core.

---

## 6. Distinctness check — four strategies, one evidence layer, different identities

| | Mandatory trigger | Timeframe horizon | Trend definition | Regime relationship | SMC role |
|---|---|---|---|---|---|
| `mtfai1` | Fast/slow SMA crossover, H1/H4 confirm | M15/H1/H4 | Moving-average alignment | none (unrestricted) | confirmation-gate only: re-derives M15 swings/trend via the same `classify_trend` and rejects on disagreement; blocks if an unswept EQH/EQL sits ahead of price within 0.5 ATR. Never BOS/CHoCH/displacement/FVG/OB/dealing-range — those are zero for mtfai1 |
| `smc_continuation` | H4 trend → M15 BOS with-trend → displacement | H4→M15 | H4 structural trend classifier | none (unrestricted) | **mandatory core itself** |
| `trend_pullback` (proposed) | EMA20/50 local relationship + H1 non-conflict + pullback zone | M15/H1 | EMA20-vs-EMA50 (local, short-horizon) | **supporting/opposing evidence, not a gate** | **supporting** trend-integrity + pullback-completion confirmation |
| `mean_reversion` (proposed) | RSI extreme OR EMA/VWAP-deviation stretch | M15 | none (counter-trend by design) | hard gate on `ctx.regime` (unchanged), soft evidence on `ctx.market_regime` | **supporting** reversal-location + reversal-confirmation |

Four genuinely different trigger philosophies survive: pure MA crossover (mtfai1), pure structural continuation (smc_continuation), local-EMA continuation confirmed by structure (trend_pullback), and statistical extension confirmed by structure (mean_reversion). None of the four collapse into another under this design.

---

## 7. Why this over the current binary implementation (and why not, honestly)

**In favor:**
- Both strategies currently discard information they already have paid the compute cost to produce. `ctx.m15_snapshot` is built on every eligible cycle regardless of whether mean_reversion/trend_pullback end up using it — the SMC evidence proposed here is not new computation, just new *consumption* of already-computed data.
- The live audit's own evidence (8/10 symbols with valid trend_pullback setups killed by a redundant regime classifier; a real 73.89-vs-75.0 near-miss on mean_reversion's one candidate) shows the binary designs are throwing away marginal, plausible setups at a hard cutoff rather than letting evidence quality make that call gradually.
- The additive-bonus, mandatory-core-plus-supporting-evidence pattern already exists everywhere else in this codebase (`smc_continuation`, `support_resistance_bounce`) — this isn't a new paradigm, it's applying an existing, already-audited pattern to the two strategies that don't yet have it.

**Against / risk, stated honestly:**
- More knobs = more ways to be wrong, and every new bonus line item needs its own chronological OOS validation before it's allowed to move a score (the exact bar `_eqh_eql_touch_count` and `_squeeze_evidence` were each held to). This document proposes *what* to evaluate, not final point weights — assigning real numbers without validation would repeat the mistake this codebase's own conventions exist to prevent.
- mean_reversion's 8-year track record is entirely attributable to its current simple trigger. Any change carries real risk of degrading a strategy that's currently the single best performer in the whole audit, purely by adding surface area for bugs or overfit bonuses.
- trend_pullback's regime-gate removal is the one proposed change I can't defend with existing data (§4.1) — it is a genuine, disclosed unknown, not a confident recommendation.

---

## 8. Next steps (not authorized by this document)

This is a design/audit deliverable only. Before any of this is implemented:
1. Exact point values for each supporting/opposing item need to be proposed and chronologically OOS-validated the same way `_eqh_eql_touch_count`'s bonus was — not guessed.
2. Given no historical counterfactual exists for trend_pullback outside `TRENDING` regime, the recommended validation path is **forward SHADOW evidence**, not a new backtest — consistent with the current architecture-freeze/forward-evidence-campaign posture already in effect for this system.
3. No implementation, deployment, backfill, or replay happens until you review this design and explicitly authorize the next step.
