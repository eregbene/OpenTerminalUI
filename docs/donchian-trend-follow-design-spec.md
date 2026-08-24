# donchian_trend_follow — Design & Implementation Spec

**Status: design only. No code written yet, nothing registered, nothing deployed.**

First of 4 new strategies (`docs/new-strategies-redundancy-audit.md` covers the redundancy check for all 4 — see that for why this is genuinely new, not a duplicate of `breakout`/`trend_pullback`/`smc_continuation`). Built one at a time per your explicit instruction, same rigor as the mean_reversion/trend_pullback work.

**Hypothesis:** established directional momentum → N-bar price breakout → volatility confirmation → ride persistent trend.

---

## 1. Why this is not `breakout` (read `backend/mt5_strategies/families/breakout.py` directly, not assumed)

`breakout`'s actual mandatory core, at default settings, is much thinner than it might seem:

- Trigger: **any** M15 BOS (SMC swing-structure break) within the last 5 bars. Not a fixed-width channel — a swing-based structural break, which fires far more liberally (a BOS just means "swing structure broke," not "price cleared a defined N-bar range").
- HTF alignment: **off by default** (`MT5_BREAKOUT_HTF_GATE_ENABLED=False`).
- ATR buffer requirement: **off by default** (`MT5_BREAKOUT_ATR_BUFFER_ENABLED=False`).
- **No volatility-expansion requirement at all**, mandatory or otherwise.
- Known track record: **negative every single year 2018–2026** in the 8-year audit (-0.183R pooled, PF 0.76, 42.2% immediate-failure rate).

`donchian_trend_follow` is a deliberately stricter, differently-triggered hypothesis:

| | `breakout` (current, default settings) | `donchian_trend_follow` (proposed) |
|---|---|---|
| Trigger definition | SMC swing-structure BOS | Fixed N-bar Donchian channel breakout (classic Turtle-style, a completely different, coarser definition — price must clear the actual N-bar high/low, not just break the most recent swing) |
| Volatility confirmation | None | **Mandatory** |
| HTF alignment | Optional (off by default) | **Mandatory** |
| Regime relationship | Hard-disabled in `CHOP_RANGING` only | No hard `ctx.regime` gate at all (see §4) |
| Historical track record here | Negative every year | Unknown — this is what validation is for |

Also reuses `breakout.py`'s own retest-vs-immediate-entry *pattern* (`_find_retest_hold`) rather than reinventing it — see §3.

**Honest framing:** "breakout"-shaped hypotheses have already underperformed in this specific market/symbol-set once. This strategy adds real, non-trivial discipline on top (volatility confirmation + mandatory HTF alignment + a coarser, harder-to-satisfy trigger) specifically to test whether that discipline is what was missing — not a guarantee it will do better.

**Why this is not `smc_continuation`:** that strategy's mandatory core is H4 trend → M15 BOS → displacement, i.e. a *structural* continuation hypothesis anchored on the higher timeframe. `donchian_trend_follow`'s core is a fixed-width M15 channel break with its own H1 (not H4) confirmation — a classic trend-following breakout, not an SMC structural continuation. Distinct trigger mechanism, distinct timeframe anchor.

**Why this is not `trend_pullback`:** that strategy enters on a *retracement into* an established trend; this strategy enters on the *breakout itself* (or its retest), never a pullback into an existing EMA zone.

---

## 2. Feature table

