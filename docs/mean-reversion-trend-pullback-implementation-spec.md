# Implementation Specification: mean_reversion & trend_pullback Evidence Upgrade

**Status: specification only. No code changed, no flags activated, no risk limits touched, no new backfill/replay run.**

Builds directly on `docs/mean-reversion-trend-pullback-evidence-architecture-design.md` (the prior audit/architecture pass) — this document does not re-derive that inventory, it turns it into an implementation-ready spec: exact feature tables, decision flow, redundancy management, external-indicator evaluation, and the smallest safe rollout sequence.

---

## 1. Identity preservation — the two hypotheses stay distinct

**Mean Reversion**: `price stretch/extreme → reversal location → liquidity/SMC evidence → reversal confirmation → confidence`
**Trend Pullback**: `established trend → pullback → structural location → trend integrity → resumption evidence → confidence`

Both become multi-evidence like `mtfai1` *architecturally* (several inputs feeding one score) but stay different *hypotheses*: mean_reversion still bets on a stretched price snapping back against the recent move; trend_pullback still bets on a continuing move resuming after a shallow retrace. Neither becomes "does structure agree with an MA crossover" (mtfai1) or "did a with-trend H4 BOS+displacement already happen, buy the retest" (smc_continuation). Section 9 below verifies this explicitly for each final design.

---

## 2. Redundancy map — collapsing correlated evidence into independent clusters first

This has to be decided *before* the feature tables, because it changes what goes in them. Six correlation clusters, each meant to become **one** scored input, not several:

| Cluster | What's correlated | Why they're the same fact | How the spec below handles it |
|---|---|---|---|
| **Directional bias** | EMA20-vs-EMA50, `ctx.htf_trend_h1`/`h4` (SMC swing-derived `classify_trend`), `ctx.regime`, `ctx.market_regime`/`adx_m15`, HH/HL-LH/LL swing sequence | `htf_trend_h1/h4` **is** the HH/HL-LH/LL swing sequence, already aggregated by `classify_trend()` — reading raw swings again as a second, separate "structure agrees" bullet would score the same computation twice. `ctx.regime` and `ctx.market_regime` are two *different* classifiers of the same underlying "is this trending" question. | EMA20/50 stays the M15 local-trend **core** (trend_pullback only). HTF alignment (H1, and now also H4 as a second, genuinely different timeframe) is checked once, as itself — never re-derived from raw swings a second time. `ctx.regime`/`ctx.market_regime` are folded into **one** bounded regime-evidence input, not two independent ones (§4.4) |
| **Structural break state** | BOS, CHoCH, MSS, displacement | MSS is not independently detected — it's "CHoCH confirmed by a co-located displacement" (confirmed by direct read of `structure.py`'s break-classification logic). Scoring "displacement present" and "MSS present" as two separate bonuses double-counts the same candle event whenever the displacement is what promoted a CHoCH into an MSS. | One graded "opposing structure" line: none < CHoCH < MSS (severity-ordered, MSS scores a larger penalty since it already implies displacement, never additive with a separate displacement bonus for the *same* break) |
| **Sweep evidence** | Generic liquidity sweep, EQH/EQL sweep | `equal_level_sweeps` is the *same* sweep detector re-run against the EQH/EQL cluster specifically (confirmed) — an EQH/EQL sweep is a strictly-stronger case of a generic sweep, not a second independent fact. `liquidity_sweep_reversal.py` already treats it this way (`swept_level_is_eqh_eql` is a cross-reference field, not a second scored bonus). | One sweep-evidence line; whether the swept level was also a repeated-touch EQH/EQL pool *upgrades* its weight, it doesn't add a second bonus |
| **Location/zone quality** | Order block overlap, FVG overlap, premium/discount extreme, OTE zone, nearby `LiquidityLevel` (S/R) | These frequently co-occur at the same price — a genuinely strong zone often trips 3-4 of these simultaneously. Summing all of them linearly would let one good location dominate the score just by being "everything at once," while a location with only one weaker factor scores far lower even if it's still a real edge. | One capped **location-quality composite** (§4.2/§5.2): counts how many distinct zone types align, diminishing returns past the second, hard cap — never a flat sum of 5 independent bonuses |
| **Momentum/oscillator** | RSI level, RSI divergence, squeeze_momentum | RSI level and RSI divergence are genuinely different information (level = *how far* price is from its own recent mean; divergence = whether momentum is *confirming or fading* on the latest push) — kept as two separate, deliberately distinct inputs. `squeeze_momentum` is excluded entirely per its own prior negative OOS finding (§6). | RSI level = core trigger path A. RSI divergence = new, separate, independent supporting input (§6). Squeeze stays observability-only, never scored |
| **Confirmation candle** | Body-direction reaction candle (`_reaction_candle_confirms`), wick/rejection length | Currently only one of these exists (body direction). A wick-based rejection metric is genuinely different information (how much of the range was rejected, not just which way it closed) | Treated as one upgraded confirmation-candle input once the wick metric is added (§6) — not two separate candle checks |

