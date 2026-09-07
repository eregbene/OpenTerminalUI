# BSI V2 Controlled Replay Report

## Scope

Ran controlled in-memory V2 research-path validation through semantic fixtures and dispatcher tests.

This is not a full historical backfill and not a profitability replay.

## Mechanical Checks

Verified:

- all nine V2 strategy evaluators are configured
- all nine can be evaluated through isolated research dispatch
- no legacy strategy is in `BSI_V2_RESEARCH_EVALUATORS`
- no legacy fallback is used when no BSI V2 setup exists
- lifecycle blocks duplicate executable opportunities
- new Order Flow MSB/new array can create a new opportunity
- freshness blocks stale entries
- trendline liquidity is consumed by Order Flow
- mentor OB is consumed by Order Flow/ABC/Asian paths
- subtype targets remain distinct
- V1 and V2 remain version-separated

## Command Evidence

Core checkpoint:

`python -m pytest backend/tests/test_bsi_v2_foundation.py backend/tests/test_bsi_v2_new_york.py backend/tests/test_bsi_v2_all_strategies.py backend/tests/test_bsi_engine.py backend/tests/test_market_structure_phase5_engine.py backend/tests/test_market_structure_phase5_api.py backend/tests/test_mt5_strategy_stop_construction.py backend/tests/test_risk_calculator.py -q`

Result:

- `144 passed`

Broader smoke:

`python -m pytest backend/tests/test_bsi_canonical_fingerprint.py backend/tests/test_bsi_hi_fingerprint.py backend/tests/test_bsi_replay.py backend/tests/test_mt5_multi_strategy.py backend/tests/test_mt5_multi_strategy_optimization.py backend/tests/test_mt5_multi_account_risk_protection_fix.py backend/tests/test_ctrader_risk_sizing.py -q`

Result:

- `122 passed`
- `2 skipped`
- `2 failed`

Failure classification:

- `ENVIRONMENTAL/PRE_EXISTING`
- failing assertion: operational `mt5_config().min_trade_confidence` expected `55.0`, shell config returns `0.0`
- V2 did not modify confidence configuration

## Point-In-Time Historical Replay

Not run.

Reason:

- V2 historical occurrence persistence/backfill adapter is not implemented yet
- `TEST_DATABASE_URL` is unset
- broader smoke has environmental config failures

## Backfill Gate

Not passed.

Blocked items:

- durable V2 lifecycle/occurrence persistence
- point-in-time V2 replay adapter
- DB-backed historical fixture run
- clean environment for operational config tests

## Outcome

Controlled in-memory mechanics passed. Controlled point-in-time DB replay and full historical backfill remain deferred.
