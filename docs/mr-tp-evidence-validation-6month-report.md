# mean_reversion / trend_pullback Evidence Validation — 6-Month Report

**Period:** last 6 months (≈2026-02-24 to 2026-08-24) · **Symbols:** EURUSD, GBPUSD, USDJPY, AUDUSD, XAUUSD · **Provider:** MT5 · **Method:** point-in-time-safe (`bars_as_of`), real production evaluator functions, no persistence to production tables (counterfactual candidates). Gross R only (no cost model applied). Single window, no walk-forward split yet — treat findings below as directional evidence, not confirmed conclusions.

**51,733 total candidates evaluated**: 38,967 trend_pullback, 9,679 mean_reversion Path A, 3,087 mean_reversion Path B.

---

## trend_pullback: regime gate is doing real work — but is one regime too narrow?

| | N | Expectancy R | PF | Win rate | Immediate-failure | +0.5R / +1R / +2R reach |
|---|---|---|---|---|---|---|
| **Current production** (trending_up/trending_down only) | 2,575 | **+0.236** | 1.39 | 40.1% | 16.4% | 76% / 66% / 41% |
| **Shadow-widened** (every other regime) | 36,377 | **−0.067** | 0.90 | 30.3% | 28.1% | 62% / 48% / 34% |

**Headline finding: the hard regime gate is not arbitrary — it's selecting a genuinely better population.** The currently-gated set is solidly profitable; the widened set as a whole is not. This is real evidence *against* simply removing the gate as proposed in the design spec.

**But the regime breakdown of the widened set reveals a specific exception worth flagging:**

| Regime (widened, currently blocked) | N | Expectancy R | PF | Win rate |
|---|---|---|---|---|
| `breakout` | 3,485 | **+0.324** | **1.57** | 43.0% |
| `unstable_transition` | 28 | +0.211 | 1.35 | 39.3% (n too small to trust) |
| `ranging` | 13,696 | −0.045 | 0.94 | 31.0% |
| `reversal` | 16,836 | −0.141 | 0.80 | 27.9% |
| `low_volatility` | 1,830 | −0.230 | 0.69 | 25.0% |
| `high_volatility` | 502 | −0.308 | 0.60 | 22.1% |

`breakout` regime outperforms even the current production set (+0.324R vs +0.236R, PF 1.57 vs 1.39), on real volume (3,485 candidates). This is a specific, scoped candidate for widening the regime gate to `{trending_up, trending_down, breakout}` — not a case for removing it. **Not acted on** — this is one 6-month window with no walk-forward split; before touching the gate this needs the same OOS/walk-forward discipline as everything else, and ideally the 3-year window too.

**By symbol** (widened set): only GBPUSD is net positive (+0.151R, PF 1.24). EURUSD (−0.107), USDJPY (−0.188), AUDUSD (−0.052), XAUUSD (−0.246) are all negative — real symbol heterogeneity, not a uniform effect.

**Do the new evidence fields discriminate outcomes?** No clean signal found:
- `location_quality_score` buckets: 0→+0.010, 12→−0.151, 18→+0.074, 22→−0.176, 24→−0.547 — **non-monotonic**, no usable pattern.
- `wick_rejection_score` quartiles: Q1→−0.006, Q2→−0.086, Q3→+0.002, Q4→−0.177 — **also non-monotonic**; if anything the strongest-rejection quartile performs worst, the opposite of the intuitive direction.

**This is a real, honest negative finding for this round**: on this single 6-month sample, neither new field shows the kind of clean, stable relationship that would justify moving them from observability-only into scoring. Consistent with the codebase's own standing bar (the same one `_eqh_eql_touch_count` and `displacement_magnitude_atr` were held to) — **no change to Step 5 planning**; these fields stay observability-only pending more data / a proper walk-forward check, not promoted or rejected outright on one window.

---

## mean_reversion: a surprising regime-gate inversion, and a clear negative for Path B

| | N | Expectancy R | PF | Win rate | Immediate-failure |
|---|---|---|---|---|---|
| **Path A, current production** (ranging/low_volatility) | 3,113 | **−0.040** | 0.94 | 38.5% | 32.4% |
| **Path A, other regimes** (RSI extreme, currently blocked) | 6,566 | **+0.227** | **1.45** | 49.1% | 16.6% |
| **Path B** (ATR/EMA stretch, no RSI requirement) | 3,087 | **−0.218** | 0.68 | 31.3% | 32.4% |
| Path B only (candidates Path A would miss) | 1,958 | −0.259 | 0.63 | 29.6% | 33.2% |

**Two things worth flagging, neither acted on:**

1. **The regime gate looks inverted in this window** — RSI-extreme candidates *outside* ranging/low_volatility (currently blocked) outperform the ones *inside* it (currently the only ones production trades) by a wide margin (+0.227R vs −0.040R). This is the opposite of what the gate is supposed to do. Important caveats before reading too much into it: single 6-month window, no walk-forward split, and this cuts against the strategy's own 8-year audit track record (+0.273R pooled, its best performer) — a recent rough patch for the gated set is at least as plausible an explanation as the gate itself being wrong. Flagging for the walk-forward follow-up, not proposing any change now.
2. **Path B shows a clear, real negative result.** The proposed non-RSI stretch trigger underperforms the existing RSI trigger substantially (−0.218R vs −0.040R for the comparable current-production population), and the candidates it would add on top of Path A are the worst-performing subset of all (−0.259R). **This is real evidence against implementing Path B as designed** — not proof it can never work, but the specific stretch/location/confirmation formulation tested here does not show a standalone edge. Path B stays unimplemented; this finding should gate any future attempt at it.

**Evidence discrimination for mean_reversion:** `location_quality_score` is non-monotonic here too (0→−0.130, 12→+0.129, 18→−0.232, 22→+0.129). One weak directional hint: "naked" candidates with zero location evidence do worse than "supported" ones (−0.130R vs −0.034R) — but both are still net-negative, small naked sample (n=184), and this is far short of a clean, reliable signal.

---

## Bottom line

- Nothing here changes production behavior — this was analysis only, matching the existing architecture-freeze/forward-evidence discipline.
- The new evidence fields (wick rejection, location quality) do **not yet** show stable discriminative value — correctly stay observability-only for now, not promoted to scoring.
- **Path B for mean_reversion is not supported by this data** — a real, disclosed negative finding, not just "insufficient evidence."
- **trend_pullback's regime gate looks substantially correct, with one specific, scoped exception** (`breakout` regime) worth a proper walk-forward check before any gate change is considered — this is a materially different, more nuanced conclusion than either "keep the gate exactly as-is" or "remove it."
- mean_reversion's regime-gate inversion is the most surprising single finding here and deserves follow-up against a longer/walk-forward window before drawing any conclusion — flagged, not acted on.

`donchian_trend_follow`'s own 6-month validation is running now; its verdict follows separately.
