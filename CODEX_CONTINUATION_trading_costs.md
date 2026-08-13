# Continuation prompt: MT5 trading-cost accounting overhaul

Paste everything below into Codex as the task prompt. It is self-contained.

---

## Task

You're continuing a large refactor in a Python/FastAPI MT5 trading backend (repo root: this
directory). The original spec (verbatim, still fully in force — nothing below overrides it):

Audit and integrate **real broker trading costs** into the MT5 $10K DEMO analytics, strategy
evaluation, expectancy, and trade reporting.

Do NOT blindly hardcode `$7 per lot`. The application must support broker/account-specific
trading costs and use actual MT5 deal history where available.

**Objective**: every performance calculation must distinguish gross P&L, commission, swap,
fees, slippage where measurable, and net P&L. Canonical relationship:

```
net_pnl = gross_pnl + commission + swap + fee
```

Respect MT5 sign conventions — commission/fees are normally negative. Do not subtract a
negative commission twice.

Full original 14-section spec (still authoritative for everything not yet done):

1. Inspect actual MT5 deal data first (profit/commission/swap/fee/volume/entry-exit/position_id/symbol) — **DONE, see "Audit findings" below**.
2. Add configurable commission fallback: `MT5_COMMISSION_MODE=BROKER_REPORTED|CONFIG_FALLBACK|NONE` (preferred: `BROKER_REPORTED`), `MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN=7.0`, scaling linearly by volume — **DONE**.
3. Avoid double counting — **DONE, centralized in one module**.
4. Persist cost breakdown per closed position: gross_pnl, commission, swap, other_fees, slippage_cost where measurable, total_trading_cost, net_pnl, total_volume, commission_per_lot_effective, aggregated correctly for partial closes — **PARTIALLY DONE** (see below).
5. Strategy analytics net-of-cost: gross/net P&L, commission, swap, total costs, gross/net expectancy, gross/net profit factor, avg gross/net win, avg gross/net loss. Primary headline = NET — **PARTIALLY DONE** (core_metrics_report done; per-strategy breakdown NOT done).
6. gross_R and net_R (`net_R = net_pnl / initial_risk_money`); a gross winner that commission turns net-negative must never report as a profitable +R trade — **DONE in core dataset, not yet in every report**.
7. Entry-quality/minimum-edge observation-only analytics: expected gross edge, estimated transaction cost, cost as % of expected profit, cost as % of initial risk, flag setups where reward barely exceeds cost. Do NOT change strategy rules — **NOT STARTED**.
8. Commission-aware confidence analytics: is confidence 75–79 actually profitable net of cost? Do NOT change confidence weights yet — **NOT STARTED**.
9. Adaptive-manager analytics: gross realized R, net realized R, gross/net manager expectancy. A break-even exit (gross ≈ 0) can still be net-negative after commission/slippage — represent this correctly, never label it a true break-even — **NOT STARTED**.
10. Dashboard/frontend field exposure: account summary (Gross P&L, Trading costs, Net P&L), trade journal (Gross P&L, Commission, Swap, Fees, Net P&L), strategy page (gross/net expectancy, cost/trade, cost/lot). Performance page defaults to NET — **NOT STARTED** (no endpoint built yet).
11. $10K DEMO historical backfill: reconstruct gross/commission/swap/net from actual broker deal history, report whether the existing "+$167.61" figure was already net or gross (verify, don't assume) — **NOT STARTED** (raw data gathered, see below, but no formal report/endpoint built).
12. Strategy comparison after costs: MTFAI1 vs breakout vs SMC continuation vs momentum vs others, gross vs net — **NOT STARTED**.
13. 22 specific tests listed in the original spec (broker-reported preferred, fallback scaling incl. exact 0.10→$0.70 / 0.70→$4.90 examples, partial-close aggregation, sign handling, no double-counting, swap/fee included, gross≠net stays distinct, net expectancy/R uses costs, gross break-even can be net-negative, strategy analytics use net, historical reconciliation matches broker ledger, and 6 "nothing else changed" guardrail tests: no trading-decision changes, confidence threshold stays 75, risk sizing unchanged, adaptive manager rules unchanged, OpenAI absent, IBKR absent, live trading blocked) — **NOT STARTED, zero tests written**.
14. Final report (see original spec for exact required contents) — **NOT STARTED**.

Do not tune strategies. Goal: every MT5 performance/learning metric reflects real net economics
after broker commission, swap, fees, and measurable execution cost — not just raw price P&L.

## Audit findings (already done — don't redo this)

Before this refactor, **three different, inconsistent formulas** computed "realized P&L" from
the same underlying MT5 deal data:
1. 4-term (correct): `profit + commission + swap + fee` — `adaptive_management/service.py` (4 call sites) + `adaptive_management/analytics.py`.
2. 3-term (missing fee): `profit + commission + swap` — `brokers/mt5/persistence.py::update_trade_history` → `MT5TradeRecordORM.realized_pnl`.
3. 2-term (missing swap + fee): `profit + commission` — `portfolio_execution/service.py::build_snapshot`.

All three now delegate to one shared module (see below) — **already fixed**.

No hardcoded `$7/lot` existed anywhere in the live MT5 path (only in an unrelated prop-firm
stress-simulation engine, `backend/research/prop_firms/`, left untouched — out of scope).

**Live broker ground truth** (queried directly via `mt5.history_deals_get`, this account,
2026-08-12, 14-day window): 381 deals, 180 closed positions. **Commission = 0.0 and fee = 0.0
on every single deal** — this is real broker data, not a gap (this MT5 demo broker charges no
explicit commission/fee; cost is embedded in spread, already reflected in `profit`). Swap was
nonzero on only 4 deals, both signs seen (e.g. `-0.64`, `-0.07`, `+0.01`, `-0.66`). Account
balance `$10,169.39`, equity `$10,170.09` vs `$10,000` starting balance — this is very close to
the previously-reported "+$167.61" figure, strongly suggesting that figure was **already
effectively net** (since gross ≈ net here — commission/fee are genuinely zero for this
account), but this needs a **formal, exact reconciliation** against the deal ledger for the
final report (section 11) — not yet done, just observed.

`MT5Position`/`MT5TradeRecordORM` had no `fee` column even though `MT5HistoryItem` did — fixed
via migration. `MT5CandidateEvaluationORM` (confidence-calibration table) had **zero**
commission/swap/fee columns at all — fixed via migration, but **not yet populated** (see below).

`profit_factor`/expectancy in `adaptive_management/analytics.py` and
`brokers/mt5/confidence_calibration.py` are **R-multiple-based**, not dollar-based — a
different, pre-existing metric. Don't conflate; name any new dollar-based profit factor
distinctly (already done as `total_gross_pnl`/`total_net_pnl` etc. in `core_metrics_report`).

## What's already implemented (verify it still works, then build on it — don't redo)

**New file `backend/brokers/mt5/trading_costs.py`** — the single canonical cost-accounting
module. Contains:
- `resolve_commission(*, broker_commission: float | None, volume: float | None) -> tuple[float, str]` — per-deal commission resolution. Key design decision: `broker_commission is None` means the broker genuinely didn't supply a value (distinct from a real, trusted `0.0`) — only `None` triggers the CONFIG_FALLBACK path even in BROKER_REPORTED mode (documents spec section 2's "if unavailable" wording precisely). A real `0.0` from the broker is trusted as-is, source=`BROKER_REPORTED` (this is why this account's commission is correctly reported as `$0`, not silently overridden with a fake `$7/lot`).
- `commission_mode()` / `commission_per_lot_round_turn()` — read `MT5_COMMISSION_MODE` (default `BROKER_REPORTED`) / `MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN` (default `7.0`) from env live (not cached), so tests can monkeypatch per-case.
- `TradeCostBreakdown` frozen dataclass: `gross_pnl, commission, swap, other_fees, net_pnl, total_volume, commission_source, commission_per_lot_effective, deal_count`, plus a `total_trading_cost` property (`= gross_pnl - net_pnl`, positive = costs reduced P&L).
- `compute_trade_costs(deals: Iterable[Any]) -> TradeCostBreakdown` — aggregates a position's deals (duck-typed: accepts dicts or objects exposing `profit`/`commission`/`swap`/`fee`/`volume`), resolving commission **per deal** (so partial closes aggregate correctly automatically — each deal's own volume drives its own fallback if needed). `commission_source` becomes `"MIXED"` if deals within one position used different sources.
- Fallback math verified against spec examples: 1.00 lot→$7.00, 0.70→$4.90, 0.10→$0.70, 0.06→$0.42 (all as negative signed values internally).

