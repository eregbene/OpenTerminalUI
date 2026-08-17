# Wyckoff Strategy — Historical Validation Report

**Date:** 2026-08-17
**Status: REJECT for DEMO promotion at this time.** `wyckoff` stays registered but `default_activation=DISABLED` — zero live footprint, no code deleted, fully reversible.

---

## 1. How Wyckoff was implemented

**Engine** — `backend/market_structure/wyckoff.py`, a new pure, point-in-time-safe module following the same pattern as `dealing_range.py`/`liquidity.py`. It is **not** wired into `engine.py::analyze_bars()` (zero blast radius on every other strategy/replay/schema consumer); callers invoke `analyze_wyckoff(rows, snapshot, ...)` directly.

It detects, by composing already-existing primitives (no duplicated pivot/break/sweep logic):

| Concept | How it's derived |
|---|---|
| Selling/Buying Climax (SC/BC) | Trailing-percentile-rank climax bar (tick-volume + true-range both ≥85th percentile) after a genuine prior ATR-scaled directional move, closing back into its own range |
| Preliminary Support/Supply (PS/PSY) | Optional — an earlier, still-elevated-volume same-type swing shortly before the climax; **never fabricated when absent** |
| Automatic Rally/Reaction (AR) | The next opposite-type confirmed swing after the climax — defines the range's second boundary |
| Secondary Test (ST) | Same-type swings after AR that revisit the climax extreme without breaking it |
| Spring / Upthrust (UTAD) | **Reuses `liquidity.py`'s existing sweep detector directly** — a Spring is structurally identical to a sell-side liquidity sweep of the range low that reclaims; UTAD is the buy-side mirror at the range high. No second sweep detector was written. |
| Sign of Strength/Weakness (SOS/SOW) | **Reuses the existing BOS/CHoCH/MSS structure-break detector directly**, filtered to the schematic's direction after a successful Spring/UTAD test |
| Last Point of Support/Supply (LPS/LPSY) | The pullback swing after SOS/SOW that holds above/below the broken level |
| Range boundaries / position | `(last_close - range_low) / (range_high - range_low)`, deliberately **not clipped** to [0,1] since a Spring/UTAD excursion outside that band is the whole point |
| False breakout / liquidity-sweep behavior | Same Spring/UTAD sweep-reuse above; a failed test (breaks past the spring/UTAD extreme instead of holding) sets `invalidated=True` |
| Effort-vs-result | Per-bar tick-volume-percentile vs true-range-percentile classification (`high_effort_low_result`, `high_effort_high_result`, etc.), attached to every event's evidence — observability, not a separate trigger |
| Phase A–E | A ladder that only advances when the corresponding evidence exists in the current ~100-bar window — never skips ahead |

**Two documented, deliberate scope limitations** (not fabricated data):
- **Volume**: Bensim's only per-bar volume-like signal for MT5 forex is `tick_volume` — a count of price ticks, not real executed/traded size. `real_volume` is unpopulated for retail FX on this deployment. Every "volume"/"effort" reference in the module says so.
- **Lookback window**: live/replay strategy evaluation only ever supplies ~100 M15 bars (~25 hours), matching every other strategy family (`STRATEGY_LOOKBACK`). What this engine detects are **short-duration (intraday/multi-hour) accumulation/distribution schematics**, not the multi-week institutional campaigns classical Wyckoff literature illustrates on daily charts.

**Setups** — `evaluate_wyckoff` in `backend/mt5_strategies/families.py`, using the exact same shared `_dynamic_stop`/`_signal`/`_geometry_metadata` pipeline (ATR/spread/broker-floor-bounded stop, `_MIN_REWARD_MULTIPLE=1.5` RR floor, long/short geometry validation) every other strategy goes through — no bypass of portfolio/spread/economic/correlation/broker-stop checks anywhere.

