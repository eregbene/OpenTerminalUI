# BSI V2 bsi_order_flow Video Examples

Source basis: audiovisual Order Flow reconstruction from the scratchpad files named in `BSI_V2_bsi_order_flow_VIDEO_SPEC.md`. Frame references are by video position because the current accessible extracted-frame bundle does not expose named Order Flow frame folders in the workspace; the underlying audit states frames were sampled at roughly 5s intervals and viewed directly.

## ORDERFLOW_GOLDEN_01

VIDEO: `1. Order Flow Trading Strategy.mp4`, about 55-90%.

SYMBOL: GBPUSD.

DIRECTION: SHORT.

TIMEFRAME: 15m.

SWINGS: Uptrend into a swing high around 1.22766, then break of a higher-low around 1.21817.

LIQUIDITY: Descending diagonal trendline through retracement swing lows, labeled `liquidity`. Not horizontal.

SWEEP: Exact wick/close taken condition NOT_ESTABLISHED.

STRUCTURE BREAK: MSS/CHoCH-style bearish break of the higher-low.

FIB: Drawn top to bottom: 100% near 1.22766 to 0% near 1.21817.

ZONE: Entry box in premium, around visible 70.5-79 region.

FVG: Present in selected extreme zone; exact three candles NOT_ESTABLISHED.

OB: Mentor OB tied to FVG origin candle; exact candle NOT_ESTABLISHED from accessible resolution.

ENTRY: From extreme gray rectangle.

SL: Above selected entry zone by general Order Flow rule; exact value NOT_ESTABLISHED.

TP: Opposing liquidity/structure; exact level NOT_ESTABLISHED.

RESULT: NOT_ESTABLISHED.

SEQUENCE:

1. Mentor sees prior bullish structure.
2. Mentor marks the high and later higher-low break.
3. Mentor identifies bearish MSS.
4. Mentor draws Fib on the break-causing leg.
5. Mentor selects premium/extreme zone.
6. Mentor marks the FVG/OB rectangle.
7. Mentor marks diagonal liquidity.
8. Mentor prepares short from the selected array.

Expected golden behavior:

- Select bearish MSS.
- Use top-to-bottom break leg.
- Require premium side for MSS.
- Select the most extreme valid array.
- Recognize trendline liquidity as liquidity.
- Use structural/array stop, not fixed pips.
- Target opposing liquidity/structure.

## ORDERFLOW_GOLDEN_02

VIDEO: `2. Order Flow Example For Market Structure Shift (1).mp4`, about 35-97%.

SYMBOL: GBPUSD.

DIRECTION: SHORT.

TIMEFRAME: 15m.

SWINGS: HH/HL sequence into a high, then break of higher-low.

LIQUIDITY: Relative equal highs/horizontal resistance are shown, and later a diagonal trendline is labeled `liquidity`.

SWEEP: Exact liquidity-taken candle NOT_ESTABLISHED.

STRUCTURE BREAK: Bearish MSS, literally labeled `mss`.

FIB: Drawn with 0% near broken low around 1.21377 and 100% at prior high.

ZONE: Entry near top/premium side.

FVG: Gray entry rectangle at top of leg.

OB: OB/FVG zone accepted; exact origin candle NOT_ESTABLISHED.

ENTRY: Around top edge of gray zone, about 1.21238 from RR-tool reading.

SL: Above gray rectangle, about 1.21378.

TP: Below entry, opposing liquidity/structure.

RESULT: RR-tool target below; exact final outcome NOT_ESTABLISHED.

SEQUENCE:

1. Mentor sees bullish HH/HL sequence.
2. Mentor marks MSS after higher-low break.
3. Mentor draws Fib across the break-causing move.
4. Mentor identifies premium entry zone.
5. Mentor marks OB/FVG.
6. Mentor marks liquidity below/around the trade context.
7. Mentor places short entry at zone.
8. Mentor places SL above zone.
9. Mentor targets lower liquidity/structure.

Expected golden behavior:

- MSS/CHoCH reversal accepted.
- Stop tied to array high.
- Trendline liquidity must be representable.
- Target is not fixed RR.

## ORDERFLOW_GOLDEN_03

VIDEO: `3. Order Flow Example For Market Structure Shift (2).mp4`, about 29-95%.

SYMBOL: GBPUSD.

DIRECTION: LONG.

TIMEFRAME: 15m.

SWINGS: LL/LH sequence, then break of lower-high.

LIQUIDITY: Ascending diagonal trendline through recovery swing highs, labeled `liquidity`.

SWEEP: Exact taken candle NOT_ESTABLISHED.