| Feature | Purpose | Core/Supporting/Opposing | Existing Bensim component | New code required | Double-counting risk |
|---|---|---|---|---|---|
| N-bar Donchian high/low | Trigger definition | **CORE** | None reusable (the equities-engine's 2-line version lives in a to-be-removed module) | Small — 2 lines of pandas rolling max/min, computed on `ctx.m15_rows` excluding the current bar (point-in-time safe, same exclusion convention as the equities version) | Low |
| Close beyond channel | Trigger confirmation | **CORE** | — | Trivial | Low |
| Breakout distance ≥ min ATR multiple | Avoid marginal/noise breaks | **CORE** | `ctx.atr_m15` | Trivial arithmetic | Low |
| Volatility expansion (`ctx.atr_expansion_ratio` / `ctx.market_regime`) | Confirms this isn't a low-conviction break | **CORE** | Phase-2 ADX axis, already computed, currently inert for most strategies | None — first real *trigger* consumer (not just ATR-widening) of this field | Low — genuinely distinct from `ctx.regime` (see §4) |
| H1 HTF alignment | Confirms the breakout has real directional backing | **CORE** | `ctx.htf_trend_h1` | None | Low |
| SMC BOS in breakout direction | Supporting confirmation, never the trigger | **SUPPORTING** | `m15_snapshot.breaks` | None | Low — explicitly kept supporting per your instruction not to let SMC become the core |
| No opposing CHoCH/MSS recently | Trend-integrity confirmation | **SUPPORTING** | `_recent_structure_break_against` (inverted positive, same pattern as trend_pullback's proposal) | None | Low |
| Displacement on/near breakout bar | Confirms real momentum behind the break | **SUPPORTING** | `m15_snapshot.displacements` | None | Low |
| Breakout occurs near a real liquidity level (EQH/EQL) | Confirms real stops are being run, not a random level | **SUPPORTING** | `_eqh_eql_touch_count` | None | Low — same confirmation-only contract already validated elsewhere |
| Retest-and-hold entry mode | Second entry path, compared against immediate | **CORE (alternate mode)** | `breakout.py::_find_retest_hold` — proposed extraction (see §3) | Small refactor: extract the core scan logic into a `_shared.py` helper taking `(rows, break_bar_index, broken_level, direction)` instead of a `StructureBreak`, so both `breakout.py` and this strategy share one implementation | Low once shared |
| Opposing structure break right at the breakout | Contradicts the setup | **OPPOSING** | `_recent_structure_break_against` | None | Low |
| HTF conflict | Direct contradiction | **OPPOSING** (already folded into CORE as a hard gate, not double-counted as a separate opposing line) | `ctx.htf_trend_h1` | None | N/A — this is a gate, not also scored separately |
| Large opposing structural level immediately ahead | Headwind close to entry | **OPPOSING** | `_opposing_structural_level` | None | Low |
| Initial stop | Risk definition | Geometry (not evidence) | `_dynamic_stop`, structural reference = the **opposite** side of the Donchian channel | None | N/A |
| Target | Let winners run | Geometry (not evidence) | `_structural_take_profit` (already flag-gated, already built for exactly "target the next real level instead of a flat multiple") — falls back to a **generous** flat ATR multiple (proposed 4.0x, wider than any existing strategy's flat target, reflecting the "ride the trend" hypothesis) when unavailable | None | N/A |
| Trailing/runner exit behavior | Preserve +2R/+3R/+5R winners | **Not this strategy's job** | **Adaptive Trade Manager** (post-entry position management, already exists, already runs for every strategy per the pipeline) | None — explicitly NOT duplicated here | N/A |

---

## 3. Reuse opportunity: generalize `_find_retest_hold`

`breakout.py::_find_retest_hold(ctx, break_obj: StructureBreak, direction)` already does exactly the retest-and-hold scan this strategy needs, but it's typed to a `StructureBreak` object (reads `break_obj.bar_index`/`break_obj.broken_level`). Proposed: extract its core loop into `_shared.py` as `_find_level_retest_hold(ctx, *, level_bar_index: int, broken_level: float, direction: str) -> tuple[int, float] | None`, then have `breakout.py` call it with `break_obj.bar_index`/`break_obj.broken_level` (behavior-identical, zero change to breakout's own output) and `donchian_trend_follow.py` call it with the Donchian channel's own edge. One implementation, two callers — exactly the pattern this codebase already uses for `_liquidity_sweep_precedes`, `_recent_structure_break_against`, etc.

---

## 4. Regime: deliberately no hard `ctx.regime` gate (learned from trend_pullback's own audit)

The live audit that started the mean_reversion/trend_pullback work found the outer `ctx.regime` hard gate duplicating and out-competing trend_pullback's own internal trend judgment, suppressing volume for no clear benefit. This strategy is designed from the start to avoid repeating that mistake: **no entry in `STRATEGY_FAMILIES["donchian_trend_follow"]["regimes"]`** (empty tuple, same pattern as `mtfai1`/`smc_continuation`). The volatility-expansion mandatory condition (§2, using `ctx.market_regime`/`ctx.atr_expansion_ratio`) already does the regime-relevant gating this strategy actually needs — a second, separate `ctx.regime` check would be the exact double-gating problem already diagnosed and fixed for trend_pullback, applied preemptively here rather than discovered later.

---

## 5. Decision flow (illustrative — weights/thresholds need their own OOS validation before any number is trusted)

```
evaluate_donchian_trend_follow(ctx):
    if not spread_ok(ctx): return no_signal("SPREAD")

    channel_high, channel_low = donchian_channel(ctx.m15_rows, n=20)   # excludes current bar
    price = closes[-1]
    if price > channel_high:
        direction = "LONG"; channel_edge = channel_high; opposite_edge = channel_low
    elif price < channel_low:
        direction = "SHORT"; channel_edge = channel_low; opposite_edge = channel_high
    else:
        return no_signal("no_channel_breakout")

    breakout_distance_atr = abs(price - channel_edge) / atr
    if breakout_distance_atr < MIN_BREAKOUT_ATR:           # avoid marginal/noise breaks
        return no_signal("breakout_distance_below_minimum")

    if not volatility_expansion_confirmed(ctx):             # ctx.atr_expansion_ratio / ctx.market_regime
        return no_signal("no_volatility_expansion")

    if htf_conflict(ctx, direction):                        # ctx.htf_trend_h1
        return no_signal("htf_conflict")

    mode = os.getenv("MT5_DONCHIAN_ENTRY_MODE", "BREAK_AND_GO")
    if mode == "RETEST_AND_HOLD":
        hold = _find_level_retest_hold(ctx, level_bar_index=breakout_bar_index, broken_level=channel_edge, direction=direction)
        if hold is None:
            return no_signal("no_confirmed_retest_and_hold")
        entry_price = hold.closest_price   # or current price, same convention as breakout.py
    else:
        entry_price = price

    bos_supporting = with_trend_bos_present(ctx, direction)          # supporting, bounded
    no_opposing_break = not _recent_structure_break_against(ctx, direction, lookback_bars=10)  # supporting
    displacement_supporting = displacement_near_breakout(ctx, direction)  # supporting
    eqh_eql_supporting = _eqh_eql_touch_count(ctx, side=..., price=entry_price, atr=atr)        # supporting
    opposing_zone_ahead = _opposing_structural_level(ctx, direction)  # opposing, if close

    strength = BASE + bounded_contributions(bos_supporting, no_opposing_break, displacement_supporting, eqh_eql_supporting) \
               - opposing_penalty(opposing_zone_ahead)
    strength = clip(strength, 50, 100)

    stop, _ = _dynamic_stop(ctx, direction, entry_price, opposite_edge, atr, min_atr_mult=1.2, max_atr_mult=3.0)
    structural = _structural_take_profit(ctx, direction=direction, entry=entry_price, stop=stop, atr=atr)
    target = structural["tp1"] if structural else entry_price + (4.0 * atr if direction=="LONG" else -4.0 * atr)

    return signal(...)
```

---

## 6. Open questions for your review before implementation

1. **Donchian period N**: proposing **20 bars** (M15, ≈5 hours) as the starting value — a standard, well-known Turtle-system-adjacent period, not searched/tuned. Fine to adjust before implementation if you have a different preference.
2. **`MIN_BREAKOUT_ATR`**: proposing **0.3×ATR** beyond the channel edge (looser than `breakout.py`'s own optional 0.5x buffer, since the channel itself is already a stricter/coarser trigger than a swing BOS).
3. **Default entry mode**: proposing `BREAK_AND_GO` as default (matching `breakout.py`'s own default), with `RETEST_AND_HOLD` available via the same env-var pattern, both to be compared head-to-head in validation exactly as requested.
4. **Fallback flat target multiple**: proposing **4.0×ATR** (wider than any existing strategy's flat target — reflects "ride the trend" intent) for when `_structural_take_profit` is unavailable/disabled.

None of this is implemented yet. Confirm the above (or adjust) and I'll write the actual strategy file, register it SHADOW-only in `STRATEGY_FAMILIES`, run targeted regressions, and move to strategy #2 only after this one is through its own review checkpoint — matching the pacing you asked for.
