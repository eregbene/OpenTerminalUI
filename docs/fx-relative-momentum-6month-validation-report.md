# fx_relative_momentum — 6-Month Fast Validation Report & Verdict

**Period:** last 6 months · **Symbols:** 8 tradeable FX pairs (EURUSD, GBPUSD, USDJPY, AUDUSD, NZDUSD, USDCHF, EURJPY, GBPJPY — USDCAD excluded as a candidate since CAD isn't in the ranked 7-currency set, XAUUSD not applicable) · **Provider:** MT5 · **Method:** point-in-time-safe, H4-bar-granularity walk (~1,092 instants, ~47 minutes total, single lightweight process, ~1 CPU core — deliberately fast per explicit instruction). Gross R only (no cost model). Single pooled window, no walk-forward folds.

**16,860 candidates** across 3 horizons (SHORT/MEDIUM/LONG), 5,626-5,628 per horizon.

---

## Headline: pooled expectancy is essentially zero, gross

| Horizon | N | Expectancy R | PF | Win rate |
|---|---|---|---|---|
| SHORT (M15/20bar) | 5,614 | −0.028 | 0.96 | 36.4% |
| MEDIUM (H1/24bar) | 5,614 | +0.001 | 1.00 | 37.5% |
| **LONG (H4/30bar, intended default)** | 5,626 | **+0.028** | **1.05** | 38.6% |
| **All 3 pooled** | 16,854 | **+0.0006** | **1.001** | 37.5% |

Even the best horizon (LONG) is only +0.028R gross — far too thin to be confident it survives real spread/commission costs, which routinely run 0.05-0.15R per trade on retail FX. This alone falls short of "positive cost-aware OOS expectancy."

## Most important finding, as explicitly requested: larger strength separation does NOT produce better outcomes

| |spread| quintile (LONG horizon) | N | Expectancy R | Win rate |
|---|---|---|---|
| Q1 (0.50–1.67 ATR) | 1,125 | +0.002 | 37.5% |
| Q2 (1.67–3.31 ATR) | 1,125 | **+0.098** (best) | 41.2% |
| Q3 (3.31–5.22 ATR) | 1,125 | +0.041 | 39.0% |
| Q4 (5.22–8.66 ATR) | 1,125 | +0.029 | 38.6% |
| Q5 (8.67–146 ATR, most extreme) | 1,126 | **−0.027** (worst) | 36.5% |

**Not monotonic — it's inverted-U shaped, peaking at moderate separation and declining toward the most extreme.** Confirmed a second way: candidates where the pair is literally the strongest-vs-weakest currency (base rank ≤2 AND quote rank ≥6, or the reverse) perform *worse* (−0.046R, PF 0.93, n=1,451) than candidates with a more moderate rank gap (+0.054R, PF 1.09, n=4,175). **Reporting this clearly rather than optimizing around it, per your explicit instruction**: "buy strongest / sell weakest" as the naive extremity play does not hold up in this data — if anything the opposite is mildly true.

## Second major finding: a stark, unexplained LONG/SHORT asymmetry

| Direction (LONG horizon) | N | Expectancy R | PF | Win rate |
|---|---|---|---|---|
| SHORT | 2,397 | **+0.233** | **1.43** | 46.2% |
| LONG | 3,229 | **−0.123** | **0.82** | 32.9% |

This is a real, substantial split in the same dataset — SHORT candidates are solidly profitable, LONG candidates are solidly unprofitable. Genuinely interesting, but per the same discipline applied to every other strategy this session (donchian's LONG-only finding, mean_reversion's regime inversion): **not acted on** — this is one pooled window, and carving out "SHORT only" from the same sample being evaluated is exactly what you told me not to do without an independent chronological check.

## Supporting evidence points the wrong way (same pattern as donchian)

| | N | Expectancy R |
|---|---|---|
| `momentum_persistence=True` | 2,730 | −0.034 |
| `momentum_persistence=False` | 2,896 | +0.087 |
| `htf_alignment=True` | 1,846 | +0.005 |
| `htf_alignment=False` | 3,780 | +0.040 |

Both supporting-evidence assumptions (persistence should help, HTF alignment should help) show flat-to-inverted relationships, not the intended positive one.

## Chronological instability

| Month | N | Expectancy R | PF |
|---|---|---|---|
| 2026-02 | 186 | **−0.283** | 0.61 |
| 2026-03 | 849 | +0.024 | 1.04 |
| 2026-04 | 831 | +0.104 | 1.18 |
| 2026-05 | 870 | +0.073 | 1.12 |
| 2026-06 | 844 | +0.213 | 1.39 |
| 2026-07 | 1,425 | **−0.092** | 0.86 |
| 2026-08 | 621 | −0.009 | 0.99 |

Two of seven months net negative, including one clearly bad month (Feb), immediately followed by four decent months, then a reversion back to negative in July. Not the stable, monotone pattern needed to trust a pooled positive average.

## Symbol spread and frequency

| Symbol | N (LONG) | Expectancy R | Candidates/day |
|---|---|---|---|
| EURUSD | 965 | +0.116 | 5.31 |
| EURJPY | 257 | +0.090 | 1.41 |
| GBPUSD | 993 | +0.042 | 5.46 |
| AUDUSD | 960 | +0.022 | 5.28 |
| USDCHF | 965 | +0.014 | 5.30 |
| USDJPY | 981 | +0.014 | 5.39 |
| **GBPJPY** | 236 | **−0.107** | 1.30 |
| **NZDUSD** | 269 | **−0.147** | 1.48 |

**Frequency goal genuinely achieved**: ~31 candidates/day combined across 8 symbols at the LONG horizon alone, before any downstream confidence filtering — a real, structural improvement over the near-zero frequency of the SMC-heavy strategies. But 2 of 8 symbols are clearly negative, and the positive symbols are all fairly thin (+0.014 to +0.116R gross).

## Distinctness from existing `momentum` (confirmed, not just asserted)

`momentum` (`families/momentum.py`) is time-series momentum — a single pair's own RSI/MACD alignment against its own price history, hard-disabled by a code-level circuit breaker (`MT5_MOMENTUM_STRATEGY_ENABLED=False`) after a 0/10-symbols-positive, 0/9-years-positive audit record. `fx_relative_momentum` is cross-sectional — a currency's return *relative to a 7-currency basket*, never referencing a pair's own past price in isolation. Different mathematical construction, different data (multi-pair basket vs single pair), different failure mode. Not a resurrection of the deprecated strategy — genuinely distinct candidates by construction, no overlap check needed beyond this structural argument since `momentum` produces zero live candidates today (hard-disabled).

---

## Verdict: **INSUFFICIENT — not promoted to DEMO, stays DISABLED**

Checking against your explicit bar (positive cost-aware OOS expectancy, PF>1, acceptable DD, cross-symbol/chronological stability, materially greater frequency):

- **Frequency**: ✅ clearly met (~31/day vs near-zero for existing ACTIVE strategies).
- **Positive cost-aware expectancy**: ❌ — gross expectancy at the best horizon is only +0.028R; no cost model applied, and this margin is too thin to trust surviving real spread/commission.
- **PF > 1**: marginal at best (1.05 for LONG horizon alone, 1.001 pooled — not a real edge).
- **Acceptable, stable drawdown**: not clearly established — the monthly and symbol breakdowns show real instability (2 of 7 months negative including one bad month, 2 of 8 symbols clearly negative).
- **Cross-symbol/chronological stability**: ❌ — same instability as above.
- **The core hypothesis test you specifically asked for** (does larger strength separation improve outcomes) came back **inverted**, not just unconfirmed.

4 of 5 explicit criteria are not clearly met. Per your own repeated instruction not to force a strategy through or manufacture trades by weakening the bar, this stays `DISABLED`. This is a genuine, reported **failure of the current design as specified** — not a data or implementation problem (473 tests pass, live smoke test showed mathematically correct, consistent signals).

**What's worth carrying into a future iteration** (not acted on now, recorded for later): the SHORT-side asymmetry (+0.233R/PF 1.43 vs LONG's −0.123R/PF 0.82) and the moderate-spread-outperforms-extreme-spread pattern are both real, substantial, and specific enough to be worth a properly-designed, independently-validated follow-up — but neither should be carved out from this same sample without a chronological OOS check first.

**No DEMO activation. No code/config changes as a result of this report. No cost/adaptive/HI/risk changes.**
