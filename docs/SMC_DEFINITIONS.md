# SMC Definitions

Phase 5 uses explicit configurable definitions.

- Swing high: a bar whose high is greater than or equal to highs in the configured left/right pivot window. It is confirmed only when the right-side bars exist.
- Swing low: a bar whose low is less than or equal to lows in the configured left/right pivot window.
- BOS: a break in the same direction as the established structure trend.
- CHoCH: a counter-structure break against an established trend when displacement is not required or not present.
- MSS: a counter-structure break with configured displacement evidence.
- Liquidity: a rule-based price reference above confirmed swing highs or below confirmed swing lows. It is not a claim about actual resting orders.
- Sweep: breach of a liquidity reference followed by a close back inside the reference level.
- FVG: three-candle wick gap where candle one and candle three do not overlap.
- Order block: last opposing candle before a confirmed structure break using the configured search/rule.
- Dealing range: range between latest confirmed swing high and latest confirmed swing low.
- Premium/equilibrium/discount: deterministic subdivisions of a dealing range.

Terminology varies across traders. Bensim stores the configuration version and hash with every result so interpretations remain reproducible.
