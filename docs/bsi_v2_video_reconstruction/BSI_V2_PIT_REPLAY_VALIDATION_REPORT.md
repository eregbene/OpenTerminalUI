# BSI V2 PIT Replay Validation Report

Date: 2026-09-03

## Verdict

Point-in-time replay harness is implemented for isolated BSI V2 research evaluation, and real DB PIT replay mechanically passed on a bounded sample.

The sample produced zero V2 executable entries because most V2 subtype evaluators still depend on synthetic `bsi_v2_fixture` geometry rather than deriving all subtype geometry from real point-in-time DB contexts.

## Implemented

- Added `backend/historical_intelligence/bsi_v2_replay.py`.
- Reuses existing `historical_intelligence.replay.replay_at()` for point-in-time candle/context reconstruction.
- Calls the isolated BSI V2 evaluators directly.
- Does not register BSI V2 in production `evaluate_all()`.
- Aggregates all-nine strategy funnel counts.
- Supports durable lifecycle replay via `lifecycle_path` and `replay_run_id`.

## No Future Leakage Boundary

The new V2 PIT entry point delegates historical bar selection to the existing `replay_at()` implementation, which already enforces closed-bar point-in-time reconstruction via `bars_as_of()`.

BSI V2 does not add its own candle loader.

## Database Used

- Container: `openterminalui-postgres-1`
- Database: `openterminalui`
- Host access: `127.0.0.1:5432`
- Source tables: `mt5_canonical_candles`, `mt5_candle_revisions`
- Result tables inspected read-only: `historical_pattern_fingerprints`, `historical_setup_outcomes`

## Validation Performed

Synthetic/in-process replay funnel validation:

`python -m pytest backend/tests/test_bsi_v2_validation_readiness.py -q`

Result:

`3 passed`

Real 4-point PIT replay:

- status: OK
- contexts: 4
- bars_insufficient: 0
- source: `RECONSTRUCTED`
- all nine V2 subtypes evaluated
- executable entries: 0

Real 12-point PIT replay:

- points: 12
- contexts: 12
- bars_insufficient: 0
- source: `RECONSTRUCTED`
- all nine V2 subtypes evaluated
- executable entries: 0

No-lookahead spot check for EURUSD at `2026-08-28T23:45:00Z`:

- M15 latest returned bar: `2026-08-28T23:00:00Z`
- H1 latest returned bar: `2026-08-28T20:30:00Z`
- H4 latest returned bar: `2026-08-28T11:30:00Z`

All are at or before T and do not read future bars.

## Blocker

Real PIT mechanics pass, but V2 historical validation is blocked by adapter coverage:

- `bsi_abc` depends on `bsi_v2_fixture["abc"]`
- `bsi_asian` depends on `bsi_v2_fixture["asian"]`
- `bsi_under_over` depends on `bsi_v2_fixture["under_over"]`
- `bsi_0930` depends on `bsi_v2_fixture["0930"]`
- `bsi_reactionary` depends on `bsi_v2_fixture["reactionary"]`
- `bsi_abcd` depends on `bsi_v2_fixture["abcd"]`
- `bsi_ob_liquidity` depends on `bsi_v2_fixture["ob_liquidity"]`

Real DB replay contexts do not contain those synthetic fixtures.

## Demo Gate

Not passed for DEMO readiness because PIT replay cannot yet produce a real V2 corpus across all strategies.
