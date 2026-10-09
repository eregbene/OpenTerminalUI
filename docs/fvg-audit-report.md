# Bensim FVG Research Audit

Generated 2026-10-08. Research only: nothing here touches live trading.

- **Data:** FOREXSB M15 (UTC), 2018-08 to 2026-08, for EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, USDCAD and XAUUSD.
- **Sample:** 370,269 FVGs on M15/H1/H4/D1, with no filters.
- **Code:** `bensim_research/fvg_audit.py`, `fvg_audit_report.py` and `test_fvg_audit.py`.
- **Full tables:** `data/research/artifacts/bensim/fvg_audit/fvg_audit_tables.md`, with JSON in the same folder.

## How it is measured

**Question.** When price revisits an FVG, does it then move in the FVG's direction more often than it would from a random point? Whether the gap stays unfilled is not the test.

**Unit.** R = 1 ATR of the FVG's timeframe at candle 1, so no strategy stop or target is assumed. "+kR first" means price reached +k ATR before −1 ATR, within 50 candles of the FVG's timeframe.

**Benchmark.** Each FVG has a random-time control:
- Same FVG, same direction, same R, same follow-up window.
- Entry at the open of a random M15 bar.

Over 370k controls the result is 49.6% / 32.7% / 23.8% at 1R/2R/3R. That matches the 50/33/25% a fair coin gives, so the measurement has no built-in bias.

- **edge** = FVG win rate minus control win rate, in percentage points (pp).
- **t** = t-statistic clustered by symbol-month.
- **CONSISTENT** = |t| ≥ 3 **and** the edge has the same sign before 2024 and from 2024 onwards. This rule was fixed before any table was read.

**Fill realism.**
- The path is read on M15 for every timeframe.
- A limit fill cannot also take profit on its fill bar.
- A bar that hits both −1R and +kR scores 0.5. That happens on 1.3% of M15 fills, 0.4% on H1 and 0.2% on H4.
- Costs are reported, not subtracted. "1:1 net R" is the 1R-stop / 1R-target expectancy after the round-trip spread.

---

## 1. FVG detector audit (line by line)

| Item | Verdict | Evidence |
|---|---|---|
| Bullish definition | ✅ correct | candle-1 high < candle-3 low; zone = [c1 high, c3 low]; strict `<`, so touching wicks are not a gap (test) |
| Bearish definition | ✅ correct | mirror: c1 low > c3 high (test) |
| Candle indexing | ✅ correct | `j` = 3rd candle; `t_known` = close of candle 3; ATR from candle 1 (before the displacement) |
| Timeframe aggregation | ⚠️ **bug found and fixed** | H1/H4 UTC buckets fine. **D1 had a 1–2 hour "Sunday day"** in every summer-time week (~264 per pair). Monday's "previous day high/low" was that stub, and it distorted D1 ATR, D1 structure and D1 FVGs. Fixed in `data.resample`; test added. |
| Timestamp/timezone | ✅ verified | FOREXSB vs converted MT5 bars: correlation 1.00 at 0h offset, ~0.0 at ±3h. Tick volume peaks at 07–08 and 13–15 UTC (London/NY). XAUUSD tick volume is constant (15), so unusable. |
| Sessions | ⚠️ limitation | Fixed UTC hours. London/NY opens shift by 1h for half the year (DST). |
| First touch | ✅ correct | First bar trading into the gap after candle 3 closes |
| Partial / 25 / 50 (CE) / 75 / full | ✅ correct | Running maximum penetration (test) |
| Invalidation | ✅ correct | Close beyond the far edge (test) |
| Duplicates | ✅ none possible | A bull and a bear FVG cannot form on the same bar (test on real data) |
| Overlapping FVGs | ⚠️ not merged | A run of N strong candles makes N−2 FVGs. 25–26% of FVGs are "chained" to the previous candle's FVG; a trader would see one zone. |
| Stale FVGs | ✅ handled | Followed for 200 candles; only ~5% are first touched after 50+ candles |
| HTF/LTF sync | ✅ correct | HTF values come only from fully closed bars (`last_closed`). Tests confirm features don't change when future bars are added. |