Net effect: roughly **5-6 independent evidence clusters** per strategy, matching your own stated preference, not 15 flat additive lines.

---

## 3. External indicator/concept evaluation

| Concept | Classification | Reasoning |
|---|---|---|
| SMC concepts (LuxAlgo/LonesomeTheBlue/NeoButcher-style: swings, BOS, CHoCH, OB, FVG, EQH/EQL, premium/discount) | **ALREADY_HAVE** | Bensim's own `backend/market_structure` engine is an independent, from-scratch implementation of the identical concept set (confirmed field-by-field in the prior audit). No reason to reimplement, and reimplementing from a named public script would risk exactly the "don't copy proprietary code" concern you raised — the existing engine already covers this ground natively |
| Breaker Blocks | **PARTIALLY_HAVE** → **not recommended now** | `BreakerBlock` model class exists (`models.py`), but there is no detector producing it — confirmed absent. Conceptually a breaker block is a refinement of order-block information (a failed OB that flips role) — for these two strategies specifically, existing OB + FVG + premium/discount already carry the location-quality signal; a breaker-block detector would add real implementation surface for a secondary/tertiary refinement, not a new independent fact. Defer — revisit only if a future strategy specifically needs the flipped-OB distinction |
| Mitigation Blocks | **PARTIALLY_HAVE** → **not recommended now** | Same situation — model class exists, no detector. `OrderBlock.status` (ACTIVE/PARTIAL/INVALIDATED) already captures "has this zone been touched/invalidated" at the granularity these two strategies need. Not worth a new detector here |
| Volume Sentiment Breakout Channels (AlgoAlpha) | **REJECT** | Depends on real traded volume / buy-sell delta. MT5 forex/CFD feeds only expose tick-count volume, already a known weak proxy elsewhere in this codebase (`DisplacementEvent.volume_ratio` is declared but always `None` — never wired, for exactly this reason). Building a volume-sentiment channel on top of tick-count volume would encode false precision, not new information |
| SuperTrend (KivancOzbilgic) | **REDUNDANT** | A public, simple ATR-band trailing-flip trend indicator — legitimately reimplementable, no IP issue. But it's a fourth way to ask "which way is the market trending," directly inside the **directional bias** cluster (§2) alongside EMA20/50, HTF trend classification, and ADX regime. Three independent trend proxies already triangulate direction; a fourth correlated one adds complexity without a new information dimension — exactly the "RSI/EMA/SuperTrend/regime/ADX overlap" risk you flagged |
| Price Action Concepts (PAC) — full pattern library (engulfing, doji, stars, inside bar, etc.) | **PARTIALLY_HAVE** → mostly **REJECT**, one piece **GENUINELY_ADDITIVE** | The one confirmed real gap: no wick/rejection-length detector exists anywhere (`_reaction_candle_confirms` is body-direction only). That specific piece is additive (§6). A full candlestick pattern zoo on top of it is explicitly the "indicator soup" this task warns against — not recommended |
| Signals & Overlays — dynamic trend filtering / "Smart Trail" | **REDUNDANT** for entry evidence | Conceptually an ATR-trailing dynamic support/resistance line — same family as SuperTrend, and overlaps with Bensim's existing `_dynamic_stop`/`construct_dynamic_stop` ATR-bounded stop machinery. Its natural home is trade *management* (adaptive_management), not a new entry-evidence input — out of scope for these two strategies' entry logic |
| Oscillator Matrix — RSI/MFI divergence | **GENUINELY_ADDITIVE** (RSI divergence only) | Divergence is genuinely different information from RSI level (momentum confirming vs fading the latest price extreme) and no divergence detector exists anywhere in Bensim today. Scoped narrowly: price-vs-RSI divergence only, not a multi-oscillator "matrix" |
| Oscillator Matrix — money-flow | **REJECT** | Same real-volume data-quality problem as Volume Sentiment Breakout Channels |
| Oscillator Matrix — generic momentum exhaustion | **REDUNDANT** | Overlaps with RSI extreme + ATR-stretch (already proposed) and with structural CHoCH/MSS opposing-evidence — a third way to say "this move looks tired" adds correlation, not independence |
| Squeeze Momentum | **ALREADY_HAVE, REJECT for scoring** | Already computed (`ctx.squeeze_state`/`squeeze_momentum_value`), already chronologically OOS-validated **negative** (sign-flips out of sample), already correctly restricted to observability-only by the codebase's own existing contract. Stays exactly as-is in both new designs — evidence-trail only, never scored |

