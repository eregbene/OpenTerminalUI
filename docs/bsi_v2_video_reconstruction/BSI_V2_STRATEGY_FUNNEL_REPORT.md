# BSI V2 Strategy Funnel Report

Date: 2026-09-03

## Verdict

All-nine funnel instrumentation is implemented, unit-tested, and executed on a bounded real DB PIT sample.

## Implemented Funnel Fields

Per subtype:

- evaluated
- valid_setups
- executable_entries
- consumed_entries
- rejection_reasons

Aggregate:

- contexts_evaluated
- source_counts
- bars_insufficient
- total_valid_setups
- total_executable_entries

## Unit Validation

Command:

`python -m pytest backend/tests/test_bsi_v2_validation_readiness.py -q`

Result:

`3 passed`

Synthetic empty-context all-nine result:

- 9 subtype evaluations
- 0 valid setups
- 0 executable entries
- all subtypes accounted for

## Real PIT Sample Funnel

12 real reconstructed contexts:

| Strategy | Evaluated | Structural Context | Thesis | Entry Opportunity | Executable | Simulated Trade | Outcome | Main rejection |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| bsi_order_flow | 12 | 12 | 0 | 0 | 0 | 0 | 0 | PRICE_NOT_IN_ENTRY_ZONE |
| bsi_asian | 12 | 0 | 0 | 0 | 0 | 0 | 0 | NO_ASIAN_SETUP |
| bsi_new_york | 12 | 12 | 0 | 0 | 0 | 0 | 0 | NY_LIQUIDITY_NOT_SIGNIFICANT_SWING |
| bsi_abc | 12 | 0 | 0 | 0 | 0 | 0 | 0 | NO_ABC_GEOMETRY |
| bsi_under_over | 12 | 0 | 0 | 0 | 0 | 0 | 0 | NO_UNDER_OVER_LEVEL |
| bsi_0930 | 12 | 0 | 0 | 0 | 0 | 0 | 0 | NO_0930_SETUP |
| bsi_reactionary | 12 | 0 | 0 | 0 | 0 | 0 | 0 | NO_REACTIONARY_SEQUENCE |
| bsi_abcd | 12 | 0 | 0 | 0 | 0 | 0 | 0 | NO_ABCD_GEOMETRY |
| bsi_ob_liquidity | 12 | 0 | 0 | 0 | 0 | 0 | 0 | NO_OB_LIQUIDITY_SETUP |

## Diagnosis

Zero does not mean the strategies were omitted. All nine were dispatched and evaluated.

For six fixture-dependent strategy families, zero currently means replay adapter coverage is incomplete for real DB contexts.

For Order Flow and New York, real structural context exists, but this bounded sample produced no fresh executable V2 opportunity.

## Demo Gate

Not passed for full historical readiness.
