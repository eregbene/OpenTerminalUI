# BSI V2 Final Demo Readiness Report

Date: 2026-09-03

## Final Verdict

BSI_BASELINE_V2_AUDIOVISUAL is NOT DEMO READY.

## What Passed

- V2 remains isolated from production dispatch.
- V1 BSI path was not replaced.
- Durable lifecycle research store implemented.
- Restart duplicate-consumption protection tested.
- Replay-run lifecycle scoping tested.
- Account execution identity kept separate from methodology opportunity identity.
- All nine V2 subtypes dispatch through the isolated research engine.
- All-nine funnel reporting exists.
- Operational confidence test environment normalized.
- Existing historical Postgres database located.
- Real DB connection works.
- Bounded real PIT replay mechanically executes all nine V2 evaluators.
- `bars_as_of(T)` spot check returned only bars at or before T.

## Test Evidence

`python -m pytest backend/tests/test_bsi_v2_validation_readiness.py -q`

Result: `3 passed`

`python -m pytest backend/tests/test_confidence_calibration.py::test_production_threshold_matches_current_operational_value backend/tests/test_mt5_multi_strategy.py::test_confidence_threshold_matches_current_operational_value backend/tests/test_mt5_multi_strategy_optimization.py::test_confidence_threshold_matches_current_operational_value -q`

Result: `3 passed`

`python -m pytest backend/tests/test_bsi_v2_foundation.py backend/tests/test_bsi_v2_new_york.py backend/tests/test_bsi_v2_all_strategies.py backend/tests/test_bsi_v2_validation_readiness.py -q`

Result: `40 passed`

`python -m pytest backend/tests/test_bsi_engine.py backend/tests/test_market_structure_phase5_engine.py backend/tests/test_market_structure_phase5_api.py backend/tests/test_mt5_strategy_stop_construction.py backend/tests/test_risk_calculator.py -q`

Result: `107 passed`

`python -m pytest backend/tests/test_bsi_canonical_fingerprint.py backend/tests/test_bsi_hi_fingerprint.py backend/tests/test_bsi_replay.py backend/tests/test_mt5_multi_strategy.py backend/tests/test_mt5_multi_strategy_optimization.py backend/tests/test_mt5_multi_account_risk_protection_fix.py backend/tests/test_ctrader_risk_sizing.py -q`

Result: `124 passed, 2 skipped`

## What Did Not Pass

- Real DB PIT replay produced no V2 occurrences.
- Historical golden cross-check was not possible because no real V2 occurrences were produced.
- Full V2 historical backfill was not executed.
- Real all-nine sample funnel counts were produced, but full historical funnel was not.
- V1/V2 performance and frequency comparison was not produced.

## Blocking Cause

The original DB access blocker is resolved.

The current blocker is V2 replay adapter coverage: six V2 subtypes still depend on test-only `bsi_v2_fixture` geometry and cannot yet generate real historical V2 occurrences from raw DB contexts.

## Required Before DEMO

1. Implement real DB context geometry extraction for fixture-dependent V2 subtypes without changing mentor rules.
2. Rerun small PIT replay until representative real V2 occurrences are produced.
3. Confirm no future leakage for V2-specific structure/session/liquidity objects.
4. Run full V2 historical backfill only after the PIT gate passes.
5. Produce V1/V2 performance, frequency, NY before/after, and Order Flow validation from the backfilled corpus.

## Live/DEMO Status

DEMO/live enablement was not changed.

Recommendation: keep BSI V2 disabled.

## Final Answers

1. Durable lifecycle/restart dedup proven? Yes for the implemented durable research store: restart consumed-state dedup and replay-run scoping are tested.
2. Configuration environment deterministic? Yes for pytest. Runtime backend still intentionally has `MT5_MIN_TRADE_CONFIDENCE=0`; tests normalize absent/zero local values to the documented 55.0 expectation.
3. DB point-in-time replay passed? Mechanically yes on bounded samples; validation gate no, because it produced no real V2 occurrences.
4. Future leakage ruled out? Partially. `bars_as_of(T)` spot check passed. Full V2-specific object leakage cannot be ruled out until real V2 occurrences exist.
5. All nine strategies dispatch and evaluate? Yes in isolated V2 research dispatch and funnel tests.
6. Unique V2 opportunities by strategy? In the 12-context real PIT sample: 0 for every subtype.
7. How often does BSI V2 trade? Unknown for the full corpus. The bounded sample produced 0 executable entries.
8. How much duplication disappeared vs V1? Unknown until a valid full V2 corpus exists.
9. What changed most between V1 and V2? Code-level changes are durable lifecycle/freshness, mentor audiovisual semantics, original-level handling, mentor OB/FVG evidence, trendline liquidity, and no OTE/generic OB insertion. Quantified historical impact is blocked.
10. NY original-level correction impact? Not quantified; the bounded V2 PIT sample produced no NY V2 occurrence.
11. Trendline liquidity materially used by Order Flow? Not proven historically. Unit-golden proves code path only.
12. Mentor OBs materially used? Not proven historically. Unit-golden proves code path only.
13. Historically strongest under V2? Unknown without backfill outcomes.
14. Historically weakest? Unknown without backfill outcomes.
15. Insufficient sample strategies? Unknown; rare-strategy coverage requires real corpus counts.
16. Concentrated by symbol/month/direction? Unknown without backfill outcomes.
17. V2 corpus isolated for future HI/confidence learning? Prepared by versioned V2 evidence and lifecycle identities; no activation/calibration done.
18. Full regression pass? Broad practical regression passed in selected suites: 3 + 40 + 107 + 124 passed, 2 skipped. Entire repo-wide pytest was not run after DB discovery.
19. Critical/HIGH correctness issues? Yes: HIGH blocker is incomplete real-data V2 replay adapter coverage for fixture-dependent subtypes. No new test regression remains unexplained in the suites run.
20. Is BSI_BASELINE_V2_AUDIOVISUAL DEMO_READY? No.
21. If yes, activation sequence? Not applicable.
22. If no, blockers? Build real DB geometry extraction for fixture-dependent V2 subtypes, rerun all-nine PIT replay with real occurrences, verify V2-specific no-leakage, run historical golden cross-check, execute full V2 backfill, produce V1/V2 frequency/performance/coverage comparison.
