# BSI V2 DEMO Activation Report

Date: 2026-09-03

## Decision

`BSI_BASELINE_V2_AUDIOVISUAL` is enabled for controlled DEMO trading.

Known limitation accepted for DEMO: real historical replay/backfill access and non-fixture historical V2 coverage remain incomplete; previous mechanical validation passed but produced zero historical V2 entries in the available replay path.

## Runtime State

- LIVE money remains disabled: `MT5_LIVE_TRADING_ENABLED=false`.
- MT5 DEMO order submission is enabled: `MT5_ORDER_SUBMISSION_ENABLED=true`.
- cTrader is explicitly DEMO: `CTRADER_ENVIRONMENT=demo`.
- cTrader bridge is enabled: `CTRADER_BENSIM_ENGINE_ENABLED=true`.
- cTrader order submission is enabled: `CTRADER_ORDER_SUBMISSION_ENABLED=true`.
- Active strategy family: `MT5_STRATEGY_ACTIVATION_BSI=ACTIVE_MT5`.
- Disabled legacy candidate generators:
  - `MT5_STRATEGY_ACTIVATION_MTFAI1=DISABLED`
  - `MT5_STRATEGY_ACTIVATION_EMA_TREND=DISABLED`
  - `MT5_STRATEGY_ACTIVATION_TREND_PULLBACK=DISABLED`
  - `MT5_STRATEGY_ACTIVATION_BREAKOUT=DISABLED`
  - `MT5_STRATEGY_ACTIVATION_MEAN_REVERSION=DISABLED`
  - `MT5_STRATEGY_ACTIVATION_LIQUIDITY_SWEEP_REVERSAL=DISABLED`
  - `MT5_STRATEGY_ACTIVATION_SMC_CONTINUATION=DISABLED`
  - `MT5_STRATEGY_ACTIVATION_SUPPORT_RESISTANCE_BOUNCE=DISABLED`
  - `MT5_STRATEGY_ACTIVATION_MOMENTUM=DISABLED`
  - `MT5_STRATEGY_ACTIVATION_SESSION_BREAKOUT=DISABLED`
  - `MT5_STRATEGY_ACTIVATION_VWAP_REVERSION=DISABLED`
- All nine BSI V2 subtypes are active under the BSI family gate.
- Generic BSI adaptive actions are disabled for initial V2 DEMO activation:
  - `BSI_ADAPTIVE_ENABLED=false`
  - `BSI_ADAPTIVE_V2_ENABLED=false`
  - `BSI_V2_ADAPTIVE_ACTIONS_ENABLED=false`

## Code Changes

- Active `EVALUATORS["bsi"]` now dispatches to `evaluate_bsi_v2_active`.
- `evaluate_bsi_v2_active` keeps downstream strategy identity as `bsi`, stamps `BSI_BASELINE_V2_AUDIOVISUAL` evidence, uses durable lifecycle storage, and preserves subtype activation metadata.
- BSI V2 candidates bypass only legacy numeric confidence hard gating.
- Historical Intelligence live influence is neutral for BSI V2 until V2-compatible fingerprints/outcomes exist.
- BSI V2 submission has an added final evidence gate requiring:
  - `freshness_status=AVAILABLE`
  - `lifecycle_state=CONSUMED`
  - non-empty `bsi_thesis_id`
  - non-empty `bsi_entry_opportunity_id`
- Adaptive manager returns HOLD-only for BSI V2 positions unless `BSI_V2_ADAPTIVE_ACTIONS_ENABLED=true`.

## Decision Path

Initial DEMO path is:

1. BSI V2 mentor setup validity.
2. Durable lifecycle and freshness evidence.
3. Strategy/subtype activation gate.
4. Context and economic risk checks.
5. Portfolio, frequency, losing-streak, cross-account exposure gates.
6. Fresh reward:risk check.
7. MT5 risk sizing / prop remaining budget / broker safety checks.
8. MT5 DEMO order submission.
9. cTrader DEMO bridge attempt only after the same candidate has naturally qualified.

No forced-trade code path was added.

## Verification

Source tests:

- `python -m pytest backend/tests/test_bsi_v2_foundation.py backend/tests/test_bsi_v2_new_york.py backend/tests/test_bsi_v2_all_strategies.py backend/tests/test_bsi_v2_validation_readiness.py backend/tests/test_bsi_v2_demo_activation.py backend/tests/test_mt5_multi_strategy.py -q`
- Result: `80 passed, 2 skipped`.

cTrader bridge/risk tests:

- `python -m pytest backend/tests/test_ctrader_bridge.py backend/tests/test_ctrader_risk_sizing.py -q`
- Result: `17 passed`.

cTrader adapter direct tests:

- `python -m pytest backend/tests/test_ctrader_bridge.py backend/tests/test_ctrader_adapter.py backend/tests/test_ctrader_risk_sizing.py -q`
- Result: `40 passed, 7 failed`.
- Failure reason: host shell lacks `ctrader_open_api`; failures occur at direct SDK import points, not in the BSI V2 activation changes.

Runtime verification after rebuild/restart:

- `docker compose up -d --build backend` completed.
- Backend health endpoint returned `{"status":"ok"}`.
- Running container confirms:
  - `EVALUATORS["bsi"] == evaluate_bsi_v2_active`: `True`
  - `MT5_LIVE_TRADING_ENABLED=false`
  - `MT5_ORDER_SUBMISSION_ENABLED=true`
  - `MT5_STRATEGY_ACTIVATION_BSI=ACTIVE_MT5`
  - `BSI_BASELINE_V2_AUDIOVISUAL_ENABLED=true`
  - `BSI_V2_LIFECYCLE_PATH=/data/bsi_v2_lifecycle_demo.json`
  - `BSI_V2_ADAPTIVE_ACTIONS_ENABLED=false`
  - `CTRADER_ENVIRONMENT=demo`
  - `CTRADER_BENSIM_ENGINE_ENABLED=true`
  - `CTRADER_ORDER_SUBMISSION_ENABLED=true`

Startup log showed the MT5 multi-account scheduler sleeping until the next completed M5 candle; no forced trade was triggered by restart.

## Status

Controlled DEMO activation is complete. LIVE money remains disabled.