**Sliver gaps are the main way the detector differs from a human.**
- 21% of all FVGs are smaller than 0.10 ATR, 39% are smaller than 0.20 ATR, and 52% are smaller than 0.30 ATR.
- 44% are touched again on the very next candle.

The chart examples show these as hair-thin bands nobody would draw.

## 2. Unit tests

`python -m pytest bensim_research/ -q` gives **15 passed**: 11 new audit tests and 4 earlier ones.

The new tests cover:
- Bull/bear definitions, equal-wick rejection and indexing.
- `t_known`.
- No same-bar duplicates, and chained runs.
- Lifecycle levels 25/50/75/full and close-beyond.
- Distinct touches.
- No take-profit on the limit fill bar, same-bar = 0.5, unresolved = NaN.
- n-bar returns.
- Bearish results exactly mirror bullish ones on synthetic data.
- UTC sessions, the D1 22:00 roll with the Sunday open joining Monday, and W1.
- Features unchanged by future bars on real EURUSD data.

**Two bugs in my own new code were caught and fixed before the final numbers:**
- The touch-count window and the revisit window were different lengths.
- The "chained" flag was always true.

## 3. Visual examples

Charts are in `data/research/artifacts/bensim/fvg_audit/charts/`. Each shows:
- The FVG (shaded) and CE (dashed).
- The first touch (circle).
- Confirmed swing pivots (blue triangles).
- The nearest liquidity above and below at creation.
- Entry, −1R stop, +1R and +2R.
- MFE and MAE.
- A full feature/outcome header.

| Set | File |
|---|---|
| 50 wins (model A hit +1R first) | `examples_win.html` |
| 50 losses | `examples_loss.html` |
| 50 neutral (ambiguous, unresolved, or \|ret20\| < 0.1R) | `examples_neutral.html` |
| 200 random review set (50 per timeframe) | `manual_review.html` + `manual_review_200.csv` |

## 4. Lifecycle statistics (all FVGs)

| tf | n | revisited | median candles to touch | 50% CE | full fill | close beyond | P(CE \| 25%) | median max penetration (× gap size) |
|---|---|---|---|---|---|---|---|---|
| M15 | 283,519 | 95.5% | 2 | 93.9% | 92.3% | 88.4% | 0.99 | 23× |
| H1 | 65,902 | 95.1% | 2 | 93.3% | 91.7% | 89.5% | 0.99 | 23× |
| H4 | 17,909 | 95.2% | 2 | 93.2% | 91.3% | 90.0% | 0.99 | 20× |
| D1 | 2,939 | 95.4% | 2 | 93.9% | 92.1% | 91.5% | 0.99 | 20× |

Once price enters a gap it almost always crosses it (P(CE | 25%) = 0.99), and usually runs well past it. As the brief says, this does **not** by itself mean there's no reaction. Section 13 measures the reaction.

## 5–17. Reaction tables (model A = limit order at the near edge on first touch)

Columns:
- **win1**: share hitting +1R first.
- **ctl1**: the random control.
- **edge**: win1 − ctl1, in points.
- **t**: t-statistic.
- **DEV / HOLD**: edge before 2024 / from 2024.
- **cost R**: spread as a fraction of 1 ATR.
- **net 1:1**: expectancy after spread.

### 17. Timeframe comparison (the headline)

| tf | fills | win1 | ctl1 | edge 1R | edge 2R | edge 3R | t | DEV / HOLD | cost R | net 1:1 | consistent |
|---|---|---|---|---|---|---|---|---|---|---|---|
| M15 | 270,759 | 48.6 | 49.6 | **−1.1** | 0.0 | +0.1 | −7.4 | −0.9 / −1.4 | 0.164 | −0.19R | yes (negative) |
| H1 | 62,649 | 51.2 | 49.8 | **+1.4** | +1.5 | +0.9 | 4.3 | +1.5 / +1.3 | 0.078 | −0.05R | yes |
| H4 | 17,051 | 51.8 | 50.4 | **+1.4** | +1.2 | +1.3 | 3.7 | +1.5 / +1.2 | 0.039 | −0.00R | yes |
| D1 | 2,803 | 50.6 | 49.2 | +1.6 | +0.5 | +0.9 | 1.2 | +1.1 / +2.6 | 0.014 | −0.00R | no (too few) |