**Net additions from this whole evaluation: two.** A wick/rejection-length metric, and an RSI-vs-price divergence check. Everything else is either already present, redundant with what's already present, or rejected on data-quality grounds.

---

## 4. mean_reversion — implementation spec

`price stretch/extreme → reversal location → liquidity/SMC evidence → reversal confirmation → confidence`

### 4.1 Two trigger paths

**Path A — traditional extreme**: RSI(14) ≤ 30 or ≥ 70 (unchanged from production).
**Path B — structural/stretch reversal**: price deviation from EMA20 (or session VWAP) ≥ a bounded ATR multiple, **AND** at least one item from the Strong Confirmation tier (§4.3) is present, **AND** the location-quality composite (§4.2) clears a minimum bar. Path B exists specifically so a real reversal setup doesn't have to wait for RSI to numerically cross 30/70 when independent structural evidence already makes the case — but it is not a loosening of the bar overall: it substitutes RSI's single condition for a *conjunction* of stretch + confirmation + location, not a single looser number.

Either path satisfies the CORE TRIGGER; they are not combined additively, and never both required.

### 4.2 Location-quality composite (supporting, capped — see §2)

Counts how many of {EQH/EQL touch (opposing side), OB/FVG overlap, premium/discount extreme (`normalized_current_position` near 0 or 1), nearby `LiquidityLevel`} are present. Contribution is capped and sub-linear (e.g., first factor contributes most, each additional factor contributes less, total contribution bounded) — never a flat sum of four independent bonuses for what's frequently one real zone.

### 4.3 Strong confirmation tier

Best-of, not sum-of: liquidity sweep in the reversal direction, CHoCH in the reversal direction, or displacement in the reversal direction. Whichever single one is strongest contributes the bonus; having two of these present at once (common, since a sweep often precedes the CHoCH that confirms it) does not double the score.

### 4.4 Regime as one bounded input

`ctx.regime`'s existing hard gate (`ranging`/`low_volatility` only) is **kept unchanged** — per the prior audit, this isn't mean_reversion's bottleneck (~28% of cycles already pass it) and there's no evidence it's miscalibrated. What changes: the currently-inert `ctx.market_regime`/`adx_m15` axis (`_regime_strategy_disabled`/`_regime_strength_bonus`, already coded for mean_reversion, sitting behind `MT5_REGIME_FILTER_ENABLED=False`) becomes the **one** additional soft regime input — not stacked with a second, separate reading of `ctx.regime` itself.

### 4.5 Feature table

