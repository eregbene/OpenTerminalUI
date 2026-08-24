# donchian_trend_follow — 6-Month Validation Report & Verdict

**Period:** last 6 months (≈2026-02-24 to 2026-08-24) · **Symbols:** EURUSD, GBPUSD, USDJPY, AUDUSD, XAUUSD · **Provider:** MT5 · **Method:** point-in-time-safe (`bars_as_of`), real production evaluator, no persistence to production tables. Gross R only (no cost model). **Single pooled window — no train/OOS split, no walk-forward folds.** This is disclosed up front because it directly caps what verdict this data can support.

**220 resolved candidates** (223 total, 3 still pending resolution at the window's tail).

---

## Headline number, and why it doesn't survive scrutiny alone

| | N | Expectancy R | PF | Win rate |
|---|---|---|---|---|
| **Overall** | 220 | **+0.182** | 1.30 | 38.6% |

Positive on the surface. But every robustness check the validation plan called for finds real instability underneath it:

### One-symbol domination

| Symbol | N | Expectancy R | PF |
|---|---|---|---|
| AUDUSD | 67 | +0.466 | 1.89 |
| EURUSD | 58 | +0.083 | 1.13 |
| **GBPUSD** | 50 | **−0.495** | **0.40** |
| USDJPY | 36 | +0.305 | 1.52 |
| **XAUUSD** | 9 | +1.974 | **18.77** |

XAUUSD's PF of 18.77 on n=9 is not a real, trustworthy number — it's a handful of trades, individually large-magnitude on gold's wider ATR. Excluding it, pooled expectancy drops from +0.182R to roughly **+0.106R**. GBPUSD, on a real sample (n=50), is a clear net loser (PF 0.40, 18% win rate) — this is not multi-symbol evidence, it's one strong symbol (AUDUSD), two mild positives, one real loser, and one unreliable tiny sample.

### Direction asymmetry

| | N | Expectancy R | PF |
|---|---|---|---|
| LONG | 83 | **+0.512** | **1.99** |
| SHORT | 137 | −0.018 | 0.97 |

The entire edge is concentrated in LONG. SHORT — the majority of candidates — shows no edge at all (PF ≈ breakeven).

### Time instability — the most serious finding

| Month | N | Expectancy R | PF |
|---|---|---|---|
| 2026-02 | 8 | **−1.000** | 0.00 (all losses) |
| 2026-03 | 35 | −0.199 | 0.72 |
| 2026-04 | 34 | **−0.593** | 0.33 |
| 2026-05 | 37 | +0.129 | 1.21 |
| 2026-06 | 31 | +0.177 | 1.31 |
| 2026-07 | 42 | +0.480 | 1.92 |
| **2026-08** | 33 | **+1.357** | **5.97** |

Three of seven months are net losers, including one total wipeout (Feb, 8/8 losses). The pooled positive result is disproportionately carried by the single most recent month, which itself shows a suspiciously high PF (5.97). This is exactly the "pooled result driven by the most recent, luckiest-looking window" pattern that should not be trusted without a genuine out-of-sample holdout — which this run does not have, by construction (it's one continuous pass, not split).

### Supporting evidence points the wrong way

The design's supporting-evidence bonuses (BOS confirmation, EQH/EQL touches, displacement) were meant to indicate *better* setups. In this data, all three go **backwards**:

| | N | Expectancy R |
|---|---|---|
| `bos_supporting=True` | 81 | −0.006 |
| `bos_supporting=False` | 139 | **+0.292** |
| `eqh_eql_touch_count≥2` | 22 | **−0.440** |
| `eqh_eql_touch_count=0` | 198 | +0.251 |
| `displacement_supporting=True` | 196 | +0.164 |
| `displacement_supporting=False` | 24 | +0.330 |

None of the three supporting-evidence assumptions baked into the strength formula show the intended positive relationship. This doesn't necessarily invalidate the core Donchian trigger (strength isn't used to filter candidates in this pass), but it's real evidence the current strength weights don't reflect anything predictive yet — consistent with the same "no stable, monotonic discrimination" caution already applied to mean_reversion/trend_pullback's evidence fields.

### One genuinely clean, useful finding

`breakout_distance_atr` (how far beyond the channel edge the breakout closed) shows a clean, **monotonically decreasing** relationship with outcome:

| Quartile | Distance range | N | Expectancy R | Win rate |
|---|---|---|---|---|
| Q1 | 0.30–0.42×ATR | 55 | +0.395 | 43.6% |
| Q2 | 0.42–0.52×ATR | 55 | +0.409 | 45.5% |
| Q3 | 0.53–0.83×ATR | 55 | +0.167 | 38.2% |
| Q4 | 0.83–2.27×ATR | 55 | **−0.242** | 27.3% |

Breakouts that have already traveled far beyond the channel edge before the candidate fires underperform — plausibly "chasing" a move that's already extended. This is economically sensible (not just noise) and worth carrying into a future iteration (e.g., an upper bound on `breakout_distance_atr`), but it's a single-window observation, not something to act on without its own OOS check.

---

## Verdict: **OBSERVATION ONLY — not promoted to DEMO**

Per the promotion bar you set (positive cost-aware OOS expectancy, PF>1, acceptable DD, sufficient N, walk-forward stability, no catastrophic recent-year degradation, multi-symbol evidence or a *validated* whitelist): this run cannot support a PASS, scoped or otherwise, honestly:

- **No walk-forward/OOS split exists** — this is one pooled window, not chronological folds. The promotion bar explicitly requires walk-forward stability; that hasn't been tested.
- **No cost model applied** — gross R only. Given how many quartiles/subgroups sit close to breakeven, real spread/commission could plausibly flip the net picture on some of them.
- **No drawdown computed** in this pass.
- The positive pooled number depends materially on removing GBPUSD, on the SHORT side, and on the most recent (least-tested) month — and the promotion rules explicitly forbid carving out a symbol/direction whitelist from the *same* sample being used to evaluate it, without an independent chronological check. I have not done that check, so I'm not proposing a whitelist now, even though AUDUSD/LONG look better in this window.

**What would change this**: a genuine walk-forward split (e.g., 3-4 chronological folds within a longer window) where AUDUSD-and/or-LONG-only performance holds up on data it wasn't measured on, plus a cost-adjusted expectancy, plus a computed max DD. Until then, `donchian_trend_follow` stays exactly where it already is — `DISABLED`, zero live footprint, continuing to accumulate its own track record passively if ever run again, per your own "do not force it to pass" instruction.

**No DEMO activation. No code/config changes as a result of this report.**
