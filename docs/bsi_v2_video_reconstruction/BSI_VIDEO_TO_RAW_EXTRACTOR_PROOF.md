# BSI Video To Raw Extractor Proof

## Status

Partial proof only. The local videos provide chart visuals and transcripts, but not exact OHLC bar exports from the demonstrated charts. Therefore the same DB/live raw extractor cannot be proven bar-for-bar against the mentor examples without reconstructing or sourcing those chart bars.

## What Is Proven

- Under/Over: raw extractor shape matches close fakeout -> reclaim -> retest over >=3-touch level.
- 9:30: raw extractor correctly refuses FX/gold replay because index/M1 data is required.
- Reactionary: raw extractor recognizes array1 -> reaction -> array2, but currently over-accepts because impulse and parent Order Flow proof are weak.
- ABCD: raw extractor recognizes opposite-side D purge logic, but currently needs stricter ABC-before-D proof.
- OB Liquidity: raw extractor recognizes same-array close fakeout -> reclaim -> retest, but needs stronger local-extremum/residual-liquidity proof.

## Not Yet Proven

No target video example has been reproduced through the live DB extractor from actual OHLC bars matching the video chart. Existing fixtures remain regression tests, not source-proof.

## Required Next Step

Create bar-reconstruction fixtures only from visible chart OHLC or obtain exported TradingView/market data for each demonstrated timestamp/symbol. Then run the same `evaluate_bsi_v2_subtype` path with fixture shortcuts disabled.
