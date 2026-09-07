# BSI V2 Remaining Strategies Implementation Report

Target version: `BSI_BASELINE_V2_AUDIOVISUAL`.

## Scope Completed

Migrated the eight remaining strategies into an isolated V2 research engine:

- `bsi_order_flow`
- `bsi_abc`
- `bsi_asian`
- `bsi_under_over`
- `bsi_0930`
- `bsi_reactionary`
- `bsi_abcd`
- `bsi_ob_liquidity`

New York remains in its Phase 2 V2 evaluator and is included in the V2 research dispatcher.

## Files Changed

New:

- `backend/mt5_strategies/families/bsi_v2_engine.py`
- `backend/tests/test_bsi_v2_all_strategies.py`
- `docs/bsi_v2_video_reconstruction/BSI_V2_REMAINING_STRATEGIES_IMPLEMENTATION_REPORT.md`
- `docs/bsi_v2_video_reconstruction/BSI_V2_ALL_STRATEGIES_GOLDEN_TEST_REPORT.md`
- `docs/bsi_v2_video_reconstruction/BSI_V2_CONTROLLED_REPLAY_REPORT.md`

Updated:

- `backend/mt5_strategies/families/bsi_v2_lifecycle.py`

No V1 BSI evaluator was modified. No production dispatch was activated.

## V2 Architecture

Implemented isolated research path:

`raw market_structure objects -> bsi_v2_interpretation -> bsi_v2_engine subtype evaluator -> bsi_v2_lifecycle/freshness -> StrategySignal`

Research dispatcher:

- `BSI_V2_RESEARCH_EVALUATORS`
- `evaluate_bsi_v2_subtype()`
- `evaluate_bsi_v2_research()`

The dispatcher contains exactly nine V2 subtypes and no legacy strategies.

## Strategy Implementations

`bsi_order_flow`

- consumes mentor MSB/MSS
- consumes FVG/mentor OB
- consumes trendline liquidity when present
- applies 50% PD for MSS
- preserves MSB PD ambiguity
- emits lifecycle/freshness-aware opportunity
- supports new MSB/new array as a new opportunity

`bsi_abc`

- uses explicit A/B/C mentor geometry input
- rejects B-leg invalidation
- requires FVG/mentor OB entry
- targets B-leg extreme
- no PD gate

`bsi_asian`

- uses Asian session box target
- requires session-level target
- no Daily/HTF hard gate
- no PD gate
- emits full-close target semantics

`bsi_under_over`

- requires >=3 touches
- rejects wick-only break
- uses bare reclaimed-level entry
- persists staged partial/final-close management style
- no MSS/PD/FVG-entry requirement

`bsi_0930`

- requires 9:30-window evidence
- requires index scope
- requires displacement MSS or strong displacement substitute
- supports OB substitute path
- enforces at least 3R target viability
- no PD gate

`bsi_reactionary`

- requires array 1 and array 2 evidence
- enters array 2 only
- persists reaction event
- preserves array-2 kind ambiguity

`bsi_abcd`

- uses full ABCD geometry evidence
- enters retest of P2/B-leg endpoint
- defaults to fixed 1:2
- preserves close-based D-break and optional 1:3/structure-target ambiguities

`bsi_ob_liquidity`

- uses same origin OB array
- rejects heavy clean reaction
- rejects uncleared residual liquidity
- rejects huge fakeout evidence
- persists same-array fakeout/reclaim evidence

## Shared V2 Rules Applied

- lifecycle IDs
- opportunity dedup
- freshness check before executable signal
- mentor invalidation separate from final protective stop
- subtype-specific targets
- optional evidence fields
- V2 version stamping

## V1 Isolation

V1 remained untouched in this phase:

- `backend/mt5_strategies/families/bsi_engine.py` was not modified.
- `backend/mt5_strategies/families/__init__.py` was not modified for V2 registration.
- `backend/mt5_strategies/models.py` was not modified for V2 activation.
- Existing BSI V1 regression passed inside the combined checkpoint.

## Tests

New all-strategy suite:

- `backend/tests/test_bsi_v2_all_strategies.py`

V2-only tests:

- `37 passed`

Core V2/V1/shared checkpoint:

- `144 passed`

Broader smoke:

- `122 passed, 2 skipped, 2 failed`
- failures classified as `ENVIRONMENTAL/PRE_EXISTING`: `mt5_config().min_trade_confidence` is `0.0` in this shell, while two operational config tests expect `55.0`
- no V2 code touches confidence thresholds

## Historical Backfill

Not run. Gate not met because durable V2 occurrence persistence/backfill tooling is not implemented in this phase and the broader smoke suite had environmental config failures.

## DEMO

Not activated. Not ready for DEMO.

## Remaining High-Risk Work

- durable lifecycle persistence
- point-in-time V2 replay adapter
- real OHLC video-derived fixtures instead of synthetic semantic fixtures
- full historical V2 backfill
- V1 vs V2 historical comparison
- DEMO readiness gate