- **`spring_sos_lps`**: Spring/UTAD → successful test (phase reached C+) → SOS/SOW confirmed (phase D/E) → entered at the LPS/LPSY pullback, or a live pullback holding the just-broken level within 1 ATR if the swing hasn't confirmed yet.
- **`phase_d_continuation`**: SOS/SOW confirmed but price isn't at a specific pullback — a plain continuation entry, deliberately tagged and tested **separately** per the explicit instruction not to assume it shares the other setup's edge.

Registered in `STRATEGY_FAMILIES`/`EVALUATORS`/`STRATEGY_LOOKBACK`/`_STRATEGY_SHORT_CODES` so the canonical replay pipeline reaches it exactly like every other strategy — but `default_activation=DISABLED`, so the live DEMO backend never evaluates or shadow-tracks it. Historical validation reached the real evaluator via a **process-local** `MT5_STRATEGY_ACTIVATION_WYCKOFF=SHADOW_MT5` env var, never deployed to the live container.

12 unit tests (synthetic accumulation + distribution schematics, insufficient-data, no-climax, spring-invalidation, `evaluate_wyckoff` integration) all pass; full `market_structure`/`mt5_strategies`/replay regression suite is clean.

---

## 2. Historical validation — method

- **Pipeline**: `backend.historical_intelligence.bulk_replay.replay_symbol_history_fast`, the exact same canonical, point-in-time-safe replay every other strategy is validated through. No reimplementation.
- **Window**: 2026-02-01 → 2026-08-01 (6 months, most recent regime). The full available corpus per symbol is ~8 years; a pilot run measured real fast-path throughput at ~0.5s/instant, which sizes a full 8-year/10-symbol run at multiple days — out of scope for this pass. This is an explicit, deliberate first validation, not the full corpus.
- **Symbols**: EURUSD, GBPUSD, USDJPY, XAUUSD, GBPJPY.
- **Sample**: 3,062 resolved Wyckoff occurrences.
- **Lookahead check**: `analyze_wyckoff` only ever reads bars up to the current replay instant (`bars_as_of` never returns a bar closing after `at`); all percentile ranks are trailing-window-only; AR/ST/Spring/SOS searches are bounded to indices within the currently-available window. Architecturally, no future information can enter a label. No lookahead was found.

### Data-quality issues found (reported honestly, not glossed over)

1. **GBPJPY and XAUUSD have a real corpus gap**: only ~111 finalized H4 candles exist for this window vs ~800 for EURUSD/GBPUSD/USDJPY (verified directly against `mt5_canonical_candles`). This caused 82-83% of instants for those two symbols to skip evaluation entirely (`insufficient_history`), so their effective sample is a much smaller, less representative slice of the intended window (occurrences cluster in June–July only, not across the full 6 months) than the other three symbols. This is a pre-existing historical-intelligence corpus gap, unrelated to Wyckoff.
2. **`MT5 UTC offset detection failed, defaulting to 0` warnings** appeared during replay for GBPUSD/USDJPY — a pre-existing platform replay warning, not introduced by this work. Likely a minor influence on session/regime labeling only, not on entry/stop/target/R math.
3. **13% of occurrences (396/3,062) couldn't be re-labeled with a setup/phase** during the enrichment pass (which re-derives `evaluate_wyckoff`'s evidence a second time, since the persisted fingerprint schema doesn't carry per-strategy evidence). Concentrated entirely in USDJPY (210) and GBPUSD (186) — the two symbols under continuous background corpus-worker revision — most likely explained by candle-revision drift between the original backfill pass and the later re-enrichment pass, not a detection bug. These rows' own R-outcomes are real (computed once, at backfill time); only their setup/phase breakdown label is unresolved. Their aggregate stats are close to flat (expectancy ≈ 0), consistent with an unlabeled mix of both setups rather than a hidden third population.

---

## 3. Results — headline number is misleading; here's why

**Pooled (n=3,062): expectancy +0.73R, PF 2.0, win rate 26.8%, reach 2R 39.2%.** On its own this looks promotable. It is not — the pooled number is dominated by one symbol and one methodological artifact.