STRUCTURE BREAK: Bullish MSS, literal on-screen `mss`.

FIB: Drawn for bullish reversal leg.

ZONE: Discount.

FVG: Gray entry rectangle in lower portion of leg.

OB: OB/FVG zone selected; exact origin candle NOT_ESTABLISHED.

ENTRY: At lower gray rectangle.

SL: Below selected array. RR tool shows stop distance about 25.1 pips.

TP: Above entry at natural/structural target. RR tool shows about 34.8 pips and 1.39R.

RESULT: NOT_ESTABLISHED.

SEQUENCE:

1. Mentor sees bearish LL/LH structure.
2. Mentor marks the lower-high that breaks.
3. Mentor labels bullish MSS.
4. Mentor uses discount-side entry logic.
5. Mentor marks trendline liquidity.
6. Mentor selects the lower OB/FVG.
7. Mentor enters long.
8. Mentor places SL below array.
9. Mentor targets structure/liquidity above.

Expected golden behavior:

- Long MSS trade in discount.
- Trendline liquidity supported.
- RR below 1.5 must not be automatically rejected if target is mentor-valid.

## ORDERFLOW_GOLDEN_04

VIDEO: `4. Order Flow Example For Market Structure Break (1).mp4`, about 23-95%.

SYMBOL: GBPUSD.

DIRECTION: SHORT.

TIMEFRAME: 5m.

SWINGS: LL/LH continuation downtrend.

LIQUIDITY: No distinct labeled liquidity in sampled frames.

SWEEP: NOT_ESTABLISHED.

STRUCTURE BREAK: Fresh lower-low continuation MSB.

FIB: No Fib tool visible.

ZONE: PD requirement NOT_ESTABLISHED for continuation.

FVG: Filled imbalances rejected; fresh unmitigated array used.

OB: Gray rectangles mark candidate arrays.

ENTRY: At first and later continuation arrays.

SL: Above array. First measured stop about 8.2 pips / 0.07%.

TP: Further low / opposing structural liquidity.

RESULT: NOT_ESTABLISHED.

SEQUENCE:

1. Mentor sees established downtrend.
2. Price prints fresh lower-low.
3. Mentor rejects filled imbalances.
4. Mentor marks a fresh short entry array.
5. Mentor enters short with SL above the array.
6. Trend continues.
7. A later fresh lower-low creates another array and another separate opportunity.

Expected golden behavior:

- Continuation MSB accepted.
- Do not require one trade per trend.
- Do not reuse mitigated arrays.
- PD treatment remains AMBIGUOUS.

## ORDERFLOW_GOLDEN_05

VIDEO: `5. Order Flow Example For Market Structure Break (2).mp4`, about 37-95%.

SYMBOL: EURUSD.

DIRECTION: LONG.

TIMEFRAME: 5m.

SWINGS: HH/HL continuation uptrend.

LIQUIDITY: No distinct labeled liquidity in sampled frames for the MSB segment.

SWEEP: NOT_ESTABLISHED.

STRUCTURE BREAK: Repeated higher-high continuation MSBs.

FIB: No Fib tool visible in sampled frames.

ZONE: PD requirement NOT_ESTABLISHED for continuation.

FVG: Small/tight FVG may be used directly.

OB: OB used when the FVG is not very short.

ENTRY: At fresh long arrays; mentor states entry should be slightly shy of exact level for spread.

SL: Below array or, conservatively, below wider structural low.

TP: Natural/structural. RR tool shows at least one 1.84R trade; audio also references 3.73R and 5.72R examples.

RESULT: NOT_ESTABLISHED.

SEQUENCE:

1. Mentor sees uptrend.
2. Price creates a fresh higher-high.
3. Mentor marks an FVG/OB array.
4. Mentor chooses FVG if very short, otherwise OB.
5. Mentor offsets entry slightly for spread.
6. Mentor places SL below array or wider structural low if conservative.
7. Mentor targets next liquidity/structure.
8. Later higher-highs create fresh independent entries.

Expected golden behavior:

- Multiple independent MSB opportunities in one trend.
- Spread offset distinct from spread rejection.
- Variable RR.
- Conservative stop is optional, not universal.

## Competing Objects The Mentor Ignored

- Filled imbalances in continuation examples are ignored.
- Non-extreme arrays nearer price are ignored when a more extreme valid array is available.
- Smaller noise swings are ignored in favor of visually meaningful HH/HL or LL/LH structure.
- Generic last-opposite-candle OB is not selected as a rule; the FVG-origin candle is the mentor OB.

Where the video does not establish why one competing object was ignored, the reason remains NOT_ESTABLISHED.
