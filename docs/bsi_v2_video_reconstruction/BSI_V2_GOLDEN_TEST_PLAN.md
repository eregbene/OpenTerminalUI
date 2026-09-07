# BSI V2 Golden Test Plan

Golden fixtures derive from the 9 `*_VIDEO_EXAMPLES.md` files. Use synthetic candle fixtures where real video OHLC is unavailable; assert geometry and rule decisions, not exact market replay.

## Positive Golden Tests

| Fixture ID | Source Example | Strategy | Expected Liquidity | Expected Sweep/Break | Expected Structure | Expected Zone | Expected Entry | Expected Stop | Expected Target | Pass Condition |
|---|---|---|---|---|---|---|---|---|---|---|
| BSI2-GOLD-OF-01 | ORDERFLOW_GOLDEN_01 | bsi_order_flow | trendline + EQH | liquidity before MSS | MSS short | premium half | extreme OB/FVG | above array | opposing low | Emits one short with trendline evidence |
| BSI2-GOLD-OF-02 | ORDERFLOW_GOLDEN_05 | bsi_order_flow | swing liquidity | MSB continuation | MSB long | PD ambiguous/not overforced | fresh array | array/structural option | natural target | New MSB+array emits new opportunity |
| BSI2-GOLD-NY-01 | NEW_YORK_GOLDEN_02 | bsi_new_york | swing high original level | wick sweep | none required | none | bare retest original level | beyond wick extreme | fixed 1:2 | Entry != wick tip |
| BSI2-GOLD-NY-02 | NEW_YORK_GOLDEN_05 | bsi_new_york | swing level | sweep no retest | none | none | none | none | none | No opportunity if target reached before retest |
| BSI2-GOLD-ABC-01 | ABC_GOLDEN_02 | bsi_abc | B-level context | C takes B | C-leg break | none | OB/FVG | beyond array | B-leg target | Valid ABC signal |
| BSI2-GOLD-ASIAN-01 | ASIAN_GOLDEN_02 | bsi_asian | Asian low | box low swept | MSS/CHoCH long | none | OB/FVG | below sweep/array | Asian high | Valid long, no daily-bias needed |
| BSI2-GOLD-ASIAN-02 | ASIAN_GOLDEN_04 | bsi_asian | Asian high | box high swept | bearish reversal | none | OB/FVG | above sweep/array | Asian low | Valid short |
| BSI2-GOLD-UO-01 | UNDER_OVER_GOLDEN_01 | bsi_under_over | 3-touch resistance | close break/reclaim | none | none | level reclaim | above fakeout | opposing level | Valid short only after close reclaim |
| BSI2-GOLD-UO-02 | UNDER_OVER_GOLDEN_02 | bsi_under_over | 3-touch support | close break/reclaim | none | none | level reclaim | below fakeout | opposing level | Valid long; partial evidence stored |
| BSI2-GOLD-930-01 | 0930_GOLDEN_02 | bsi_0930 | pre-930 high | sweep after 09:30 | displacement MSS | none | extreme FVG | beyond swept side | 3R-5R bounded | Valid NAS100 short |
| BSI2-GOLD-930-02 | 0930_GOLDEN_04 | bsi_0930 | equal highs | sweep | strong displacement substitute | none | OB substitute | beyond OB/sweep | bounded | Valid without clean MSS |
| BSI2-GOLD-RB-01 | REACTIONARY_GOLDEN_02 | bsi_reactionary | inherited OF liquidity | array1 mitigated | array2 no break | inherited/ambiguous | array2 | beyond array2 | natural target | Entry array id is second array |
| BSI2-GOLD-ABCD-01 | ABCD_GOLDEN_01 | bsi_abcd | B endpoint | D breaks P2 | completed ABC first | none | P2 retest | beyond D extreme | fixed 1:2 | Valid long after reclaim/retest |
| BSI2-GOLD-OBL-01 | OB_LIQUIDITY_GOLDEN_01 | bsi_ob_liquidity | origin OB edge | close fakeout/reclaim | internal ok | inherited/ambiguous | same origin OB | beyond OB | next imbalance/liquidity | Same array id throughout |

## Negative Golden Tests

| Fixture ID | Rule | Strategy | Expected Result |
|---|---|---|---|
| BSI2-NEG-OB-01 | Last-opposite generic OB must not satisfy mentor OB | bsi_order_flow/bsi_abc | Reject or select FVG-anchored mentor OB |
| BSI2-NEG-OTE-01 | 62-79 OTE must not gate BSI V2 | all non-PD strategies | Valid setup not rejected for OTE absence |
| BSI2-NEG-CHOCH-01 | Generic CHoCH severity split must not alter BSI MSS/MSB | BSI shared | BSI wrapper preserves mentor break kind |
| BSI2-NEG-NY-01 | Wick-tip cannot substitute original level | bsi_new_york | Signal entry equals original level, not wick extreme |
| BSI2-NEG-DUPE-01 | Same scheduler cycle replay cannot duplicate opportunity | all | Same `bsi_entry_opportunity_id` |
| BSI2-NEG-DUPE-02 | New MSB plus new array is a new opportunity | bsi_order_flow | New opportunity id |
| BSI2-NEG-GATE-01 | Unsupported universal PD gate | bsi_asian/bsi_new_york/bsi_under_over/bsi_0930 | No rejection for PD missing |
| BSI2-NEG-UO-01 | Wick beyond level does not count | bsi_under_over | Reject until close break/reclaim |
| BSI2-NEG-930-01 | Entry zone touched before 09:30 | bsi_0930 | Reject/expire |
| BSI2-NEG-OBL-01 | Heavy clean OB reaction | bsi_ob_liquidity | No OB Liquidity signal; route evidence to direct OF |
| BSI2-NEG-OBL-02 | Residual wick liquidity uncleared | bsi_ob_liquidity | Reject until cleared |
| BSI2-NEG-FRESH-01 | Stale setup processed minutes late | all executable strategies | Expire, no stop/TP manipulation |

## Before First Historical V2 Replay

Must pass:

- All version isolation tests.
- New York original-level tests.
- Mentor OB/FVG anchoring tests.
- Trendline liquidity tests for Order Flow.
- No-unsupported-PD tests.
- Duplicate opportunity lifecycle tests.
- Entry freshness/stale expiration tests.
- OB Liquidity residual/heavy-reaction negatives.
- V1 regression proving current `BSI_BASELINE_V1` outputs and fingerprints remain unchanged.