### By symbol — the edge is not consistent

| Symbol | n | Expectancy R | PF | Win rate |
|---|---|---|---|---|
| USDJPY | 1,036 | **+3.03** | **7.27** | 51.7% |
| XAUUSD | 155 | +0.23 | 1.34 | 32.3% |
| GBPJPY | 124 | −0.35 | 0.57 | 18.6% |
| EURUSD | 842 | **−0.50** | 0.43 | 12.2% |
| GBPUSD | 905 | **−0.51** | 0.42 | 11.9% |

3 of 5 symbols lose money. The pooled positive result is almost entirely a USDJPY effect.

### By setup type — the two setups behave oppositely

| Setup | n | Expectancy R | PF | Reach 2R |
|---|---|---|---|---|
| `spring_sos_lps` | 546 | +4.64 | 9.52 | 57.9% |
| `phase_d_continuation` | 2,120 (69% of all) | **−0.14** | 0.82 | 35.1% |

**`phase_d_continuation` does not hold up** — a net loser overall, and losing on 3 of 5 symbols even independent of the target-formula issue below (EURUSD PF 0.36, GBPUSD PF 0.30, GBPJPY PF 0.61; only USDJPY PF 2.62 and XAUUSD PF 1.84 are positive). This directly answers the "does Phase-D continuation provide a separate valid setup" question: **no**.

### `spring_sos_lps`'s apparent edge is an artifact, not real — proven three ways

1. **R-multiple distribution is implausible for real trading**: of 249 winning trades, the winner distribution is `min=1.77, p25=13.55, median=13.55, p75=13.55, max=14.28` — 78.7% of winners land at R≥10, contributing 93.9% of all winning R. Real trade outcomes do not cluster this tightly. This is the signature of the setup's **point-and-figure-style target formula** (`target = entry + max(range_span, 2×ATR)`) occasionally producing enormous, unbounded targets — unlike every other strategy's ATR-bounded target — that happened to get touched within the outcome resolver's lookforward window.
2. **Excluding USDJPY, the edge inverts**: `spring_sos_lps` ex-USDJPY = **−0.35R expectancy, PF 0.585** (n=285). The entire positive result lives in one symbol.
3. **USDJPY's own `spring_sos_lps` numbers are implausible on their face**: PF **49.7**, win rate 79.3%, median MFE 13.71R. No real edge looks like this; it is a symbol/period-specific alignment between an unbounded target formula and one large USDJPY directional move within this window.

Chronological train/OOS for `spring_sos_lps` shows the same instability: train50 expectancy +0.20R vs OOS50 **+9.07R** — an OOS result nine times larger than train is not evidence of a stable edge, it's evidence the result is being driven by a specific, concentrated period (which, cross-referenced against the by-symbol breakdown, is USDJPY's move landing in the back half of the window).

### Comparison against existing strategies, same window/symbols

| Strategy | n | Expectancy R | PF |
|---|---|---|---|
| mean_reversion | 1,063 | +0.87 | 4.47 |
| smc_continuation | 702 | +0.53 | 2.09 |
| trend_pullback | 491 | +0.53 | 2.06 |
| **wyckoff** | 3,062 | +0.73 | 2.00 |
| vwap_reversion | 1,906 | −0.05 | 0.93 |
| mtfai1 | 17,291 | −0.11 | 0.84 |
| support_resistance_bounce | 4,012 | −0.28 | 0.63 |
| liquidity_sweep_reversal | 103 | −0.67 | 0.25 |
| session_breakout | 2,578 | −0.67 | 0.26 |
| momentum | 4,327 | −0.72 | 0.20 |
| breakout | 1,755 | −0.64 | 0.26 |
| ema_trend | 1,446 | −0.97 | 0.02 |

