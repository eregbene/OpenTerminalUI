# MT5 Trading-Cost Accounting Report - 2026-08-12

Branch: `feature/adaptive-trade-manager-v2`

## Broker Commission Model

- Account: MT5 DEMO `5054067375`, server `MetaQuotes-Demo`, currency `USD`.
- Configured mode: `MT5_COMMISSION_MODE=BROKER_REPORTED` by default.
- Fallback config: `MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN=7.0`.
- Live broker deal history reports explicit commission as `0.0` on every deal checked.
- Live broker deal history reports explicit fee as `0.0` on every deal checked.
- Swap is real and nonzero on a small number of deals.
- Therefore, for this DEMO broker, explicit commission/fee cost is zero; spread cost is embedded in broker `profit`.

Canonical formula:

```text
net_pnl = gross_pnl + commission + swap + fee
```

Commission, swap, and fee keep MT5 signed conventions. Negative commission/fee/swap are costs and are never subtracted twice.

## Files Changed

- `backend/brokers/mt5/trading_costs.py`
- `backend/alembic/versions/0043_trading_cost_accounting.py`
- `backend/brokers/mt5/orm.py`
- `backend/brokers/mt5/persistence.py`
- `backend/portfolio_execution/service.py`
- `backend/adaptive_management/service.py`
- `backend/adaptive_management/analytics.py`
- `backend/brokers/mt5/outcome_resolver.py`
- `backend/brokers/mt5/confidence_calibration.py`
- `backend/api/routes/adaptive_management.py`
- `backend/tests/test_mt5_trading_costs.py`

## New Config

- `MT5_COMMISSION_MODE=BROKER_REPORTED|CONFIG_FALLBACK|NONE`
- `MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN=7.0`

`BROKER_REPORTED` trusts a real broker-reported `0.0`; only missing commission (`None`) falls back to configured commission.

## Schema And Backfill

- Docker backend image rebuilt successfully.
- Backend restarted successfully.
- Alembic migrated to `0043_trading_cost_accounting (head)`.
- Confirmed DB `alembic_version = 0043_trading_cost_accounting`.
- Confirmed new cost columns exist on `mt5_trade_records` and `mt5_candidate_evaluations`.
- Forced MT5 session import after migration:
  - imported events: `781`
  - imported deals: `390`
  - imported orders: `389`
  - open positions: `2`
- One-time candidate cost backfill ran against existing executed outcomes.

## Historical $10K DEMO Reconciliation

Live MT5 bridge snapshot:

- Balance: `$10,149.00`
- Equity: approximately `$10,142` during verification, moving with two open positions.
- Open positions: `2`
- Open floating P&L during verification: about `-$7`.

Live broker deal-history totals from MT5 `history_deals_get`, 30-day/14-day window:

- Deal count: `387`
- Exit-side symbol deals:
  - Gross P&L: `$172.91`
  - Commission: `$0.00`
  - Swap: `-$1.36`
  - Fee: `$0.00`
  - Net P&L: `$171.55`
  - Trading costs: `$1.36`
- Non-symbol balance operations:
  - Initial deposit: `$10,000.00`

Balance delta from `$10,000` is currently `$149.00`, while broker exit-side history sums to `$171.55`. The bridge-returned history and account balance therefore do not exactly reconcile at this snapshot; the unexplained gap is `$22.55`. I did not assume the old `+$167.61` was gross or net. Given broker commission/fee are zero and swap is tiny, the old `+$167.61` figure was effectively net-of-explicit-cost if it came from broker/account P&L, but the exact source was not found in repo history/files.

Deduped adaptive managed-trade analytics after import:

- Closed managed trades: `142`
- Gross P&L: `$113.86`
- Commission: `$0.00`
- Swap: `-$0.65`
- Fees: `$0.00`
- Trading costs: `$0.65`
- Net P&L: `$113.21`
- Gross expectancy: `0.0403R`
- Net expectancy: `0.0401R`
- Gross profit factor: `1.1500`
- Net profit factor: `1.1491`

