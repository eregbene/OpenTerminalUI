# BSI V3 Adaptive Manager Spec

Methodology version: `BSI_BASELINE_V3_UPDATED_FAIZ`.

Status: demo policy module implemented; broker routing not connected because replay validation is not yet promotion grade.

## Mentor Management Rules Extracted So Far

- Move to breakeven when price breaks the closest relevant structure in profit.
- Partial close may occur at 1:2, commonly 40-70%, or smaller 20-25% at POI/FVG/supply-demand.
- Do not take more than two partials before final TP.
- Trailing stop may follow new higher lows / lower highs instead of partials.
- Some models prefer fixed 1:2 or 3R-5R rather than chasing very large R.
- 4H OB has explicit fixed management: move to breakeven at `1:1`, hold for `1:2`.
- Weaver may accept a smaller safe target such as around `1.5R` when the H1 FVG draw is too close; do not shrink SL artificially just to force `1:2`.
- IFVG/PO3 requires BE when closest liquidity is taken and 50% partial at `1R`; if closest liquidity is taken before entry, no trade.
- Turtle Soups & Ranges takes 30-50% partial and BE at 0.5 of the range, then targets the opposite range side; if the opposite side is taken before entry, no trade.
- Yin Yang prefers fixed `1:2`, with BE at `1R` shown in examples; optional runners are secondary to the fixed target.
- 4H Candle Ranges takes 40-50% partial at `1R`, moves BE, then targets the opposite candle range side.
- SMT Session H/L uses BE after `1R`, with optional delayed BE around `1.5R` only in rare strong HTF/order-flow alignment.
- 1H Candle Ranges takes a partial and BE at 50% of the 8AM candle range.
- Enigma Range takes partial/BE at `0.5` of the engineered range, can use `0.79` as target/second partial, and final target can be opposite range side.
- 9:30 and Silver Bullet models have time/session-specific targets and should not be managed by generic early-exit logic before liquidity/FVG/fixed-R intent is reached.

## Conflict With Current Adaptive V2 To Audit

- Any generic MFE close that exits before mentor structure break/target logic may conflict.
- Any forced partial or SL movement not tied to mentor structure/POI may conflict.
- Any model using tiny stops just to satisfy RR must be rejected; mentor stop logic stays outside relevant structure/FVG/OB, not artificially tight.
- Any generic "leave fast" behavior must be disabled in V3 unless it maps to a mentor rule: BE at structure break/1R, partial at POI/2R, fixed target, or final liquidity target.

## Proposed V3 Direction

V3 adaptive actions should be strategy-aware and source-tagged:

- `MENTOR_BE_ON_STRUCTURE_BREAK`
- `MENTOR_PARTIAL_AT_POI_OR_2R`
- `MENTOR_TRAIL_STRUCTURE`
- `MENTOR_FINAL_TARGET_LIQUIDITY`
- `MENTOR_FIXED_1R_BE_2R_TARGET`
- `MENTOR_IFVG_CLOSEST_LIQUIDITY_BE`
- `MENTOR_IFVG_50_PERCENT_AT_1R`
- `MENTOR_TURTLE_RANGE_PARTIAL_BE_AT_0_5`
- `MENTOR_CANDLE_RANGE_PARTIAL_BE_TARGET_OPPOSITE_SIDE`
- `MENTOR_SESSION_SMT_BE_1R_OR_STRONG_BIAS_1_5R`
- `MENTOR_1H_CRD_PARTIAL_AT_50_PERCENT_RANGE`
- `MENTOR_ENIGMA_PARTIAL_BE_0_5_TARGET_0_79_OR_1_0`

Implemented demo-policy module:

- `backend/adaptive_management/v3_faiz.py`
- `backend/tests/test_adaptive_management_v3_faiz.py`

Current state:

- Strategy-aware V3 adaptive decisions are available as broker-neutral demo policy outputs.
- The module can select BE, partial, or hold according to the V3 management model.
- It does not mutate MT5/cTrader positions by itself.
- V3 MT5/cTrader broker routing must remain off until replay results and detector validation support promotion.
