# BSI V2 Zero-Strategy Root Cause Report

Window audited: August 1, 2026 00:00 UTC to September 1, 2026 00:00 UTC.

## Finding

The zero-firing result was not genuine methodology rarity for most subtypes.

Seven paths were fixture-dependent:

| Strategy | Root cause |
|---|---|
| `bsi_asian` | Required `m15_snapshot.bsi_v2_fixture["asian"]`; no real replay/live producer existed. |
| `bsi_abc` | Required `m15_snapshot.bsi_v2_fixture["abc"]`; no raw swing-leg extractor existed. |
| `bsi_abcd` | Required `m15_snapshot.bsi_v2_fixture["abcd"]`; no raw ABCD/liquidity-purge extractor existed. |
| `bsi_under_over` | Required `m15_snapshot.bsi_v2_fixture["under_over"]`; equal-level/fakeout/reclaim was not extracted. |
| `bsi_0930` | Required fixture fields and is index scoped; August universe had no index symbols. |
| `bsi_reactionary` | Required `m15_snapshot.bsi_v2_fixture["reactionary"]`; no array1 reaction into array2 extractor existed. |
| `bsi_ob_liquidity` | Required `m15_snapshot.bsi_v2_fixture["ob_liquidity"]`; no same-OB fakeout/reclaim extractor existed. |

`bsi_new_york` was raw-data based, but rejected real swing liquidity because Bensim emits `source="confirmed_swing"` and the evaluator accepted only `swing`, `structural_swing`, or `mentor_swing`.

## Fixes

- Added raw fallback extraction in `backend/mt5_strategies/families/bsi_v2_engine.py`.
- Preserved golden fixture path for tests.
- Fixed New York significant swing source acceptance in `backend/mt5_strategies/families/bsi_v2_new_york.py`.
- Rechecked original videos and tightened New York, Asian, Under/Over, and OB Liquidity sequencing to match mentor examples.
- Kept 9:30 blocked on FX/gold as `EXPECTED_INSTRUMENT_SCOPE`.

## After-Fix August Result

Total valid setups after the stricter video recheck: `1,967`.

| Subtype | Setups |
|---|---:|
| `bsi_order_flow` | 184 |
| `bsi_new_york` | 412 |
| `bsi_ob_liquidity` | 114 |
| `bsi_reactionary` | 1,134 |
| `bsi_abcd` | 78 |
| `bsi_under_over` | 17 |
| `bsi_abc` | 16 |
| `bsi_asian` | 12 |
| `bsi_0930` | 0 |

9:30 zero is expected for this universe because there were no NAS100/US30/SPX-type symbols in the test.