## Strategy Gross Vs Net

Strategy reporting now exposes gross/net expectancy, gross/net profit factor, gross/net P&L, commission, swap, fees, cost/trade, and cost/lot. Current strongest/weakest rows are sample-size limited; examples from the latest report:

- `mtfai1`: 76 trades, gross/net both approximately `-$97`, explicit costs near zero.
- `liqsweep`: 2 trades, net `$76.28`.
- `breakout+smc_cont`: 1 trade, net `$35.62`.
- `momentum`: affected by swap; gross `-$2.42`, net `-$3.08`.

## Confidence Net Of Cost

Executed confidence-band report now includes net-cost fields.

Current populated executed bands:

- `75-79`: 102 resolved, net expectancy `0.0184R`, gross `$40.14`, swap `-$0.65`, net `$39.49`, net profitable `true`.
- `80-84`: 34 resolved, net expectancy `0.1093R`, net `$78.59`, net profitable `true`.

## Entry Quality / Minimum Edge

New observation-only report: `/api/adaptive-management/validation/entry-quality-costs`.

Latest result:

- Trades evaluated: `142`
- Threshold: costs >= `25%` of expected gross edge.
- Flagged trades: `1`
- Flagged rate: `0.007`

This does not change strategy rules, confidence thresholds, risk sizing, or execution behavior.

## API Exposure

Added:

- `GET /api/adaptive-management/validation/account-cost-summary`
- `GET /api/adaptive-management/validation/trade-cost-journal`
- `GET /api/adaptive-management/validation/entry-quality-costs`

Existing validation reports now include gross/net cost fields.

## Tests

Focused tests:

- `python -m pytest backend/tests/test_mt5_trading_costs.py -q`
- Result: `7 passed`

MT5/adaptive targeted suite:

- `python -m pytest backend/tests/test_mt5_trading_costs.py backend/tests/test_adaptive_management.py backend/tests/test_confidence_calibration.py backend/tests/test_adaptive_manager_validation.py -q`
- Result: `100 passed`

Full backend suite:

- `python -m pytest backend/tests -q`
- Result: `1615 passed`, `14 failed`

Full-suite failures observed in unrelated/pre-existing areas:

- `test_agent_screener.py` Postgres run hit SQLite-only `PRAGMA`.
- `test_ai_shadow_trading.py` existing validation expectation mismatch.
- `test_broker_phase11.py` IBKR unavailable/503 failures.
- `test_ibkr_*` IBKR manager/fixture failures.
- `test_mcp_server.py` MCP API shape mismatch.
- `test_mt5_adapter.py::test_mt5_order_send_called_exactly_once_for_approved_intent` still fails in isolation.
- `test_portfolio_execution_manager.py` duplicate-key expectation mismatch.
- `test_research_fetch.py` PDF extraction dependency/runtime issue.
- `test_economic_intelligence_lifecycle.py` lifecycle status expectation mismatch.

The MT5 trading-cost and adaptive analytics slices pass.

## Guardrails

- No trading-decision logic changed.
- Confidence threshold remains `75`.
- Risk sizing not changed.
- Adaptive-manager rules not changed.
- OpenAI not introduced into this path.
- IBKR not introduced into this path.
- Live trading remains blocked: `MT5_LIVE_TRADING_ENABLED=false`.

## Limitations

- `mt5_trade_records` still has no populated closed records in the current DB snapshot; the adaptive deal ledger is the populated source for current analytics.
- Some historical `mt5_candidate_evaluations` rows marked `CLOSED` still lack matching adaptive deal rows and therefore remain without cost breakdown.
- Live MT5 bridge account balance and returned deal history did not exactly reconcile during this run (`$149.00` balance delta vs `$171.55` exit-side deal net). This must be treated as a broker/bridge-history data discrepancy until explained by a wider account statement export or terminal-side report.