| Feature | Purpose | Core/Supporting/Opposing | Existing Bensim component | New code required | Double-counting risk |
|---|---|---|---|---|---|
| RSI(14) extreme | Path A trigger | **CORE** (Path A) | inline `backend.core.technicals.rsi` | none | Low — sole trigger for this path |
| ATR-normalized EMA/VWAP deviation | Path B trigger | **CORE** (Path B) | `ctx.atr_m15`; EMA inline (`backend.core.technicals.ema`); VWAP needs light extraction from `vwap_reversion.py` into a reusable helper | Small — new arithmetic + one small extraction refactor | Low, distinct statistic from RSI |
| RSI-vs-price divergence | Momentum-fade confirmation, independent of RSI level | **SUPPORTING** | none | New, small (compare recent swing highs/lows in price vs RSI series) | Low if scoped to divergence only, not folded into the RSI-level trigger |
| Location-quality composite (EQH/EQL, OB, FVG, premium/discount, S/R) | Where the reversal is happening | **SUPPORTING** | `_eqh_eql_touch_count`, `m15_snapshot.order_blocks`/`.imbalances`/`.dealing_ranges`/`.liquidity_levels` | Small — one new capped aggregator function, no new detectors | **High if not capped** — this is the cluster §2 exists to protect against |
| Strong confirmation (sweep / CHoCH / displacement in reversal direction, best-of) | Higher-conviction reversal signature | **SUPPORTING (elevated tier)**, also gates Path B | `m15_snapshot.liquidity_sweeps`, `.breaks`, `.displacements` | Small — composition only | **High if summed instead of best-of** |
| Wick/rejection length | Was the extreme actually rejected, not just touched | **SUPPORTING** | none (gap confirmed real) | New, small (wick-vs-body/range ratio on the extreme bar) | Low, genuinely new dimension vs body-only `_reaction_candle_confirms` |
| Squeeze state/momentum | Observability only | **NONE — never scored** | `_squeeze_evidence` | none | N/A by design (prior OOS-negative finding) |
| `ctx.regime` (ranging/low_volatility gate) | Hard eligibility | **CORE** (unchanged) | `regime_compatible()` | none | Low, unchanged from production |
| `ctx.market_regime`/`adx_m15` | Soft regime confirmation/opposition | **SUPPORTING / OPPOSING** | `_regime_strategy_disabled`, `_regime_strength_bonus` (already coded, currently inert) | none — just needs the flag/wiring turned on for this strategy | Low if this is the *only* regime input scored (§4.4) |
| HTF (H1) trend strongly against the faded direction | Textbook way mean-reversion loses | **OPPOSING** | `ctx.htf_trend_h1` | none | Low |
| No location evidence at all ("naked" RSI extreme) | Penalize a stretch with zero structural support | **OPPOSING** | Absence of §4.2's composite | none | Low |

### 4.6 Decision flow (illustrative, weights not final — needs its own OOS validation before any real number ships)

```
evaluate_mean_reversion(ctx):
    if not spread_ok(ctx): return no_signal("SPREAD")
    if regime_disabled(ctx, "mean_reversion"):           # ctx.regime hard gate, unchanged
        return no_signal("REGIME_DISABLED")

    rsi = rsi14(closes)
    stretch = atr_normalized_deviation(price, ema20_or_vwap, atr)   # new, small

    path_a = rsi <= 30 or rsi >= 70
    direction = infer_direction(rsi, stretch)             # unchanged convention: fade the extreme

    location = location_quality_composite(ctx, direction) # new, capped (§4.2)
    strong_confirm = best_of(sweep_evidence, choch_evidence, displacement_evidence)  # (§4.3)

    path_b = (stretch >= STRETCH_MIN) and strong_confirm.present and location >= LOCATION_MIN

    if not (path_a or path_b):
        return no_signal("no_stretch_trigger")

    divergence = rsi_price_divergence(ctx, direction)     # new, small
    rejection  = wick_rejection_score(ctx)                 # new, small
    regime_soft = market_regime_evidence(ctx, "mean_reversion")  # existing, inert -> wired
    htf_opposing = htf_trend_conflict_penalty(ctx, direction)
    naked_penalty = 0 if location > 0 or strong_confirm.present else NAKED_PENALTY

    strength = BASE(55) + location + strong_confirm.score + divergence + rejection \
               + regime_soft - htf_opposing - naked_penalty
    strength = clip(strength, 50, 100)

    stop, target = existing unchanged geometry (_dynamic_stop, flat ATR target)
    return signal(...)
```

---

## 5. trend_pullback — implementation spec

`established trend → pullback → structural location → trend integrity → resumption evidence → confidence`

### 5.1 Core stays the strategy's own trend judgment

Unchanged mandatory chain: EMA20-vs-EMA50 decides direction → price inside the pullback zone → H1 HTF non-conflict. This is deliberately preserved — it's what makes the strategy *its own* trend judgment rather than a downstream consumer of someone else's classifier.

### 5.2 Structural location (supporting, capped composite — same pattern as §4.2)

OTE zone hit, OB/FVG overlap, discount positioning for LONG / premium for SHORT (`normalized_current_position`). Same capping logic as mean_reversion's location cluster — these frequently co-occur at a genuinely strong pullback zone and must not stack linearly.

### 5.3 Trend integrity (supporting, bounded)

A recent M15 BOS in the local-trend direction (event-level confirmation, distinct from the aggregate `htf_trend_h1` reading — not a re-derivation of the same swing sequence, see §2) **and/or** absence of a recent opposing CHoCH/MSS (currently a hard off-by-default gate via `_recent_structure_break_against`; proposed as a **positive contribution when clear**, not only a rejection when violated).

