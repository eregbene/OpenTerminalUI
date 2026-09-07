# BSI V2 Cross Strategy Primitives

Shared vocabulary reconstructed from the videos.

## Liquidity Types

- `swing_level`: significant swing high/low. Used by New York, Order Flow, 9:30, ABCD.
- `equal_level`: repeated equal highs/lows or multi-touch support/resistance. Used by Under/Over, 9:30, sometimes Order Flow.
- `trendline_liquidity`: diagonal liquidity. Confirmed in Order Flow only.
- `session_box_edge`: Asian session high/low. Asian only.
- `origin_ob_edge`: far edge of origin order block. OB Liquidity.
- `residual_wick_liquidity`: older uncleared wick pockets. OB Liquidity.

## Break Types

- `wick_sweep`: wick beyond a level; no close required. New York.
- `close_break`: body close beyond level; wicks do not count. Under/Over and OB Liquidity; ABCD likely but not independently explicit.
- `mss`: reversal market structure shift. Order Flow, Asian, 9:30.
- `msb`: continuation market structure break. Order Flow, Reactionary inheritance.
- `displacement_substitute`: strong displacement can replace clean MSS. 9:30 only.

## Entry Types

- `bare_retest`: entry at the original swept/reclaimed level. New York, ABCD, Under/Over.
- `array_entry`: entry from OB/FVG. Order Flow, ABC, Asian, 9:30.
- `second_array_entry`: entry from fresh array 2 after array 1 reaction. Reactionary.
- `same_array_fakeout_retest`: entry from origin OB after fakeout/reclaim through same OB. OB Liquidity.

## Array Rules

- Mentor OB = first candle that creates the FVG/imbalance.
- Unmitigated arrays are preferred/required where arrays are used.
- Extreme array preference appears in Order Flow and 9:30.
- Reactionary array 2 has no fresh structure-break requirement.
- OB Liquidity must not create array 2.

## Premium/Discount

Confirmed required only in base Order Flow MSS material.

Not shown in New York, ABC, Asian, Under/Over, 9:30, ABCD, and not independently re-taught in Reactionary or OB Liquidity.

## Targets

- `fixed_1_2`: New York; ABCD default.
- `bounded_3_5`: 9:30 target-setting.
- `opposing_structure`: Order Flow, Under/Over, Reactionary, OB Liquidity.
- `asian_opposite_edge`: Asian.
- `b_leg_target`: ABC.

## Management

- Partials explicit: Under/Over and 9:30.
- Structure-break breakeven explicit: 9:30.
- Full exit at fixed target: New York, ABCD default.
- Management not established: ABC, Reactionary, OB Liquidity, except implied full target exits.

## Common No-Trade Patterns

- No retest after sweep: New York invalid.
- Huge fakeout: Under/Over skip; OB Liquidity fuzzy skip if huge.
- Clumsy/no-displacement MSS: 9:30 skip.
- B leg exceeds A start: ABC invalid.
- No array 2: Reactionary no trade.
- Heavy clean OB reaction: OB Liquidity should route away from OB Liquidity.
- Residual liquidity uncleared: OB Liquidity no trade yet.
