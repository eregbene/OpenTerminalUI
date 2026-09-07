# BSI V2 Phase 1 Foundation Implementation Report

Target version: `BSI_BASELINE_V2_AUDIOVISUAL`.

## Commits

No commits were created. The working tree already contains unrelated/untracked BSI and historical-intelligence work, so this phase was kept as file-level changes only.

Recommended split remains:

1. Version scaffold + typed primitives + V1 isolation tests.
2. Mentor structure/FVG/OB interpretation.
3. V2 liquidity + trendline liquidity.
4. Mentor dealing leg / 50% PD.
5. Thesis/opportunity identity + evidence model.

## Files Changed

New files:

- `backend/mt5_strategies/families/bsi_v2_scaffold.py`
- `backend/mt5_strategies/families/bsi_v2_primitives.py`
- `backend/mt5_strategies/families/bsi_v2_interpretation.py`
- `backend/tests/test_bsi_v2_foundation.py`
- `docs/bsi_v2_video_reconstruction/BSI_V2_PHASE1_FOUNDATION_IMPLEMENTATION_REPORT.md`

No existing evaluator, adaptive-manager, risk, broker, or market-structure implementation file was modified for this phase.

## New Modules And Classes

`bsi_v2_scaffold.py`

- `BSI_BASELINE_V1`
- `BSI_BASELINE_V2_AUDIOVISUAL`
- `BSI_V2_ENV_FLAG`
- `bsi_v2_enabled()`

`bsi_v2_primitives.py`

- `BSIEvidenceClass`
- `BSIMentorStructureKind`
- `BSILiquidityType`
- `BSILiquidityStatus`
- `BSIPremiumDiscount`
- `BSIRuleEvidence`
- `BSIMentorStructureEvent`
- `BSIMentorFVGRelation`
- `BSIMentorOrderBlock`
- `BSITrendlineGeometry`
- `BSILiquidityObject`
- `BSILiquidityTakenEvent`
- `BSIDealingLeg`
- `BSIEntryArray`
- `BSIIdentitySeed`
- `BSIV2Evidence`
- `build_bsi_thesis_id()`
- `build_bsi_entry_opportunity_id()`

`bsi_v2_interpretation.py`

- `interpret_mentor_structure()`
- `build_mentor_fvg_relation()`
- `build_mentor_order_block_from_fvg()`
- `liquidity_from_level()`
- `liquidity_from_session_level()`
- `liquidity_from_trendline()`
- `build_liquidity_taken_event()`
- `build_dealing_leg()`

## Reused Components

- `backend.market_structure.models.StructureBreak`
- `backend.market_structure.models.ImbalanceZone`
- `backend.market_structure.models.LiquidityLevel`
- `backend.market_structure.models.SessionLevel`
- `backend.market_structure.models.SwingPoint`
- `backend.market_structure.models.stable_id`
- `backend.market_structure.bar_utils.StructureBar`
- `backend.market_structure.pivot_trendlines.TrendlinePivotSummary`

Generic market-structure behavior was not rewritten.

## V1 Isolation Proof

V1 remains isolated because:

- Existing `backend/mt5_strategies/families/bsi_engine.py` was not modified.
- Existing `BSI_VERSION` remains `BSI_BASELINE_V1`.
- V2 is not registered in `EVALUATORS`.
- `bsi_v2_enabled()` defaults false.
- Existing BSI V1 tests passed.

## Mentor Structure Implementation

Implemented mentor structure interpretation:

- continuation direction unchanged -> `MSB`
- direction changes -> `MSS`
- raw CHoCH can be used as source evidence but is not exposed as a separate BSI V2 mentor state

Persisted evidence fields include source break ID, direction before/after, mentor classification, swing ID, break level, break price, bar index, time, and rule IDs.

## Mentor FVG Implementation

Implemented `BSIMentorFVGRelation` over existing raw 3-candle FVGs.

Preserved:

- FVG ID
- candle 1
- impulse candle
- candle 3
- bounds
- direction
- mitigation state
- source rule IDs

No new numeric FVG threshold was introduced.

## Mentor OB Implementation

Implemented FVG-anchored mentor OB:

- selected candle = FVG candle 1
- bounds = selected candle high/low/body-extreme-inclusive range
- ID includes symbol, timeframe, FVG ID, candle index, bounds, and direction
- rejected competing generic OB IDs can be persisted for negative tests