### 5.4 Resumption confirmation (supporting, best-of)

Displacement back with-trend, a fresh with-trend BOS/MSS after the pullback extreme, or a liquidity sweep on the retracement's trapping side — best-of, not summed, since these frequently co-occur at a genuine resumption.

### 5.5 Regime — from hard gate to bounded evidence

This is the direct response to the live audit's finding (8/10 symbols with valid setups killed by `regime_compatible()` before ever being scored). Proposed: remove `trend_pullback` from `STRATEGY_FAMILIES[...]["regimes"]`'s hard-gate table (matching `mtfai1`/`smc_continuation`'s unrestricted pattern) and fold `ctx.regime` + `ctx.market_regime`/`adx_m15` into **one** bounded soft input — never two separately-scored regime readings (§2), and never simply "off" — a genuinely choppy/opposing regime reading still counts as opposing evidence, it just can no longer unilaterally block evaluation the way it does today.

**Explicitly not**: removing the gate and leaving nothing in its place. The strategy's own EMA20/50+H1 core is still a real, mandatory trend requirement — regime becomes a second opinion that can nudge the score, not a rubber stamp that opens the floodgates.

### 5.6 Feature table

| Feature | Purpose | Core/Supporting/Opposing | Existing Bensim component | New code required | Double-counting risk |
|---|---|---|---|---|---|
| EMA20-vs-EMA50 direction | Local trend definition | **CORE** (unchanged) | inline `backend.core.technicals.ema` | none | Low — sole directional trigger |
| Pullback zone (ATR band around EMA20/50) | Entry timing | **CORE** (unchanged) | `_in_ema_zone` | none | Low |
| H1 HTF non-conflict | Cross-timeframe agreement | **CORE** (unchanged) | `ctx.htf_trend_h1` | none | Low — different timeframe from the EMA core, not a restatement of it |
| Structural location composite (OTE, OB/FVG, premium/discount) | Pullback-completion quality | **SUPPORTING** | `_ote_zone_for_direction`, `m15_snapshot.imbalances`/`.order_blocks`/`.dealing_ranges` | Small — one new capped aggregator, reuses existing OTE_OB_FVG-flag logic as a supporting bonus instead of an exclusive alternate mode | **High if not capped** |
| M15 BOS in local-trend direction | Event-level trend-integrity confirmation | **SUPPORTING** | `m15_snapshot.breaks` | Small — filter to M15 + local direction | Low if kept distinct from the `htf_trend_h1` aggregate (§2) |
| No opposing CHoCH/MSS present | Trend-integrity confirmation | **SUPPORTING** (positive form) | `_recent_structure_break_against` | none — reuse, inverted to a positive contribution | Low |
| Liquidity sweep during retracement (trapping side) | Resumption confirmation | **SUPPORTING (best-of w/ below)** | `_liquidity_sweep_precedes`-style composition | none | High if summed with displacement/BOS instead of best-of |
| Displacement back with-trend | Resumption confirmation | **SUPPORTING (best-of)** | `m15_snapshot.displacements` | none | see above |
| Fresh BOS/MSS with-trend post-pullback | Resumption confirmation | **SUPPORTING (best-of)** | `m15_snapshot.breaks` | none | see above |
| `ctx.regime` + `ctx.market_regime`/`adx_m15` | Trend-quality second opinion | **SUPPORTING / OPPOSING, one bounded input** (was CORE hard gate) | `regime_compatible()` removed from this strategy's table; `_regime_atr_mult_scale`-style pattern extended | Small — config change (regime tuple → `()`) + one new bounded scoring function | **High if both classifiers scored independently** — must be one input (§2, §5.5) |
| Opposing CHoCH/MSS against trend | Direct contradiction of the setup | **OPPOSING**, severity-graded (CHoCH < MSS) | `_recent_structure_break_against` + `break_kind` | Small — severity grading | Low if MSS/CHoCH stay one graded line, not two |
| No reaction candle at pullback low/high | Weak confirmation the retrace has turned | **OPPOSING** (soft, was hard-gate-behind-flag) | `_reaction_candle_confirms` | none | Low |
| Wick/rejection length at the pullback extreme | Stronger reversal-of-retracement confirmation | **SUPPORTING** | none (gap confirmed real) | New, small — same helper as §4's wick metric, shared | Low, shared component reduces net new surface |

### 5.7 Decision flow (illustrative, weights not final)

```
evaluate_trend_pullback(ctx):
    if not spread_ok(ctx): return no_signal("SPREAD")
    ema20, ema50 = ema(closes, 20), ema(closes, 50)
    direction = "LONG" if ema20 > ema50 else "SHORT"      # unchanged
    if not in_pullback_zone(ctx, direction): return no_signal("not_in_pullback_zone")
    if htf_conflict(ctx, direction): return no_signal("htf_conflict")

    location  = structural_location_composite(ctx, direction)   # new, capped (§5.2)
    integrity = trend_integrity_score(ctx, direction)             # M15 BOS + no-opposing-break, bounded (§5.3)
    resumption = best_of(sweep_evidence, displacement_evidence, fresh_bos_evidence)  # (§5.4)
    regime_evidence = regime_soft_score(ctx, "trend_pullback")   # NEW: replaces the old hard regime_compatible() gate for this strategy only (§5.5)

    opposing_break = graded_opposing_structure_break(ctx, direction)   # CHoCH < MSS, one line (§2)
    rejection_bonus = wick_rejection_score(ctx)                          # shared helper w/ mean_reversion
    weak_reaction_penalty = 0 if reaction_candle_confirms(ctx, direction) else SOFT_PENALTY

    strength = BASE(70) + location + integrity + resumption + regime_evidence \
               + rejection_bonus - opposing_break - weak_reaction_penalty
    strength = clip(strength, 50, 100)

    stop, target = existing unchanged geometry (_dynamic_stop, flat ATR target)
    return signal(...)
```

---

## 6. Confirmed gaps and what to do about each

| Gap | Confirmed real? | Materially benefits these 2 strategies? | Recommendation |
|---|---|---|---|
| Wick/structural rejection detection | **Yes** — `_reaction_candle_confirms` is body-only, no wick-length detector exists anywhere | Yes — directly strengthens both mean_reversion's reversal-confirmation and trend_pullback's pullback-completion confirmation | **Add** — one small, shared helper in `_shared.py` |
| Breaker Block detection | Yes — model class exists, zero detector | Marginal — existing OB+FVG+premium/discount already carry the location signal these two strategies need | **Defer** — not part of this rollout |
| Mitigation Block detection | Yes — model class exists, zero detector | Marginal — `OrderBlock.status` already covers the needed granularity | **Defer** — not part of this rollout |
| Underused premium/discount | Yes — computed, zero current consumers | Yes — directly proposed as location-composite input for both | **Add via consumption, not new detection** — the data already exists |

---

## 7. Comparison against mtfai1 / smc_continuation / current production self

| | mtfai1 | smc_continuation | trend_pullback (current prod) | trend_pullback (proposed) | mean_reversion (current prod) | mean_reversion (proposed) |
|---|---|---|---|---|---|---|
| Core trigger | MA crossover (M15 fast/slow SMA) confirmed by H1/H4 avg | H4 trend → M15 BOS with-trend → displacement | EMA20/50 relationship + zone + H1 non-conflict | **same** EMA20/50 + zone + H1 non-conflict (unchanged) | RSI(14) extreme | RSI(14) extreme **or** ATR-stretch + confirmation (Path A/B) |
| SMC role | confirmation gates only (swing-trend agree, EQH/EQL block) | **is** the mandatory core | none | supporting only (location + integrity + resumption) | none | supporting only (location + strong-confirmation) |
| Regime | none, unrestricted by design | none, unrestricted by design | hard gate, `trending_up`/`trending_down` only | **soft evidence**, not a gate | hard gate, `ranging`/`low_volatility` only | hard gate **unchanged** + new soft ADX-axis input |
| Horizon | M15/H1/H4 | H4→M15 | M15/H1 | M15/H1 (unchanged) | M15 | M15 (unchanged) |
| What makes it still itself | — | — | — | Same mandatory trigger and same timeframe as production; only what happens *around* an already-qualifying setup changed | — | Path A is byte-identical to production; Path B is a strictly additional, narrower path, never a loosening of Path A |

**Why trend_pullback (proposed) ≠ smc_continuation:** different timeframe for the trend judgment (M15/H1 local EMA vs H4 macro structural trend), different mandatory core (EMA relationship vs BOS+displacement), SMC evidence is supporting-only here vs mandatory there.

**Why trend_pullback (proposed) ≠ mtfai1:** mtfai1 has no trend-pullback concept at all (a crossover, not a pullback-into-a-zone entry) and no location/OTE/OB-FVG evidence of any kind.

**Why mean_reversion (proposed) ≠ liquidity_sweep_reversal:** sweep/CHoCH there is a *mandatory sequential chain* and the only way in; here it's one best-of item inside a supporting tier, and RSI/ATR-stretch remains the actual trigger.

**Why mean_reversion (proposed) ≠ support_resistance_bounce:** S/R proximity there is the mandatory trigger with no RSI involvement at all; here it's one component of a capped location composite, never the trigger by itself.

---

## 8. Historical Intelligence — unchanged position, explicitly confirmed

Pipeline stays exactly as it is today: `strategy setup/evidence → base confidence (including everything in §4/§5 above) → Historical Intelligence ranking adjustment (±10, bounded) → portfolio/risk → execution`. Nothing in this spec adds a new HI call earlier in the pipeline, and HI is never a trigger — it only ever adjusts a candidate that the strategy's own evidence already produced.

---

## 9. Final recommendation

**Add (small, genuinely new code):**
1. Wick/rejection-length helper (shared between both strategies).
2. RSI-vs-price divergence check (mean_reversion only).
3. ATR-normalized EMA/VWAP-deviation stretch calc (mean_reversion Path B) — includes lightly extracting VWAP out of `vwap_reversion.py` into a reusable helper.
4. Two capped location-quality composite functions (one per strategy, same pattern, not identical inputs).
5. One bounded regime-evidence scorer per strategy, replacing trend_pullback's hard `regime_compatible()` gate and wiring mean_reversion's already-coded-but-inert ADX-regime bonus.

**Reuse as-is (no new code, pure composition):** `_eqh_eql_touch_count`, `_ote_zone_for_direction`, `_recent_structure_break_against`, `_reaction_candle_confirms`, `_liquidity_sweep_precedes`-style sweep composition, `m15_snapshot.breaks`/`.displacements`/`.order_blocks`/`.imbalances`/`.dealing_ranges`, `_dynamic_stop`, `_squeeze_evidence` (kept observability-only, unchanged contract).

**Reject:** Volume Sentiment Breakout Channels, SuperTrend, "Smart Trail"/dynamic-trend-filter overlays, money-flow, generic momentum-exhaustion oscillators, full PAC candlestick-pattern library, Breaker/Mitigation Block detectors (deferred, not rejected outright — just out of scope for this rollout).

**Smallest safe implementation sequence** (each step independently testable, no step requires the next to be useful):
1. Add the shared wick/rejection helper to `_shared.py` — pure addition, zero behavior change until a caller reads it.
2. Add the two capped location-quality composite functions — same, zero behavior change until wired in.
3. Wire mean_reversion's Path A (RSI) to also attach the new supporting-evidence fields to its `evidence` dict **without yet changing `strength`** — an observability-only rollout step, mirroring exactly how `_eqh_eql_touch_count`/`displacement_magnitude_atr` were introduced elsewhere in this codebase before being trusted to move a score.
4. Same observability-only step for trend_pullback's new supporting fields.
5. Only after real forward data accumulates on those observability-only fields (same discipline `_eqh_eql_touch_count` was held to before its own bonus was trusted) — implement Path B (mean_reversion) and the regime-gate-to-evidence change (trend_pullback) behind new, off-by-default env flags, exactly matching this codebase's existing flag-gated-rollout convention.
6. SHADOW-mode forward validation before any flag defaults to on. No new historical backfill/replay — forward evidence only, consistent with the current architecture-freeze posture.

At every step: DEMO execution unchanged, no risk limits touched, no flags default-activated, no corpus backfill.

**Status (2026-08-24): steps 1-4 implemented and deployed** (wick/rejection helper, capped location-quality composites, evidence-dict wiring for both strategies — all observability-only, verified zero strength/confidence impact via a real natural DEMO candidate). Per explicit user decision, the system now sits in a **forward evidence-collection phase**: real DEMO cycles populate the new fields on every natural candidate (executed/SHADOW/rejected), the existing outcome resolver fills in realized outcomes automatically, and steps 5-6 (Path B for mean_reversion, soft-regime scoring for trend_pullback) do not proceed until that forward evidence has actually been reviewed for whether the new fields discriminate winning from losing setups. See `project_mr_tp_evidence_forward_collection` memory for the exact review procedure.
