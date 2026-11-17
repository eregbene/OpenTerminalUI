# Round 28: imbalance / liquidity map (user request 2026-11-11)

## What the user wants (his words, sorted)
1. **All timeframes:** imbalances (FVGs) on 5m, 15m, 30m, 1H, 4H, 12H, 1D.
2. **Same move?** Do the imbalances need to point the same way (bull with bull, bear with bear) across timeframes, and does it matter?
3. **For each zone:**
   - Does price take the liquidity under it, or does the zone hold?
   - How often each happens.
   - How long the zone holds.
   - When it happens, compared with the higher and lower timeframes.
4. **BOS / MSS on each timeframe:**
   - Was the move that created the imbalance a BOS?
   - After a sweep, does an MSS appear, on which timeframe, and how fast?
5. **News:** does the imbalance form, fill or break around high-impact news?
6. **When the imbalance fills:** how many hours or days later, and in which session or hour.
7. **Which trade, on which timeframe:**
   - **a. Before the fill:** follow price into the imbalance. The target is the zone; the stop is above the impulse high.
   - **b. At the fill:** buy or sell the zone. The stop goes under the liquidity, aiming for 1:2 or 1:3.
   - **c. Follow the push to the liquidity:** the zone fails (a close through it), so trade toward the liquidity under it.
   - **d. Sweep then return:** liquidity taken, then a lower-timeframe MSS, then entry at the lower-timeframe FVG (round 27c: 4H was best, +141R).
8. **How many times each zone holds:** the 1st, 2nd and 3rd touch.
9. **Why it held and why it didn't.** Factors to compare:
   - imbalance size;
   - whether the impulse made a BOS;
   - higher-timeframe trend agreeing;
   - stacked zones, or opposite zones nearby;
   - distance to the liquidity;
   - zone age;
   - session and hour;
   - news;
   - how many touches it already had.
10. **Make a note of all of this:** this file.

## How it is tested
- **Data:** M5 bars from the candles DB, 7 pairs (EURUSD GBPUSD AUDUSD XAUUSD USDJPY USDCHF USDCAD).
- **Discovery period:** 2024-11-07 .. 2026-11-03 (data ends 2026-11-03).
- **Untouched check:** 2021-10 .. 2024-10, used only for rules picked in discovery, unchanged.
- **Zone** (bullish; bearish is the mirror):
  - FVG at bar i: low[i] > high[i-2], with a gap of at least 0.3 × ATR14 of that timeframe;
  - liquidity = the lowest low of bars i-6..i-2.
- **Outcomes:** M5 path, costs (spread + 0.2 pip) in R, stop first if stop and target are hit in the same bar.
- **Results:** in R at the same $ risk, and in $ at $1k risk per trade.
- **Swings:** 2-bar fractals, used only once confirmed.
- **Multiple testing:** many cuts are tested on the same data, so nothing goes live without the untouched check.

## Files
- **`zone_map.py`**: one row per zone, with outcomes and factors.
- **`zone_map.log`**: the report.
- **`zone_map_events.csv`**: the raw rows.

## Candidates fixed BEFORE the untouched check (written 2026-11-11, after discovery, before running 2021-10 .. 2024-11)
Same code, same definitions, no parameter changes. Pass = total R > 0 AND t >= 2 on the untouched period.
1. H4 zone -> sweep -> H1 MSS -> limit at the newest H1 FVG, stop under the sweep low, 1:3 (all)      [discovery +102R, t 2.4]
2. same, only zones whose impulse made a BOS (bos=True)                                                [discovery +98R, t 3.6]
3. same, only zones formed WITH the H4 trend (own_trend=1)                                             [discovery ~+0.5R/trade]
4. H12 zone, buy/sell at the fill (zone edge), stop under the liquidity, 1:3                            [discovery +119R, t 2.6]
5. D1 zone -> sweep -> H4 MSS close, 1:3                                                                [discovery +33R, t 1.6]
Bug fixed before this list: section 6 had look-ahead for strategy a (filtered on zones that were later touched).

## Untouched check result (2021-10-04 .. 2024-11-07, same code) -- NONE PASSED (pass = total > 0 and t >= 2)
1. H4 sweep-MSS-FVG 1:3         600 trades  -0.072R  -43R  t -1.1   FAIL
2. same, bos                    400 trades  -0.018R   -7R  t -0.2   FAIL
3. same, own_trend=1            291 trades  -0.197R  -57R  t -2.2   FAIL (sign flipped = the discovery filter was luck)
4. H12 at the fill 1:3         1173 trades  +0.065R  +76R  t  1.4   positive both halves, below the pass mark (5y ~+195R, t ~2.3)
5. D1 sweep-MSS close 1:3       292 trades  -0.080R  -23R  t -1.0   FAIL
The DESCRIPTIVE facts repeat almost exactly (fill %, took-liquidity %, hold times, touch-by-touch) -> those are real.
