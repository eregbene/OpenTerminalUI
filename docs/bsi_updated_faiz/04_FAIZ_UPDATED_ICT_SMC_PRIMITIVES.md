# Faiz Updated ICT/SMC Primitives

Methodology version: `BSI_BASELINE_V3_UPDATED_FAIZ`.

Source rule: only `F:\new faiz` is methodology authority. Existing BSI V1/V2 code is audit material, not authority.

## Evidence Extracted So Far

### Market Structure

- Rule ID: `FAIZ_V3_STRUCTURE_001`
- Definition: bullish structure is higher highs and higher lows; bearish structure is lower lows and lower highs; ranging structure is price moving inside a small range without directional progress.
- Source: `2. Market Structure.mp4` at `00:00:00-00:02:46`.
- Evidence: `SPOKEN_AND_VISUAL`
- Fractal rule: structure is timeframe-dependent. A higher-timeframe bullish pullback can contain a lower-timeframe bearish trend; this is allowed and later strategies use that fractal relationship.
- Source: `2. Market Structure.mp4` at `00:02:55-00:06:53`.
- Production status: core primitive.

### MSB / Market Structure Break

- Rule ID: `FAIZ_V3_STRUCTURE_002`
- Definition: MSB is trend continuation. In bullish structure, a new higher high that breaks the prior high is MSB. In bearish structure, a new lower low that breaks the prior low is MSB.
- Source: `3. Market Structure Break & Market Structure Shift.mp4` at `00:00:00-00:02:17`.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: core primitive.

### MSS / Market Structure Shift

- Rule ID: `FAIZ_V3_STRUCTURE_003`
- Definition: MSS is trend shift/reversal. After a fresh bullish higher high, bearish MSS occurs when price breaks the previous low created after that new high. After a fresh bearish lower low, bullish MSS occurs when price breaks the previous high/lower-high created after that new low.
- Source: `3. Market Structure Break & Market Structure Shift.mp4` at `00:02:27-00:05:51`.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: core primitive.

### External Liquidity

- Rule ID: `FAIZ_V3_LIQUIDITY_001`
- Definition: external liquidity is any relevant high or low.
- Source: `1. Finding Monthly, Weekly, & Daily Bias.mp4` at `00:00:44-00:02:16`.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: candidate primitive, pending cross-video confirmation.

### Buy-Side / Sell-Side Liquidity

- Rule ID: `FAIZ_V3_LIQUIDITY_004`
- Definition: buy-side liquidity sits above highs/resistance where short stops and breakout buy orders can trigger. Sell-side liquidity sits below lows/support where long stops and breakout sell orders can trigger.
- Source: `5. Liquidity.mp4` at `00:00:00-00:07:07`.
- Evidence: `SPOKEN_AND_VISUAL`
- Scope: liquidity can exist at many highs/lows, including higher lows and prior highs, but V3 must prioritize important liquidity areas rather than treating every minor point equally.
- Production status: core primitive.

### Internal Liquidity

- Rule ID: `FAIZ_V3_LIQUIDITY_002`
- Definition: internal liquidity is an unmitigated FVG in discount for bearish draw, or premium for bullish draw.
- Source: `1. Finding Monthly, Weekly, & Daily Bias.mp4` at `00:00:56-00:02:16` and `00:04:51-00:05:05`.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: candidate primitive, pending cross-video confirmation.

### Equilibrium / PD

- Rule ID: `FAIZ_V3_PD_001`
- Definition: equilibrium is the midpoint of the dealing range measured with Fibonacci from swing low to swing high, or high to low depending on direction.
- Source: `1. Finding Monthly, Weekly, & Daily Bias.mp4` at `00:01:03-00:01:10`.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: candidate primitive.

### Premium / Discount Zone

- Rule ID: `FAIZ_V3_PD_002`
- Definition: the 50% Fibonacci level separates discount and premium zones.
- Long rule: after an upward move, draw Fib from the low/start to the high/end. Price below 50% is discount and is the preferred area for longs.
- Short rule: after a downward move, draw Fib from the high/start to the low/end. Price above 50% is premium and is the preferred area for shorts.
- Source: `8. Premium & Discount Zone.mp4` at `00:00:00-00:06:45`.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: core primitive.

### Valid MSS

- Rule ID: `FAIZ_V3_MSS_001`
- Definition: a market structure shift must break the relevant structure level with displacement. Displacement is a move in one direction with momentum/volume.
- Invalid case: if price breaks structure through messy stair-step movement without volume/momentum, mentor does not treat it as valid MSS.
- Source: `2. Valid MSS vs Invalid MSS.mp4` at `00:00:00-00:01:37`; `4. Valid MSS Example.mp4` short clip confirms the move may leave an FVG.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: core primitive.

### Trendline Liquidity

- Rule ID: `FAIZ_V3_LIQUIDITY_003`
- Definition: a trendline is liquidity. The Order Flow lesson explicitly points to a trendline and calls it liquidity.
- Source: `1. Order Flow Trading Strategy.mp4` at `00:02:38-00:02:52`.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: primitive/confluence only so far; no standalone trendline model established yet.

### SMT Divergence

- Rule ID: `FAIZ_V3_SMT_001`
- Definition: SMT divergence compares two or three correlated instruments. One instrument sweeps liquidity by making the next high/low while the correlated instrument fails to make the matching high/low.
- Source: `1. SMT Divergence.mp4` at `00:01:02-00:08:05`.
- Evidence: `SPOKEN_AND_VISUAL`
- Instrument pairs explicitly named: BTC/ETH, NAS100/S&P500/US30, EURUSD/GBPUSD/AUDUSD with DXY. DXY must be treated carefully because it is inverse unless visually inverted.
- Production status: standalone strategy and confluence primitive.