**Migration `backend/alembic/versions/0043_trading_cost_accounting.py`** (revises `0042_mtfai1_confirmation_gate`) — adds to `mt5_trade_records`: `gross_pnl, fee, total_trading_cost, commission_per_lot_effective, commission_source`. Adds to `mt5_candidate_evaluations`: `gross_pnl, commission, swap, fee, net_pnl, total_trading_cost, commission_source` (+ index on `net_pnl`). **Not yet run against the live DB** — needs container rebuild + restart (entrypoint runs `alembic upgrade head` automatically) before anything touching these columns will work live.

**ORM updates** (`backend/brokers/mt5/orm.py`) — `MT5TradeRecordORM` and `MT5CandidateEvaluationORM` classes updated to match the migration exactly.

**Fixed call sites** (all now delegate to `compute_trade_costs`, verified via `ast.parse` syntax check only — **not yet test-run**):
- `backend/adaptive_management/analytics.py::_position_records()` — now computes `cost_breakdown` per position via `compute_trade_costs`, keeps `realized_pnl`/`realized_r` field names (now correctly/explicitly NET), adds new fields: `gross_pnl, gross_r, net_pnl (alias), net_r (alias), commission, swap, other_fees, total_trading_cost, total_volume, commission_per_lot_effective, commission_source`.
- `backend/adaptive_management/analytics.py::core_metrics_report()` — adds `gross_expectancy_r, net_expectancy_r, gross_profit_factor, net_profit_factor, avg_gross_win_r, avg_net_win_r, avg_gross_loss_r, avg_net_loss_r, total_gross_pnl, total_commission, total_swap, total_other_fees, total_trading_cost, total_net_pnl` alongside the existing (now-net) `realized_expectancy`/`avg_r`/`profit_factor`.
- `backend/brokers/mt5/persistence.py::update_trade_history()` — populates the new `MT5TradeRecordORM` columns via `compute_trade_costs`.
- `backend/portfolio_execution/service.py::build_snapshot()` — `realized` now via `compute_trade_costs(...).net_pnl`.
- `backend/adaptive_management/service.py` — 4 call sites (`import_session`, `group_theses`, `audit_report`'s per-position loop, `_auto_replay_recently_closed`) all now route through `compute_trade_costs`. Note `group_theses` needed **per-row** (not aggregate) net values since `first_entry_result`/`additional_entry_contribution` decompose the list positionally — implemented as a list comprehension calling `compute_trade_costs([row])` once per row, not one aggregate call.

All new imports added with correct alphabetical placement (checked against each file's existing import block). All 6 touched files pass `python -c "import ast; ast.parse(...)"` syntax checks.

**Was mid-investigation when interrupted**: `backend/brokers/mt5/outcome_resolver.py`'s `_executed_outcome_from_management_records()` currently reads `MT5TradeRecordORM.realized_pnl` (via `trade_record.realized_pnl`) to populate `MT5CandidateEvaluationORM`'s outcome fields — this was documented earlier (in this session's prior work, see git history / `analytics.py` docstrings) as "unpopulated, a known gap." Was checking whether `update_trade_history()` (which populates that field) is actually invoked in the live cycle — confirmed yes, it's called from `autonomous.py::reconciliation()` (~line 1209), wrapped in try/except, using a 14-day deal-history window. **Not yet resolved**: why was the field allegedly never populated before — was it purely the calculation bug (now fixed) or also a wiring/invocation gap? Verify by checking live DB state after redeploying the migration + fix, before assuming it's now fully working.

## What's NOT done yet — pick up here

1. **`outcome_resolver.py`**: redirect `_executed_outcome_from_management_records()` to compute cost breakdown via `compute_trade_costs()` against the position's actual deals (same pattern as `_position_records()` in `analytics.py` — reuse that approach, possibly via `AdaptiveTradeEventORM` rows filtered by `position_id`), then populate the new `MT5CandidateEvaluationORM` columns (`gross_pnl, commission, swap, fee, net_pnl, total_trading_cost, commission_source`) at the same point `record_executed_outcome()` is called. This makes confidence-band analytics net-of-cost-capable (needed for section 8).

2. **Slippage** (section 4/7, "where measurable"): `MT5CandidateEvaluationORM` already has an unused `slippage` column (verified via grep — never populated anywhere in the codebase) plus existing `proposed_entry` and `actual_entry` columns (both already populated at candidate-evaluation / outcome-link time). Plan: compute `slippage = (actual_entry - proposed_entry)` direction-adjusted (positive = unfavorable/cost) in `outcome_resolver.py` when linking executed outcomes, populate the existing column in PRICE units. For a dollar estimate (best-effort, clearly labeled as approximate — do not overengineer with real broker pip-value calls, which would require async/broker access not available in that sync context), join to the position's `AdaptivePositionBaselineORM` (`initial_risk_money / initial_stop_distance` gives an implied $-per-price-unit ratio) inside the new section-7 report (see next item), not as a persisted column.

3. **Per-strategy gross vs net** (section 5, 12): extend `symbol_strategy_regime_report()` in `analytics.py` (already groups by `strategy`) to report gross_pnl, net_pnl, commission, swap, gross_r/net_r expectancy per strategy group — the per-record data already has everything needed (`_position_records()` was already extended with all the fields). Also check `winner_preservation_report()`, `loser_protection_report()`, `exit_reason_analytics_report()`, `confidence_band_management_report()` — extend each with gross/net parity following the same pattern used in `core_metrics_report()`.

4. **Entry-quality / minimum-edge report** (section 7, NEW, observation only): new function in `analytics.py`, e.g. `entry_quality_cost_report()` — for each position, compute expected gross edge (e.g. from `initial_reward_risk` × `initial_risk_money`, already on baseline), estimated transaction cost (`total_trading_cost`, or a fallback-mode "what would $7/lot cost" hypothetical alongside actual), cost as % of expected profit, cost as % of initial risk. Flag trades/setups where cost ≥ some threshold fraction of expected edge (document the threshold choice, keep it observation-only — do not gate/filter any actual trading path).

5. **Confidence-band net-of-cost** (section 8): `backend/brokers/mt5/confidence_calibration.py`'s `confidence_band_report()`/`_stats_for_group()` currently compute purely from R-multiples read off `MT5CandidateEvaluationORM` (`mfe_r`/`mae_r`/`realized_r`), which had no cost data before this refactor. Once step 1 populates `net_pnl`/`commission`/etc. on that table, extend these functions to report net-of-cost expectancy per confidence band (e.g., is 75-79 actually net-profitable). Keep confidence weights/thresholds completely untouched — this is reporting only.

6. **Adaptive-manager gross/net** (section 9): extend `break_even_analysis_report()` and any "break-even" labeling logic in `analytics.py` — a position whose `gross_r`≈0 (price closed at/near entry) but `net_r`<0 (commission/swap made it a real loss) must be classified/reported as a small loss, never as a true break-even. Check `_derive_exit_category`'s `BREAK_EVEN` classification logic specifically — it currently only looks at price/SL movement, not cost.

7. **$10K DEMO account-summary endpoint** (section 10): new endpoint (likely `backend/api/routes/adaptive_management.py`, following the existing `/validation/*` pattern) exposing account summary (Gross P&L, Trading costs, Net P&L), reusing `core_metrics_report()`'s new total_* fields. Also ensure trade-journal-shaped output (per-trade gross/commission/swap/fees/net) is available — `_position_records()` already has everything; may just need a thin new report function that returns the raw per-trade list with the cost fields, or confirm an existing endpint already exposes `_position_records()`-shaped data.

8. **Historical $10K reconciliation** (section 11): build the exact reconciliation — sum `_position_records()` (or a wider window) gross_pnl/commission/swap/net_pnl, compare against live broker balance delta (`$10,169.39 - $10,000 = $169.39`, already observed) and explicitly state whether the previously-reported `+$167.61` was gross or net (verify by finding where that figure was originally computed/reported — search git history/prior conversation artifacts if available, or just recompute honestly from current data and report the discrepancy).

9. **22 tests** (section 13) — none written yet. Suggested new file `backend/tests/test_mt5_trading_costs.py` for the core module (items 1-13 in the original list: broker-reported preferred, fallback scaling incl. exact examples, partial-close aggregation, sign handling, no double-counting, swap/fee inclusion, gross≠net). Extend existing test files (`test_adaptive_management.py`, `test_mt5_persistence.py`, `test_confidence_calibration.py`, `test_adaptive_manager_validation.py`) for items 14-19 (net expectancy/R, gross break-even can be net-negative, strategy analytics net, historical reconciliation). The 6 guardrail tests (20-25 in spirit — items 16-22 in original numbering: no trading-decision changes, confidence threshold 75, risk sizing unchanged, adaptive manager rules unchanged, OpenAI absent, IBKR absent, live trading blocked) likely already have precedent tests elsewhere in the suite (e.g. `test_confidence_calibration.py` already has `test_production_threshold_remains_75`, `test_calibration_layer_source_has_zero_openai_dependencies`, `test_live_trading_remains_blocked` from prior work this session) — just confirm they still pass rather than writing new ones, unless coverage gaps are found.

10. **Full verification**: rebuild the docker image (`docker compose build backend` then `docker compose up -d backend` — note: this project was JUST moved from `C:\Users\Intel\Desktop\Devops\trading\OpenTerminalUI` to `D:\Devops\trading\OpenTerminalUI`; make sure you're operating from the new path), run the migration, run targeted tests, run the full suite, live-verify against real broker data (the same `mt5.history_deals_get` ground-truth technique used in the audit — see "Live broker ground truth" above for the exact pattern), then the full `backend/tests` suite. Known **pre-existing, unrelated** flaky failures to expect and ignore (not caused by this work): tests in `test_broker_phase11.py`, `test_mcp_server.py`, `test_research_fetch.py::test_pdf_to_text_extracts_a_known_string`, `test_mt5_adapter.py::test_mt5_order_send_called_exactly_once_for_approved_intent`, and occasionally IBKR-domain tests (`test_ibkr_*`) — these fail intermittently in the full suite due to pre-existing test-order pollution unrelated to any MT5/adaptive-management/portfolio-execution work; verify by re-running any that appear in isolation before treating them as real regressions.

11. **Final report** (section 14) — once everything above is done, write the report with the exact contents the original spec section 14 lists (broker commission model discovered, files changed, config, P&L formula, historical $10K gross/commission/swap/net, strategy gross vs net, expectancy gross vs net, cost per trade/lot, test results, limitations).

## Constraints (unchanged from original spec — do not violate)

- Do not change any trading-decision logic, confidence threshold (must stay 75), risk sizing, or adaptive-manager rules.
- OpenAI and IBKR must remain absent from this path.
- Live trading must remain blocked.
- This is DEMO-account analytics/accounting work only.
- Never commit to git unless explicitly asked.
