# BSI Current V2 Correction Plan

Do not change live methodology only for win rate or trade frequency.

## High-Confidence Findings

1. 9:30 index focus is correct. Keep FX/gold rejection until native index/M1 data exists.
2. Trendline is liquidity primitive, not standalone strategy.
3. Reactionary count remains suspicious because raw fallback over-permits array sequences.
4. OB Liquidity count was previously inflated; current count is lower but still needs same-array and residual-liquidity strengthening.
5. Under/Over main lesson is missing locally, so examples define only confirmed rules.

## Corrections To Consider Before More Demo Data

- Reactionary: require array1 to be linked to valid Order Flow MSS/MSB, require actual mitigation, then require impulse-created array2.
- OB Liquidity: require local-extremum origin candle and residual wick liquidity clearance.
- ABCD: require explicit ABC geometry before D sweep/reclaim/retest.
- Trendline: keep as liquidity primitive under Order Flow; improve anchor detection only after visual anchor rules are stronger.

## Corrections Not Recommended

- Do not add `bsi_trendline`.
- Do not make 9:30 trade FX/gold to increase frequency.
- Do not add newer public-index strategies until local video/audio exists.
