# BSI V2 Production Methodology Promotion Report

Date: 2026-09-03

## Final Verdict

BSI_BASELINE_V2_AUDIOVISUAL is the intended forward BSI methodology, but it is NOT promoted to DEMO runtime in this pass.

Promotion is blocked by a HIGH correctness issue:

Real DB PIT replay executes all nine V2 evaluators, but fixture-dependent V2 subtype paths cannot yet produce real historical V2 occurrences from DB-built contexts.

No DEMO/live settings were changed.

## 1. Historical Data Source Found

Existing source:

- PostgreSQL 16 Docker container: `openterminalui-postgres-1`
- Database: `openterminalui`
- Host port: `127.0.0.1:5432`
- Data volume: `openterminalui_postgres_recovered_20260807`
- Runtime path: `docker-compose.yml` injects `DATABASE_URL` into the backend container.

The Codex shell initially had no `DATABASE_URL` because it was outside Docker Compose. The backend container did have `DATABASE_URL`.

## 2. PIT Replay Result

Real DB PIT replay was executed through `backend.historical_intelligence.bsi_v2_replay.replay_bsi_v2_grid`.

Bounded sample:

- points: 12
- contexts built: 12
- bars insufficient: 0
- source: `RECONSTRUCTED`
- all nine V2 subtypes evaluated
- executable V2 entries: 0

## 3. Future-Leakage Result

Existing `replay_at()` / `bars_as_of()` was used for point-in-time candle/context reconstruction.

Spot check at `2026-08-28T23:45:00Z`:

- M15 latest returned bar: `2026-08-28T23:00:00Z`
- H1 latest returned bar: `2026-08-28T20:30:00Z`
- H4 latest returned bar: `2026-08-28T11:30:00Z`

That candle boundary check passed.

Full V2-specific future-leakage proof is not complete because there are no real V2 occurrences to inspect for V2-specific liquidity, session, trendline, FVG, mentor OB, dealing-leg, and lifecycle evidence.

## 4. All-Nine Evaluation Funnel

12 real reconstructed contexts:

| Strategy | Evaluated | Valid Thesis | Entry Opportunity | Executable Opportunity | Main result |
|---|---:|---:|---:|---:|---|
| bsi_order_flow | 12 | 0 | 0 | 0 | PRICE_NOT_IN_ENTRY_ZONE |
| bsi_asian | 12 | 0 | 0 | 0 | NO_ASIAN_SETUP |
| bsi_new_york | 12 | 0 | 0 | 0 | NY_LIQUIDITY_NOT_SIGNIFICANT_SWING |
| bsi_abc | 12 | 0 | 0 | 0 | NO_ABC_GEOMETRY |
| bsi_under_over | 12 | 0 | 0 | 0 | NO_UNDER_OVER_LEVEL |
| bsi_0930 | 12 | 0 | 0 | 0 | NO_0930_SETUP |
| bsi_reactionary | 12 | 0 | 0 | 0 | NO_REACTIONARY_SEQUENCE |
| bsi_abcd | 12 | 0 | 0 | 0 | NO_ABCD_GEOMETRY |
| bsi_ob_liquidity | 12 | 0 | 0 | 0 | NO_OB_LIQUIDITY_SETUP |

This proves dispatch/evaluation, not historical validity.

## 5. V2 Historical Statistics

Not produced.

A full V2 historical backfill would currently create a misleading zero/near-zero corpus because six V2 strategy paths still require test-only fixture geometry:

- `bsi_abc`
- `bsi_asian`
- `bsi_under_over`
- `bsi_0930`
- `bsi_reactionary`
- `bsi_abcd`
- `bsi_ob_liquidity`

These paths reject real DB contexts before real opportunity creation.

## 6. Actual V2 Trading Frequency

Unknown for the full historical corpus.

Observed bounded PIT sample:

- 0 executable opportunities across 12 contexts
- not valid as a frequency estimate

## 7. Representative Methodology Evidence

Video-derived behavior is proven in semantic golden tests.

Real historical examples did not confirm behavior because no real V2 occurrences were produced in the bounded PIT sample.

Missing real-evidence proofs:

- New York original liquidity level vs sweep extreme
- Order Flow trendline-liquidity material usage
- Order Flow mentor OB/FVG material usage
- ABC geometry on real data
- Asian session sweep/MSS on real data
- Under/Over touches/fakeout/reclaim on real data
- 9:30 NY window and lower-timeframe relationship on real data
- Reactionary array1/reaction/array2 on real data
- ABCD sequence on real data
- OB Liquidity same-array fakeout/reclaim on real data

## 8. Regression Results

Previously run selected regression:

- V2 readiness: `3 passed`
- V2 suite: `40 passed`
- core MT5/shared checkpoint: `107 passed`
- broader BSI/MT5/cTrader smoke: `124 passed, 2 skipped`

No full repo-wide regression was run after DB discovery because promotion stopped on the HIGH V2 replay adapter coverage issue.

## 9. Adaptive Manager Decision

Adaptive Manager is not approved for V2 positions.

Reason:

- V2 historical positions do not yet exist.
- V2 thesis/invalidation/target semantics have not been verified through real DEMO/historical lifecycle.
- Legacy management must not replace mentor invalidation or close V2 positions from legacy strategy assumptions.

Initial V2 DEMO, when gates pass later, should use baseline mentor-defined management unless a V2-aware Adaptive Manager path is explicitly proven.

## 10. Confidence/HI Decision

Legacy confidence/HI is not approved as a hard V2 gate.

Reason:

- V2 corpus is not generated.
- BSI_HI_V2 / BSI_CONFIDENCE_V2 are not calibrated.
- V1/legacy intelligence must not veto V2 mentor-valid setups.

## 11. Legacy Candidate-Generation Removal

Not performed.

Current runtime still registers legacy and non-BSI strategy evaluators in `backend/mt5_strategies/families/__init__.py`.

Removing them from candidate generation before V2 real-data validation passes would leave the runtime with no proven active candidate path.

## 12. V2 Active Runtime Configuration

Not enabled.

Current V2 remains isolated research dispatch, not active production candidate generation.

## 13. MT5 DEMO Status

MT5 DEMO activation was not changed.

BSI V2 was not enabled for MT5 DEMO.

## 14. cTrader DEMO Status

cTrader DEMO activation was not changed.

BSI V2 was not enabled for cTrader DEMO.

## 15. Live-Money Protection Status

Live-money enablement was not changed.

Required live-money protection remains:

- `MT5_LIVE_TRADING_ENABLED=false`
- cTrader adapter refuses LIVE account mutation unless verified demo conditions pass.

## 16. Remaining Issues

HIGH:

- V2 real-data replay adapter coverage incomplete for fixture-dependent strategy geometry.

MEDIUM:

- Full V2-specific future-leakage proof cannot complete until real V2 occurrences exist.
- Full V2 historical statistics/frequency cannot be produced until V2 real-data backfill is valid.
- Adaptive Manager V2 safety remains unproven.
- BSI_HI_V2 / BSI_CONFIDENCE_V2 remain unbuilt and uncalibrated.

LOW:

- Existing V1 corpus remains useful for audit/reference but is not the forward methodology.

## Promotion Gate

Promotion gate result: FAILED.

Reason:

The directive requires real PIT replay, no future leakage, all nine evaluators genuinely execute, evidence correctness, historical validation, risk/execution regression, and no CRITICAL/HIGH correctness defect.

The HIGH replay adapter coverage defect means DEMO activation must stop here.

NO BSI V2 SETUP = NO TRADE remains the intended runtime rule after the blocker is fixed.

## Final Answers

1. Did real V2 PIT replay pass? Mechanically yes on bounded real DB samples; promotion validation no, because no real V2 opportunities were produced.
2. Is future leakage ruled out? Partially for bars; not fully for V2-specific objects because no real V2 occurrences exist.
3. Did all nine strategies evaluate correctly? They all dispatched/evaluated. Six fixture-dependent paths are not yet real-data complete.
4. Unique opportunities/trades by strategy? Bounded PIT sample: 0 for every strategy.
5. Combined V2 trade frequency? Unknown; bounded sample 0 is not a valid corpus estimate.
6. Historically positive/negative/insufficient? Unknown for V2. No full V2 corpus exists.
7. Did real historical examples confirm video behavior? No real V2 occurrences were produced to inspect.
8. Did full regression pass? Selected regressions passed earlier; full regression was not rerun after promotion stopped.
9. Is V2 now the only active BSI trading methodology? No.
10. Are all legacy strategies excluded from candidate generation? No.
11. Is legacy confidence/HI excluded from V2 hard gating? V2 is not active; decision is to exclude legacy confidence/HI when V2 activation becomes safe.
12. Is Adaptive Manager safe for V2, or disabled? Not proven safe; should be disabled/baseline-only for initial V2 DEMO when gates pass.
13. Is V2 enabled on MT5 DEMO? No.
14. Is V2 enabled on cTrader DEMO? No.
15. Is live-money execution still disabled? No live-money setting was changed.
16. Any CRITICAL/HIGH correctness blockers? Yes: HIGH V2 real-data replay adapter coverage blocker.
