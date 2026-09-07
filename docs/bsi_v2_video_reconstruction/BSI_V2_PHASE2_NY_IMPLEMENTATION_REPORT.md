# BSI V2 Phase 2 NY Implementation Report

Target version: `BSI_BASELINE_V2_AUDIOVISUAL`.

## Commits

No commits were created. The repository already has unrelated dirty/untracked work, so this phase stayed as scoped file changes.

Preferred future split:

1. lifecycle + dedup tests
2. entry freshness tests
3. New York V2 evaluator
4. NY golden/regression evidence

## Files Changed

New:

- `backend/mt5_strategies/families/bsi_v2_lifecycle.py`
- `backend/mt5_strategies/families/bsi_v2_new_york.py`
- `backend/tests/test_bsi_v2_new_york.py`
- `docs/bsi_v2_video_reconstruction/BSI_V2_PHASE2_NY_IMPLEMENTATION_REPORT.md`

Updated:

- `backend/mt5_strategies/families/bsi_v2_primitives.py`

No V1 evaluator file was modified. No production dispatch, DEMO activation, Adaptive Manager, confidence, broker, risk, or historical backfill behavior was changed.

## Lifecycle Implementation

Implemented `BSILifecycleStore` with explicit transition persistence in memory.

States:

- `DETECTED`
- `THESIS_CREATED`
- `ENTRY_ARMED`
- `ENTRY_AVAILABLE`
- `CONSUMED`
- `INVALIDATED`
- `EXPIRED`

Implemented:

- stable thesis/opportunity lookup from Phase 1 IDs
- transition log via `BSILifecycleTransition`
- consumed opportunity blocking
- expired/invalidated opportunity blocking
- stable opportunity identity independent of scheduler timestamp
- new structural/liquidity seed creates a new opportunity

Lifecycle semantics are explicitly `BENSIM_ENGINEERING`.

## Freshness Implementation

Implemented `evaluate_bare_retest_freshness()`.

It separates:

- mentor setup validity
- mentor entry validity
- execution viability
- broker/risk viability

It checks:

- current bid/ask against original retest level
- retest tolerance
- mentor invalidation geometry
- broker minimum stop constraint
- fixed RR viability against planned mentor target

It does not rescue stale trades by moving the mentor level, moving invalidation, widening after entry, lowering RR, or substituting liquidity.

Freshness decisions:

- `AVAILABLE`
- `WAIT`
- `EXPIRE`

## New York V2 Evaluator

Implemented isolated research evaluator:

- `evaluate_bsi_v2_new_york()`

It is not registered in `EVALUATORS`.

Implemented mentor sequence:

1. resolve latest NY swing sweep
2. resolve `sweep.level_id` back to original `LiquidityLevel`
3. require liquidity source to be structural swing-like
4. determine direction from sweep side
5. require later retest of original liquidity level
6. keep sweep wick extreme separate
7. run fresh quote viability
8. emit bare-retest V2 signal
9. persist V2 evidence and lifecycle IDs
10. mark opportunity consumed after executable signal emission

## Original-Level Fix Proof

V2 uses:

- `level.level` as `liquidity_level`
- `sweep.swept_price` as `sweep_wick_extreme`
- retest checked against `level.level`
- mentor invalidation anchored to sweep extreme

Test proves a candle touching only the sweep wick extreme does not qualify as retest.

## Evidence Schema Usage

NY V2 evidence includes:

- `bsi_version`
- `setup_subtype`
- `mentor_rule_ids`
- `bsi_thesis_id`
- `bsi_entry_opportunity_id`
- `entry_type`
- `liquidity_id`
- `source_liquidity_level_id`
- `liquidity_level`
- `sweep_wick_extreme`
- `retest_bar_index`
- `retest_price`
- `entry_price`
- `mentor_invalidation_level`
- `raw_structural_stop`
- `broker_min_stop`
- `final_stop`
- `constraint_source`
- `freshness_status`
- `freshness_reason`
- `lifecycle_state`

## Golden Tests

Added `backend/tests/test_bsi_v2_new_york.py`.

Covers:

- original-level retest positive
- wick-tip substitution negative
- no OB/FVG requirement
- valid wick sweep without close-break requirement
- invalid liquidity source negative
- stable opportunity ID across repeated scans
- no duplicate executable candidate on repeated scan
- new NY setup creates new opportunity ID
- stale entry waits/fails closed
- RR destroyed by quote drift expires
- broker stop constraint persisted separately
- V1 NY function/version unchanged
- V2 not registered in production dispatch

## Regression Results

Command:

`python -m pytest backend/tests/test_bsi_v2_foundation.py backend/tests/test_bsi_v2_new_york.py backend/tests/test_bsi_engine.py backend/tests/test_market_structure_phase5_engine.py backend/tests/test_market_structure_phase5_api.py backend/tests/test_mt5_strategy_stop_construction.py backend/tests/test_risk_calculator.py -q`

Result:

- `129 passed`

Warnings:

- existing Pydantic `json_encoders` deprecation warnings
- `TEST_DATABASE_URL` unset notice

## V1 Isolation Proof

- `backend/mt5_strategies/families/bsi_engine.py` not modified.
- Existing `test_bsi_engine.py` passed: `60 passed`.
- `BSI_VERSION` remains `BSI_BASELINE_V1`.
- V2 NY evaluator is imported directly by tests only.
- `bsi_v2_enabled()` remains false by default.
- No `bsi_v2` registration in `EVALUATORS`.

## Unresolved NY Ambiguities

- Deterministic tie-breaker among multiple equally significant swing levels is not video-established.
- Numeric significant-swing threshold is not video-established.
- Exact small-fakeout quantitative cutoff is not video-established.
- Pair allowlist is visually shown but not implemented as a hard gate in Phase 2.
- Exact New York session filtering is not implemented in this isolated V2 evaluator yet; tests use NY-shaped sweep fixtures.

## BENSIM_ENGINEERING Rules

- lifecycle state machine
- opportunity consumption after executable signal emission
- retest tolerance
- broker minimum stop constraint representation
- in-memory transition persistence for Phase 2 tests
- freshness `WAIT` versus `EXPIRE` distinction

## Methodology Correctness

New York V2 is methodology-correct for the critical reconstructed mentor rules:

- original liquidity level is used for retest
- sweep wick extreme is separate
- bare retest is used
- no OB/FVG/MSS/PD is required
- fixed 1:2 RR is enforced
- stale drift fails closed without changing mentor geometry

## Controlled Historical Replay Readiness

Ready for a small controlled point-in-time NY V2 replay fixture after adding durable lifecycle persistence or a replay-scoped lifecycle adapter.

Not ready for DEMO/live dispatch.

## Deferred

- Durable DB-backed lifecycle persistence.
- Exact NY session window gate in V2 evaluator.
- Significant-swing scoring/selection beyond source filtering.
- Pair allowlist/evidence mode.
- Historical backfill.
- Any migration of the other eight BSI strategies.
