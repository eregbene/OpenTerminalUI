# BSI V2 Full Backfill Report

Date: 2026-09-03

## Verdict

Full BSI V2 backfill was not run.

## Reason

The real database was found and PIT replay mechanics passed on bounded samples, but full backfill is blocked by V2 replay adapter coverage.

Most V2 strategy evaluators still require synthetic `bsi_v2_fixture` inputs that are present in golden tests, not in real historical DB contexts.

Running the full backfill now would create a misleading near-zero corpus and would not prove historical rarity.

## Current Backfill Readiness

Implemented prerequisites:

- Durable replay lifecycle store.
- All-nine V2 evaluator dispatch.
- All-nine funnel aggregation.
- Test environment confidence normalization.
- V2-only regression suite is clean.

Missing prerequisites:

- Real-data geometry adapters for fixture-dependent V2 subtypes.
- Real PIT replay sample that produces representative V2 occurrences.
- Historical output cross-check against real V2 occurrences.
- Backfill storage/writing policy approved for version-isolated V2 rows.

## Methodology Boundary

No strategy methodology was changed for this report.

## Demo Gate

Not passed.

Full backfill must remain blocked until fixture-dependent V2 subtypes can evaluate from real DB contexts.