**There is a real, measurable reaction at H1/H4 FVGs, but it is small:** about 1.4 points above random, the same in both periods.

At 1:1 that is worth about +0.03R before costs. Spread takes 0.08R on H1, so it's negative after costs. On H4 it's break-even. **On M15, price reacts slightly *against* the FVG,** and spread there is 0.16R. Higher timeframes did better and M15 worst, the same ranking the [MPM Markets 40k-FVG study](https://mpmmarkets.com/research/does-the-fair-value-gap-strategy-work) found.

### 4. Size (ATR-normalised)

- **H1/H4:** no size bucket is CONSISTENT. Edges are +0.6 to +2.5 points in almost every bucket with no clear trend. H1 >1.0 ATR: +2.4 (t 2.7). H4 >1.0 ATR: +2.5 (t 2.1, but negative in HOLD).
- **M15:** larger gaps are *worse*. 0.75–1.0 ATR: −2.3 (t −4.0). >1.0 ATR: −2.9 (t −6.0). Both are CONSISTENT, and negative.

**On its own, size does not separate good FVGs from bad.**

### 5. Displacement (weak < 0.5 ATR body; strong ≥ 1.0 ATR body and body/range ≥ 0.6)

- **H1:** normal +1.5 (t 3.4, consistent), strong +1.1, weak +1.7.
- **H4:** +1.2 to +1.8 across all three, none consistent.
- **M15:** strong displacement is *worse*, −1.8 (t −6.5).

**Strong displacement does not improve the reaction.** On M15 it predicts slightly more continuation through the gap.

### 6. Session (middle candle, UTC)

| tf | Asia | London | Overlap | New York | Off |
|---|---|---|---|---|---|
| H1 edge (t) | +1.3 (2.5) | +0.2 (0.7) | **+2.2 (3.3)** | **+2.8 (4.4)** | +0.3 |
| M15 edge (t) | −1.2 (−4.0) | −1.6 (−4.5) | −0.7 | −0.1 | −2.4 (−5.2) |

H1 FVGs formed during Overlap and New York react best, and both are CONSISTENT. London is the weakest session for H1, not the strongest. No session rescues M15.

### 7. HTF alignment (M15/H1 vs H4 structure, H4 vs D1; neutral = HTF efficiency ratio < 0.2)

- **H1:** bullish FVG against a *bearish* H4 is +3.5 (t 3.3, consistent). Bullish aligned is +2.4 (t 2.1); bearish aligned is +0.8.
- **H4:** bullish aligned with D1 is +4.8 (t 1.4, not significant).
- **M15:** aligned bearish is −2.1 (t −4.3), and aligned bullish −1.9.

**HTF alignment does not reliably improve the reaction.** The one consistent H1 cell is a counter-trend bullish FVG.

### 8. Liquidity sweep before the FVG

| tf | no sweep | minor sweep | major sweep |
|---|---|---|---|
| H1 | +1.6 (t 3.2) | +1.0 | +1.3 |
| H4 | +2.1 | +7.7 (n=168) | +0.8 |
| M15 | −1.3 (t −7.3) | −0.9 | −0.1 |

**A sweep before the FVG does not improve the reaction.** On M15 it only removes part of the negative bias.

### 9. Structure event at creation

- **H1:** none +1.3 (t 3.9, consistent), BOS +2.1 (t 2.9), MSS +1.0.
- **H4:** none +2.4 (consistent), BOS +0.3, MSS +0.2.
- **M15:** BOS −2.3 (t −7.4), MSS −1.4 (t −4.4), none −0.5.

**FVGs created with a BOS/MSS do not react better.** On M15 they react worse.

### 10. Location

- **Premium/discount, favourable** (bullish in discount, bearish in premium): H1 +2.1 (t 3.3, consistent); **H4 +2.7 (t 3.2, consistent), net +0.03R**, the best H4 location cell. Unfavourable: H1 +0.9, H4 +1.3; M15 −1.9 (t −8.8).
- **HTF supply/demand zone:**
  - Inside an aligned zone: H1 +1.3, H4 −0.4.
  - No zone: H1 +1.4 (consistent), H4 +1.7 (consistent).
  - M15 inside an *opposing* zone: −2.2 (consistent).
  - Being inside a zone does not help.
- **Near the previous day's or week's high/low, or equal highs/lows:** no meaningful difference. Every split is within ±1 point of its timeframe average, except H4 near equal highs/lows at −0.2 vs +2.9 for not near.

### 11. First vs later touches

| tf | 1st | 2nd | 3rd | 2nd after a prior 50–100% fill | 2nd after a full fill |
|---|---|---|---|---|---|
| H1 | +1.4 (t 4.3) | +1.0 (t 3.4) | +0.2 | +0.5 | +1.0 |
| H4 | +1.4 (t 3.7) | −0.1 | −0.5 | +3.2 (n=1,551) | −0.6 |
| M15 | −1.1 | −1.2 | −1.5 | −0.7 | −1.5 |

The first touch is the best on H1/H4, and the reaction fades with each later touch.

### 12. Entry models

| tf | A first touch | B 25% | C 50% CE | D 75% | E close back outside |
|---|---|---|---|---|---|
| H1 edge (t) | +1.4 (4.3) | +1.3 (3.7) | +1.4 (3.9) | +1.3 (4.6) | +1.1 (3.4) |
| H4 edge (t) | +1.4 (3.7) | +1.2 (3.2) | +1.2 (3.0) | +1.2 (3.0) | +1.0 (2.7) |
| M15 edge (t) | −1.1 (−7.4) | −1.1 | −0.9 | −0.8 | −0.1 (−0.5) |
| fill rate H1 | 95% | 94% | 93% | 93% | 66% |

**The choice of entry barely matters,** because about 99% of FVGs that reach 25% also reach 75%. A deeper entry gives a better price but the same direction. The close-back-outside confirmation (E) removes the M15 negative bias, but it adds no positive edge.

### 13–15. Penetration, MFE/MAE and 1R/2R/3R excursion

| tf | series | MFE median | MAE median | P(MFE > MAE) | ret20 mean | +1R / +2R / +3R first |
|---|---|---|---|---|---|---|
| H1 | FVG A | 2.79 | 2.70 | 50.6% | +0.029R | 51.2 / 33.9 / 24.8 |
| H1 | control | 2.65 | 2.73 | 49.2% | −0.049R | 49.8 / 32.5 / 23.9 |
| H4 | FVG A | 2.91 | 2.82 | 50.7% | +0.033R | 51.8 / 34.6 / 25.9 |
| H4 | control | 2.79 | 2.83 | 49.9% | +0.009R | 50.4 / 33.3 / 24.6 |
| M15 | FVG A | 3.05 | 3.04 | 49.9% | −0.003R | 48.6 / 32.7 / 23.9 |
| M15 | control | 2.96 | 3.06 | 49.2% | −0.036R | 49.6 / 32.7 / 23.8 |

**Does price move in the FVG's direction after entering it?** On H1/H4, slightly more often than random: favourable movement is about 0.1 ATR larger, and the 20-candle return beats the control by +0.03 to +0.08R. On M15, no.

### 16. Instruments (H1 / H4 edge in points)

| | AUD | EUR | GBP | JPY | CAD | CHF | XAU |
|---|---|---|---|---|---|---|---|
| H1 | +2.0 | +1.7 | +1.8 | +2.0 | +0.9 | +1.1 | +0.5 |
| H4 | +1.1 | **+3.9 (t 3.1, consistent, net +0.04R)** | +0.6 | +2.6 | −0.9 | +3.2 | −0.1 |

**XAUUSD shows no reaction on H1 or H4.** Every pair is positive on H1, but no single pair is significant alone.

### Extra (post-hoc, added after reading the tables above): how long price stayed away

This is known at the moment of the touch, so it can be used live.

| tf | 1 candle | 2–3 | **4–10** | 11–50 | >50 |
|---|---|---|---|---|---|
| H1 edge (t) | +0.2 (0.0) | +1.9 (3.1) | **+4.2 (6.4)**, DEV +3.8 / HOLD +5.0 | +1.2 | +1.6 |
| H4 edge (t) | +1.0 | +1.2 | +2.2 (3.5), HOLD −1.2 | +1.5 | +2.2 |
| M15 edge (t) | **−1.8 (−8.9)** | −1.1 | +0.5 | −0.2 | −1.7 |

**FVGs that price re-touches on the very next candle are pure noise on H1 and negative on M15, and they make up 44% of all FVGs.**

The H1 "price stayed away 4–10 candles" group is the strongest cell in the study:
- Win rates: 53.2% at 1R (control 48.8%), and 35.8% at 2R (control 32.0%).
- That's about +0.07R gross at a 2:1 target. The spread costs 0.078R, so net is roughly **break-even**.

Because this cut came after looking at the data, it is a **hypothesis, not a result**.

## 18. 200-example manual review dataset

`manual_review_200.csv` contains 50 random FVGs per timeframe. Each row has:
- The chart path, timestamp, instrument, timeframe and FVG coordinates.
- Every feature and outcome.
- Two empty columns: `human_meaningful_YN` and `human_notes`.

`manual_review.html` shows the 200 charts in one scrolling page.

**This is the critical test, and it needs you.** Mark each one Y if you would have traded or marked that FVG, N if not. Then I can measure whether your Y group reacts differently from the N group. That's the only way to put the "trader's eye" into rules without overfitting.

## 19. Implementation errors found

1. **Fixed:** D1 Sunday stub days in `data.resample`. The earlier Bensim backtest used D1 for HTF bias and previous-day high/low liquidity, so those runs used corrupted D1 data on ~264 Mondays per pair. The direction of that impact is unknown, but D1 bias showed no value in any test.
2. **Earlier backtest, pessimistic:** a bar touching both stop and target counted as a loss. Measured ambiguity is small (0.2–1.3% of fills), so this explains at most about 0.01R of the old losses.
3. **Earlier lifecycle, optimistic:** "respected after touch ~50%" counted the touch bar's high, which may have happened before the touch, and it had no random benchmark. It could not show an edge either way, and it is replaced by this audit.
4. **Design, not a code bug:** the earlier system used **M15 for confirmation**, and M15 is the timeframe where FVGs react *against* their direction (−1.1 points, t −7.4). It also traded every sliver and chained gap.
5. **Detector:** correct to the textbook definition. It needs a "visible imbalance" rule before strategy work: exclude tiny gaps and gaps re-touched the next candle, and merge chained gaps.
6. **Verified correct:** definitions, indexing, no look-ahead, UTC alignment, HTF sync, CE/fill/invalidation logic.

## 20. Recommendation for the next Bensim experiment

What the data shows:
- **FVGs are not random:** on H1/H4 there is a small, consistent reaction of about +1.4 points (+0.03R).
- **The reaction is too small to beat costs** on M15 and H1. On H4 it's about break-even.
- **None of the confluences in the spec make it reliably bigger:** sweep, BOS/MSS, HTF alignment, supply/demand, strong displacement and Wyckoff (not tested here).
- **Only four factors make it bigger and hold in both periods:** price stayed away 4–10 candles (H1), NY/Overlap session (H1), favourable premium/discount (H1 and H4), and EURUSD H4.

Next steps, in order:

1. **You review the 200 charts (Y/N).** No more optimisation until we know whether the detector's FVGs are the ones you trade.
2. **Detector v2: a "visible imbalance" rule.** Size ≥ 0.3 ATR, price stays away ≥ 4 candles, chained gaps merged. Re-run this audit unchanged and compare with your Y/N labels.
3. **One pre-registered test,** written down before running:
   - H1 FVG, first touch after price stayed away 4–10 candles, limit at the near edge.
   - Stop 1 ATR, target 2R, real costs.
   - Judged **only on data after 2026-08-14 or on forward demo**, because 2018–2026 has now been looked at.
   - It passes only if net expectancy > 0 with a t-statistic ≥ 2.
4. **Drop M15 FVGs as setups,** and drop M15 FVG/structure confirmation as a filter. On this data M15 FVGs are negative.

Do not combine factors to find the best historical profit. With ~200 cells tested here, some will look good by chance.