Note this comparison is **not fully apples-to-apples**: wyckoff's own number is inflated by the same target-formula artifact described above, so its ranking here is not trustworthy at face value either. What IS informative: most trend/breakout-style existing strategies (ema_trend, breakout, session_breakout, momentum) also did poorly across this same window/symbol set, suggesting Feb–Aug 2026 was broadly unfavorable to trend-continuation-style setups on most of these symbols, with USDJPY as a shared exception — a regime/symbol effect visible across multiple, unrelated strategy families, not something specific to Wyckoff's own logic.

### Overlap with SMC/liquidity-sweep/support-resistance

**93.6% of Wyckoff occurrences (2,865/3,062) do not overlap** with `liquidity_sweep_reversal`/`smc_continuation`/`support_resistance_bounce` firing the same direction at the same instant. Of the 6.4% that do overlap, most overlap with `support_resistance_bounce` (156) and `smc_continuation` (40); almost none with `liquidity_sweep_reversal` (1). **Wyckoff is finding genuinely different opportunities, not relabeling existing SMC/liquidity-sweep/support-resistance candidates.** (The "unique" subset's own stats are actually somewhat better than the "overlapping" subset's — expectancy 0.78 vs 0.10 — for whatever residual signal exists once the target-formula issue is separately fixed.)

---

## 4. Adaptive Manager

Not reached. Per the explicit instruction, Wyckoff trades would use the deployed Adaptive Manager unchanged (current `ADAPTIVE_BREAKEVEN_R=0.5`, disabled `THESIS_INVALIDATION_CLOSE`, unchanged `MFE_PROTECTION_CLOSE`) — but since no setup here clears the promotion bar, there is no live Wyckoff trade population yet to observe management behavior on. Deferred until after re-validation.

---

## 5. Recommendation: **REJECT** promotion to DEMO at this time

Do not enable `ACTIVE_MT5` or `SHADOW_MT5` for `wyckoff`. It remains `DISABLED` (already the deployed state — no change needed).

**Reasoning, matching the explicit "do not promote because the pooled result looks good" instruction:**
- The pooled positive result is real in the database but not a genuine, generalizable edge — it decomposes into one broken setup (`phase_d_continuation`, 69% of volume, a net loser on 3/5 symbols) and one setup whose apparent edge is dominated by an unbounded target-formula artifact concentrated in a single symbol.
- Sample size is adequate (3,062 total) but **not evenly distributed** — 2 of 5 symbols have a real, pre-existing corpus gap that shrank their effective window to ~1.5 months instead of 6.
- OOS/train stability is poor in exactly the direction that should raise suspicion (OOS wildly outperforming train, traced to one symbol's concentrated move), not the kind of stable, boring, cross-period consistency the instruction asks for.

**What would change this verdict**, in priority order:
1. Fix `spring_sos_lps`'s target to the same bounded ATR-multiple approach every other strategy uses (or cap the point-and-figure projection at a sane ATR multiple) — the point-and-figure idea itself is reasonable Wyckoff practice, but it must be bounded like every other strategy's target, not left unbounded.
2. Re-run this exact validation with the fixed target formula.
3. Either close the GBPJPY/XAUUSD H4 corpus gap or accept a 3-symbol (EURUSD/GBPUSD/USDJPY) validation as the honest scope.
4. Extend the window (or add symbols) once the above hold up, before considering `SHADOW_MT5` (live calibration, still non-executing) — which is itself a prerequisite step before ever considering `ACTIVE_MT5`.

Drop `phase_d_continuation` as a standalone setup regardless — it did not earn a place across this validation.

---

## Reproducing this validation

```
python run_wyckoff_backfill.py --symbols EURUSD GBPUSD USDJPY XAUUSD GBPJPY --start 2026-02-01 --end 2026-08-01
python scratch_wyckoff_validation_report.py
```

Both scripts set `MT5_STRATEGY_ACTIVATION_WYCKOFF=SHADOW_MT5` as a process-local env var only — never deployed to the live container.