### Volume Imbalance

- Rule ID: `FAIZ_V3_VI_001`
- Definition: a volume imbalance is the gap between one candle close and the next candle open. It is a two-candle pattern.
- Source: `11. VOLUME IMBALANCE.mp4` at `00:00:30-00:02:57`.
- Evidence: `SPOKEN_AND_VISUAL`
- Usage: entries, institutional support/resistance, and sometimes daily bias. If broken, the level can flip from support to resistance or resistance to support.
- Instrument note: mentor says volume imbalances are mostly found on futures indices; they can appear on forex, but he suggests using them with indices.
- Production status: primitive/array. Standalone strategy is `NOT_ESTABLISHED` so far.

### BPR / Balanced Price Range

- Rule ID: `FAIZ_V3_BPR_001`
- Definition: BPR forms when an old FVG and a new FVG from the MSS move overlap. The old FVG is drawn forward as a box; if any part of the new FVG overlaps that box, the overlap/old FVG area is tradable as BPR.
- Source: `12. BPR.mp4` at `00:00:00-00:06:35`.
- Evidence: `SPOKEN_AND_VISUAL`
- Usage: BPR strengthens an FVG, can provide a tighter entry when the new extreme FVG is too large, and acts as old FVG flipping support/resistance after being broken.
- Production status: primitive/entry array and confluence, not standalone strategy so far.

### IFVG / Inverse Fair Value Gap

- Rule ID: `FAIZ_V3_IFVG_001`
- Definition: an FVG becomes an inverse FVG when price closes through the FVG instead of respecting it.
- Source: `21. The Juggernaut Model.mp4` at `00:00:50-00:01:49`.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: primitive and entry trigger.

### Fair Value Gap / Imbalance

- Rule ID: `FAIZ_V3_FVG_001`
- Definition: fair value gap and imbalance are treated as the same concept. It is a three-candle gap left by strong buying/selling.
- Bearish/short FVG detection: compare the low of the first candle with the high of the third candle; a gap between them is the bearish FVG area.
- Bullish/long FVG detection: compare the high of the first candle with the low of the third candle; a gap between them is the bullish FVG area.
- Expected behavior: price may return to fill the imbalance before continuing.
- Fractal rule: FVGs can exist on smaller timeframes inside a larger-timeframe move.
- Source: `7. Fair Value Gap.mp4` at `00:00:00-00:08:14`.
- Evidence: `SPOKEN_AND_VISUAL`
- Production status: primitive/entry array. Mentor explicitly says not to use FVG alone as an independent strategy.

### FVG Internal High/Low Liquidity

- Rule ID: `FAIZ_V3_FVG_LIQ_001`
- Definition: highs and lows formed inside an FVG become liquidity references. If price takes the low formed inside an FVG, the high formed inside that FVG can become draw, and vice versa.
- Source: `27. FVG LIQUIDITY.mp4` at `00:00:39-00:03:11`.
- Evidence: `SPOKEN_AND_VISUAL`
- Timeframe combinations named: M15/M1, D1/H1, H4/M15, W1/H4, MN/D1.
- Production status: primitive and possible model; standalone strategy status pending examples.

### Breaker Block

- Rule ID: `FAIZ_V3_BREAKER_001`
- Definition: after a lower-low/lower-high sequence and MSS, the last candle before the final lower-low leg is the bullish breaker block. After a higher-high/higher-low sequence and MSS, the last candle before the final higher-high leg is the bearish breaker block.
- Source: `13. BREAKER BLOCK.mp4` at `00:00:00-00:02:35`.
- Evidence: `SPOKEN_AND_VISUAL`
- FVG relationship: if an FVG is present around the breaker block, the breaker becomes stronger, but an FVG is not mandatory.
- Stop rule: mentor warns not to place stop only beyond the breaker block; safer stop goes beyond the extreme FVG.
- Production status: primitive/entry pattern, not standalone strategy so far.

### Order Block / Orderblock 2.0

- Rule ID: `FAIZ_V3_OB_001`
- Definitions: two OB methods are taught and both remain valid.
- Refined/old method: first candle of the FVG/imbalance sequence after an impulsive move. The original Orderblocks lesson says the first candle that creates the imbalance is the order block, not the last candle of the move.
- Classic/newer method: last opposite candle or consecutive opposite candles before the move that breaks structure. Bullish OB uses bearish/down-close candle(s) before upside structure break. Bearish OB uses bullish/up-close candle(s) before downside structure break.
- Strategy distinction: mentor says he uses the classic/consecutive-candle OB for Order Flow, while other strategies may still use the first-candle-of-FVG OB.
- FVG requirement: the classic/consecutive-candle OB does not require an FVG in that leg.
- Tradeoff: refined first-candle/FVG OB gives better RR but more missed entries; classic OB triggers more often but lower RR.
- High-probability confluence: classic OB aligned with breaker block is high probability.
- Original warning: OB and FVG should be in the correct premium/discount zone, and OBs should not be used blindly as standalone trades.
- Source: `28. Orderblock 2.0.mp4` at `00:00:30-00:04:54`.
- Evidence: `SPOKEN_AND_VISUAL`
