# External Indicator Redundancy Audit: LazyBear / jdehorty / LuxAlgo vs. Bensim

**Date:** 2026-08-17
**Scope:** Compares four named open-source TradingView concepts against Bensim's existing MT5 live-trading indicator/feature/strategy set, to determine what (if anything) is genuinely new information worth backtesting as a Historical Intelligence fingerprint feature. **No code has been written yet.** This is the audit + comparison phase; implementation is a separate, gated next step per pair below.

---

## 1. What Bensim already has (confirmed via full codebase audit)

### Indicators/features
| Category | What exists | Where |
|---|---|---|
| Moving averages | SMA, EMA | `backend/core/technicals.py` |
| Bollinger Bands | SMA(20)±2σ | `core/technicals.py` — **not used by any live MT5 strategy** |
| RSI | Simple (non-Wilder) 14-period | `core/technicals.py`, used by `momentum`/`mean_reversion` |
| MACD | 12/26/9 | `core/technicals.py`, used by `momentum` |
| ATR | **Three independent implementations** (core/technicals simple, market_structure simple, scanner_engine Wilder) — only `market_structure`'s feeds live `ctx.atr_m15` | see file list |
| ATR regime bucket | Percentile rank of ATR → LOW/NORMAL/HIGH/EXTREME | `historical_intelligence/fingerprint.py` |
| VWAP | True cumulative session VWAP, deviation + volume-confirmation entry | `mt5_strategies/families.py` (`vwap_reversion`) |
| BOS/CHoCH/MSS | 3-way structural break classification (more granular than most public SMC indicators' 2-way BOS/CHoCH) | `market_structure/structure.py` |
| Displacement | Body/range vs ATR threshold | `market_structure/displacement.py` |
| Liquidity sweep | Swing level breach + reclaim | `market_structure/liquidity.py` |
| FVG (imbalance) | Classic 3-candle wick gap, ATR-filtered, mitigation-tracked | `market_structure/imbalance.py` |
| Order blocks | Backward search to last opposite candle before a break | `market_structure/zones.py` |
| Premium/discount + OTE | Dealing-range midpoint + Fib 0.62–0.79 zone | `market_structure/dealing_range.py` |
| Session levels | Per-session high/low/open, prev-day levels | `market_structure/sessions.py` |
| Regime classification | Rule-cascade (event/high-vol/low-vol/trending/breakout/reversal/unstable/ranging) | `adaptive_management/service.py` |
| Squeeze/breakout | BB-width percentile + KC breakout | `scanner_engine/` — **separate module, not wired into MT5 live trading at all** |
| Historical Analog similarity | Weighted-overlap kNN over ~17 categorical/bucketed dimensions, hard-filtered on 5-6 exact-match dims | `historical_intelligence/similarity.py` |

**Confirmed absent anywhere in the repo:** ADX, CCI, "Wave Trend" oscillator, any Lorentzian/Euclidean/Manhattan distance-metric classifier, any kNN used for live candidate scoring (Historical Analog is advisory-only, never a live signal generator).

### The 11 strategies (confirmed complete)
mtfai1 (triple-timeframe SMA alignment), ema_trend, trend_pullback, breakout, mean_reversion, liquidity_sweep_reversal, smc_continuation, support_resistance_bounce, momentum, session_breakout, vwap_reversion. Full entry-logic descriptions in the underlying audit; every strategy shares one stop-construction path and a minimum 1.5 RR floor.

---

## 2. Licensing reality check (important — this shapes *how* anything gets built, not just *whether*)

| Source | License found | Implication |
|---|---|---|
| LazyBear Squeeze Momentum | No formal license; "open-source, subject to TradingView House Rules." 10+ years old, freely republished hundreds of times. | Safe to independently reimplement the well-documented public methodology (derivative of John Carter's published TTM Squeeze). |
| jdehorty Lorentzian Classification | **Mozilla Public License 2.0** (confirmed via his GitHub ports to Python/Rust/Lean4) | Genuinely permissive for commercial use even if code were referenced directly — but we're independently implementing from the described methodology regardless, not porting his files. |
| LuxAlgo Smart Money Concepts | **CC BY-NC-SA 4.0 — non-commercial** | Directly porting this code would be a license violation for any commercial use. Independent reimplementation from the public concept description is the only safe path — moot anyway, see §3.3. |
| LuxAlgo kNN Market Architecture | No CC license found; described as "Free indicator, built in-house by LuxAlgo" — appears to be a **protected script** (Pine source not publicly viewable). | Can only work from the published methodology description (confirmed: relative-ATR + relative-volume kNN signature matching, confidence threshold, ST/MT/LT layers), not actual source. This is a real constraint on how literally "inspect the actual open-source Pine logic" can be honored for this one. |

**Bottom line:** independent reimplementation from the documented public methodology (never copying source) is the correct approach for all four, and is legally required for at least one of them (LuxAlgo SMC).

---

## 3. Per-concept redundancy assessment

### 3.1 LazyBear Squeeze Momentum — **not redundant, worth testing**
- Formula (public, well-documented): squeeze ON when Bollinger Bands (20, 2σ) sit fully inside Keltner Channels (20-period EMA ± 1.5×ATR); momentum = `linreg(close - avg(avg(highest_high, lowest_low, N), sma(close, N)), N, 0)`.
- Bensim's existing squeeze detector (`scanner_engine`) uses a *different* proxy (BB-width percentile rank, no direct BB-vs-KC comparison) and computes no momentum histogram — and critically, **it isn't wired into MT5 live trading at all**, only an unrelated screener.
- Bensim's ATR-regime bucket captures volatility *level*, not specifically "bands compressed relative to each other then expanding" — a narrower, different condition.
- **Verdict:** genuinely new signal type. Worth implementing as two new fingerprint dimensions (squeeze_state: ON/RELEASING/OFF, momentum_direction+magnitude bucket) and walk-forward testing per the plan in §4.

### 3.2 jdehorty Lorentzian Classification — **not redundant, worth testing (and is the natural kNN comparison point)**
- Feature set (RSI, Wave Trend, CCI, ADX) is mostly **absent from Bensim entirely** — no ADX, no CCI, no Wave Trend anywhere; RSI exists but computed differently (simple vs. this indicator's own smoothing).
- Distance formula: `d(x,y) = Σ ln(1 + |xᵢ - yᵢ|)` (Lorentzian), k=8 default, approximate nearest-neighbor search over continuous oscillator values.
- **This is architecturally different from Bensim's Historical Analog engine**, not a duplicate: Historical Analog does weighted-overlap matching over *categorical/bucketed structural* dimensions (regime, session, BOS/CHoCH presence, ATR bucket, etc.) with hard filters; Lorentzian Classification does distance-based matching over *continuous oscillator* values with no categorical filtering at all. Same broad technique family (kNN-style retrieval), different feature space, different question being answered (next-bar direction vs. whole-setup outcome distribution).
- **Verdict:** worth implementing as a new fingerprint feature (oscillator-state similarity score) and testing both (a) its own incremental value, and (b) a direct head-to-head against Historical Analog on the same prediction task — this is the comparison you explicitly asked for, and I have not run it yet (would require real implementation + identical walk-forward evaluation; any claim about which "performs better" without that data would be a guess, so I'm not making one).

### 3.3 LuxAlgo Smart Money Concepts — **largely redundant, do not reimplement broadly**
- Bensim's own SMC/ICT engine (`market_structure/*`) already covers BOS/CHoCH (Bensim: 3-way BOS/CHoCH/MSS, *more* granular), order blocks, FVG, premium/discount+equilibrium (Bensim additionally computes the OTE Fib zone, which LuxAlgo's public description doesn't mention), and liquidity sweeps — independently built, not derived from LuxAlgo.
- **One possible genuine gap:** LuxAlgo's Equal-Highs/Equal-Lows (EQH/EQL) detection — repeated similar-level touches, used for liquidity-pool identification — is conceptually distinct from Bensim's single-swing-pivot detection (`swings.py`). I did not find an explicit EQH/EQL-equivalent in the audit, but I also did not exhaustively verify its absence.
- **Verdict:** do not reimplement LuxAlgo SMC broadly — this would be redundant work reproducing what Bensim already has, arguably at lower granularity. The only piece worth a closer, cheap look is whether EQH/EQL specifically is missing and would add value; everything else is a clear no.

### 3.4 LuxAlgo kNN Market Architecture — **not redundant, worth testing, with a licensing/access caveat**
- Two distinct capabilities: (a) validating swing pivots via kNN similarity to historical pivot "signatures" (relative ATR + relative volume) rather than pure rule-based detection, and (b) cumulative volume-delta ("Delta Tank") + anchored volume-profile zones.
- Bensim's swings are **purely rule-based** (fractal pattern + ATR-scaled thresholds) — never confidence-scored against historical pivot outcomes. Volume-delta/volume-profile logic is **entirely absent** from the live MT5 pipeline (only raw volume-vs-rolling-average checks exist, in `vwap_reversion`).
- Caveat from §2: actual Pine source isn't public for this one — implementation must work from the published methodology only.
- **Verdict:** worth testing both sub-capabilities as separate fingerprint features (pivot-confidence score, volume-delta state) — genuinely new information, not overlapping with Historical Analog (which operates on whole trade setups, not individual pivot validation) or existing SMC swing detection.

---

## 4. Proposed validation plan (not yet executed — for your sign-off)

For each of the three "worth testing" concepts (LazyBear squeeze, Lorentzian oscillator-kNN, LuxAlgo pivot/volume-delta kNN), the plan mirrors exactly what was just done for the tiered-ESS validation:

1. Independently implement the feature computation (no copied source) as a pure function, added as new `StrategyContext`/fingerprint dimensions — **not** a new strategy, not wired into live trading or SUPPORT/OPPOSE decisions yet.
2. Backfill the feature retroactively over the existing EURUSD/GBPUSD (and USDJPY where relevant) fingerprint corpus.
3. Walk-forward test (chronological train/OOS split, purge window, same methodology as `walk_forward.py`): does the new feature carry incremental expectancy/calibration value on top of existing ATR/EMA/VWAP/SMC dimensions? Is it redundant (correlated with an existing dimension) or additive?
4. For the Lorentzian comparison specifically: run it and Historical Analog on the *same* prediction task (probability of reaching +1R) over the *same* candidates, OOS, and report which calibrates better — genuinely, from real numbers, not from methodology alone.
5. Only if a feature shows robust, OOS-stable incremental value does it get promoted to influencing SUPPORT/OPPOSE (and even then, through the existing conservative gating architecture — ESS thresholds, trust gating — not a shortcut around it). Otherwise it's retained as an observability-only Historical Intelligence dimension, exactly as you specified.

This is a genuinely substantial engineering effort (three independent feature implementations + retroactive fingerprint backfill + walk-forward validation each) — realistically comparable in scope to the tiered-ESS validation work, per concept. I'd like to confirm before starting: build these one at a time (start with whichever you consider highest-priority), or in parallel? And should LuxAlgo SMC's one open question (EQH/EQL) get a quick look now, or is the "largely redundant, skip" verdict good enough to close it out?
