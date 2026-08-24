# Redundancy Audit — 4 Proposed New Strategies

**Status: research only, required before implementing any of the 4 strategies below (per explicit instruction: "if one of these proposed strategies is already functionally implemented under another name, stop and report it rather than creating duplicate code").**

Covers: `donchian_trend_follow`, `session_liquidity_breakout`, `fx_relative_momentum`, `volatility_compression_expansion`.

---

## 1. donchian_trend_follow

| New concept | Existing Bensim equivalent | Overlap | What's actually new |
|---|---|---|---|
| N-bar Donchian high/low | `backend/strategies/indicators.py:161-162` — trivial 2-line Donchian(20) in the **old equities engine**, not `mt5_strategies` | Low — a rolling max/min primitive, not a real detector | Recompute inline (2 lines), not worth importing a to-be-removed module |
| Point-in-time-safe breakout | `bars_as_of`/`build_strategy_context` | Full | Nothing new |
| ATR-normalized breakout distance | `ctx.atr_m15` | Full | New arithmetic only |
| Volatility expansion confirmation | `ctx.atr_expansion_ratio`, `ctx.market_regime==HIGH_VOLATILITY_EXPANSION` (Phase-2 ADX axis, currently inert for most strategies) | Full | First real *trigger* consumer of this field |
| HTF directional alignment | `ctx.htf_trend_h1`/`htf_trend_h4` | Full | Nothing new |
| False-break protection | `_recent_structure_break_against`, wick-rejection helper (Steps 1-4) | Full | Nothing new |
| Retest vs immediate entry | `breakout.py::_find_retest_hold` (same pattern, tied to `StructureBreak` today) | Partial | Small: generalize to a level-based helper (see donchian spec §3) |
| Initial stop | `_dynamic_stop` | Full | Nothing new |
| Trailing/runner exits (+2R/+3R/+5R) | **Adaptive Trade Manager** (post-entry, already exists) | Full — wrong layer to duplicate | Nothing belongs in the strategy evaluator here |
| SMC/BOS as supporting only | `m15_snapshot.breaks` | Full | Nothing new |

**Genuinely new**: the combination (Donchian channel as mandatory core + mandatory volatility-expansion + mandatory HTF alignment, SMC supporting only). See `docs/donchian-trend-follow-design-spec.md` for the full design.

---

## 2. session_liquidity_breakout

| New concept | Existing Bensim equivalent | Overlap | What's actually new |
|---|---|---|---|
| Asian/London/NY range high-low, previous-day high/low | **`detect_session_levels()` (`market_structure/sessions.py`) — already computes exactly this**, config-driven session windows, feeds `ctx.m15_snapshot.session_levels` | **Full** | Nothing — ready-made match |
| Session liquidity sweep | `m15_snapshot.liquidity_sweeps` | Full | Nothing new |
| Range breakout / close acceptance | `session_levels` + close-beyond-level check | Full | Trivial composition |
| Breakout displacement | `m15_snapshot.displacements` | Full | Nothing new |
| Retest of broken range | Same zone-retest pattern as trend_pullback's OTE/OB-FVG check | Partial | Small: retest-of-broken-session-level check |
| Rejection after retest | Wick-rejection helper (Steps 1-4), `_reaction_candle_confirms` | Full | Nothing new |
| FVG/OB around retest, BOS/MSS | `m15_snapshot.imbalances`/`.order_blocks`/`.breaks` | Full | Nothing new |
| ATR-normalized range size, avoid late entries | `ctx.atr_m15`, `is_session_liquidity_valid`/`_session_timing_permits` (scoped today to `{breakout, session_breakout, momentum}`) | Full | Trivial config addition |

**Critical finding vs `session_breakout`**: the existing `session_breakout` (`families/_legacy.py`) is confirmed (direct read) to be a **bare breakout-beyond-level** check — `levels = ctx.m15_snapshot.session_levels`, find the broken high/low, done. Zero liquidity-sweep, retest, rejection, FVG/OB, or BOS/MSS logic. **Not a rename** — a materially richer strategy on the same underlying levels. The redundancy question is empirical (does the extra architecture add value), to be settled in validation via a direct immediate-breakout-vs-retest and new-vs-`session_breakout` comparison.

---

## 3. fx_relative_momentum

Repo-wide search confirms: **zero existing currency-strength or relative-momentum implementation anywhere in Bensim.** The "relative_strength" hits found in equities modules are RSI-adjacent naming coincidences, not FX currency-basket strength. Genuinely new at the concept level — no redundancy risk. Reusable pieces: per-symbol closes/ATR (existing), point-in-time-safe candle fetch (existing). The 7-currency basket (USD/EUR/GBP/JPY/CHF/AUD/NZD) is derivable from the existing 10-symbol universe's own pairs without new data sources. XAUUSD excluded from this model per the original instruction (not one of the 7 currencies, no forced methodology).

---

## 4. volatility_compression_expansion

| New concept | Existing Bensim equivalent | Overlap | What's actually new |
|---|---|---|---|
| Bollinger-vs-Keltner compression (boolean ON/OFF/RELEASING) | **`squeeze_momentum.py`** — already computed as `ctx.squeeze_state`, **already chronologically OOS-validated NEGATIVE** across standalone/confirmation-filter/HI-feature/hybrid roles | **Full — this is the exact rejected signal** | Nothing — must not resurrect as-is |
| ATR percentile / realized-range percentile (continuous rank) | Not present — existing squeeze is boolean, never a percentile rank | None | Genuinely new measurement of the same underlying phenomenon |
| Range/channel break after compression | Same Donchian-style check as strategy #1 | Full | Reuse |
| Displacement strength, close acceptance | `m15_snapshot.displacements`, wick helper | Full | Nothing new |
| HTF directional context | `ctx.htf_trend_h1/h4` | Full | Nothing new |
| False-break/re-entry | `_recent_structure_break_against`-style pattern | Partial | Small composition |

**Highest rejection risk of the four.** The core hypothesis targets the same underlying phenomenon the already-rejected squeeze indicator measured, via a different statistic (percentile rank vs. boolean band-inside-band). Must be built and validated independently, then explicitly compared against the existing negative finding per the original instruction — real possibility of ending REJECT rather than forcing a 4th strategy through.

---

## Sequencing

Per explicit user decision: one strategy at a time, same rigor as the mean_reversion/trend_pullback work (design → implement → validate → verdict, checkpointed between each).

**Status:**
- **`donchian_trend_follow`**: design complete (`docs/donchian-trend-follow-design-spec.md`), **implemented and deployed at commit `9f6516d`** — registered `DISABLED` (zero live footprint, same pattern as `wyckoff`), 470 regression tests pass, verified via live + historical smoke tests. Its 3-year point-in-time-safe validation (2020-01-01 → 2023-01-01, `FOREXSB` provider) is written and smoke-tested but **paused**, pending CPU headroom — see `project_new_strategies_initiative` memory for the resume procedure and CPU-priority rule. Verdict not yet available.
- Strategies #2-4 (`session_liquidity_breakout`, `fx_relative_momentum`, `volatility_compression_expansion`) not yet started — each follows the same design → implement → validate → verdict cycle only after `donchian_trend_follow`'s verdict is reported.