Generic `market_structure/zones.py::detect_order_blocks()` remains untouched.

## Liquidity Implementation

Implemented `BSILiquidityObject` with supported categories:

- structural swing liquidity
- equal-level liquidity
- session box edge liquidity
- trendline liquidity
- origin OB edge
- residual wick liquidity

Implemented `BSILiquidityTakenEvent` with separate:

- `original_liquidity_level`
- `sweep_extreme`

## Trendline Liquidity Implementation

Implemented BSI-owned trendline liquidity interpretation over raw `TrendlinePivotSummary`.

Preserved:

- anchor 1 / anchor 2 IDs
- anchor bar indexes
- anchor prices
- slope
- intercept
- projected reference level
- active/taken status
- touch count and break information in geometry evidence

Diagonal liquidity is not flattened into a horizontal level.

## Premium/Discount Implementation

Implemented `BSIDealingLeg`:

- break-causing source break ID
- start/end anchors
- start/end prices
- midpoint
- `PREMIUM`
- `DISCOUNT`
- `AT_EQUILIBRIUM`

No generic OTE or 62-79% dependency is used.

## Identity Implementation

Implemented stable engineering identities:

- `bsi_thesis_id`
- `bsi_entry_opportunity_id`

Identity uses stable structural evidence, not scheduler timestamp.

Tested behavior:

- same structural event + same entry array -> same opportunity ID
- same thesis + new entry array -> new opportunity ID

## Persisted Evidence Model

Implemented optional `BSIV2Evidence` container for future strategy persistence.

It can carry:

- version
- subtype
- rule IDs
- structure
- relevant swings
- liquidity
- liquidity taken event
- dealing leg
- premium/discount
- FVG
- mentor OB
- entry array
- thesis ID
- entry opportunity ID
- invalidation
- target semantics
- engineering adjustments

Fields are optional so strategy-specific absence is not fabricated.

## Tests Added

`backend/tests/test_bsi_v2_foundation.py`

Covers:

- V2 version exists and is disabled by default.
- V1 engine version remains `BSI_BASELINE_V1`.
- V2 is not registered in live evaluator dispatch.
- continuation -> mentor MSB.
- reversal -> mentor MSS.
- raw CHoCH does not become a separate mentor methodology state.
- 3-candle FVG relationship is preserved.
- FVG-anchored mentor OB selection.
- generic last-opposite-candle candidate rejected as automatic BSI V2 OB.
- structural/session/trendline liquidity objects.
- original liquidity level differs from sweep extreme.
- 50% PD midpoint without OTE dependency.
- stable thesis/opportunity identity.
- optional V2 evidence fields.

## Tests Run

New foundation suite:

- `python -m pytest backend/tests/test_bsi_v2_foundation.py -q`
- Result: `10 passed`

Existing BSI V1 suite:

- `python -m pytest backend/tests/test_bsi_engine.py -q`
- Result: `60 passed`

Relevant market-structure/execution/risk regression:

- `python -m pytest backend/tests/test_market_structure_phase5_engine.py backend/tests/test_market_structure_phase5_api.py backend/tests/test_mt5_strategy_stop_construction.py backend/tests/test_risk_calculator.py -q`
- Result: `47 passed`

Total run in this phase: `117 passed`.

Warnings:

- Pydantic deprecated `json_encoders` warnings from existing dependencies.
- `TEST_DATABASE_URL` not set notice; no failing DB integration test was run.

## Regressions

None observed in the suites run.

## Unresolved Ambiguities

Preserved for later strategy migration:

- Order Flow MSB premium/discount status.
- Reactionary array-2 OB-vs-FVG selection.
- ABCD close-based D break.
- ABCD optional structure/1:3 target.
- Reactionary/OB Liquidity inherited premium/discount.

## Deferred To Strategy Migration

Not implemented in Phase 1:

- V2 evaluator dispatch.
- Any of the nine strategy rewrites.
- New York original-level fix inside live evaluator.
- Order Flow trendline-liquidity trading logic.
- OB Liquidity residual-liquidity and heavy-reaction gating inside evaluator.
- Adaptive Manager wiring.
- Confidence changes.
- Historical backfill.
- DEMO or production activation.
