"""Forward FTMO-style validation (Priority 6, 2026-08-21).

Read-only reporting over data the system already collects -- no new persisted tables, no
strategy/risk/execution logic touched. Reuses backend.adaptive_management.analytics'
_position_records (the same real, cost-adjusted, per-position dataset Priority 2 used) and
backend.historical_intelligence.strategy_robustness' Monte Carlo machinery (the same one
Priority 5 used for the account-level breach simulation), rather than rebuilding either.

Two families of function, deliberately kept separate per the user's own explicit instruction
not to mix them into one number:
  - account_forward_scorecard / portfolio_forward_status / strategy_forward_scorecard: ACTUAL
    FORWARD DEMO PERFORMANCE -- real closed positions, real account state, live right now.
  - ftmo_breach_simulation: HISTORICAL SIMULATION -- bootstrap-resampled from the historical
    corpus, an estimate of what COULD happen, not a report of what DID happen.
"""
from __future__ import annotations

import random
import statistics as pystats
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.adaptive_management.analytics import _position_records
from backend.brokers.mt5.multi_account import adapter_for_account
from backend.portfolio_execution.service import portfolio_manager

CURRENT_EXECUTION_ELIGIBLE = ("mean_reversion", "trend_pullback")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sample_label(n: int) -> str:
    # Matches the sample-size honesty convention already established elsewhere in this
    # codebase (confidence_calibration.py, strategy_robustness.py) -- never present a number
    # computed from a handful of trades with the same visual weight as one computed from
    # thousands.
    if n == 0:
        return "no_data"
    if n < 10:
        return "insufficient"
    if n < 30:
        return "preliminary"
    if n < 100:
        return "indicative"
    return "reliable"


def _summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)
    net_rs = [r["net_r"] for r in records if r.get("net_r") is not None]
    wins = [r for r in net_rs if r > 0]
    losses = [r for r in net_rs if r < 0]
    gross_win = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0
    # Longest consecutive-loss run, ordered by whatever order records were returned in
    # (_position_records has no guaranteed chronological order -- callers that need
    # chronological consecutive-loss tracking should sort by opened_at/exit first; this
    # reports on the given order, documented rather than silently assumed).
    longest_losing_run = 0
    current_run = 0
    for r in net_rs:
        if r < 0:
            current_run += 1
            longest_losing_run = max(longest_losing_run, current_run)
        else:
            current_run = 0
    return {
        "sample_size": n, "sample_label": _sample_label(n),
        "win_rate": round(len(wins) / len(net_rs), 4) if net_rs else None,
        "net_expectancy_r": round(pystats.fmean(net_rs), 4) if net_rs else None,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss else None,
        "avg_win_r": round(pystats.fmean(wins), 4) if wins else None,
        "avg_loss_r": round(pystats.fmean(losses), 4) if losses else None,
        "consecutive_losses_max": longest_losing_run,
        "total_net_pnl": round(sum(r.get("net_pnl") or 0.0 for r in records), 2),
        "total_gross_pnl": round(sum(r.get("gross_pnl") or 0.0 for r in records), 2),
        "total_trading_cost": round(sum(r.get("total_trading_cost") or 0.0 for r in records if r.get("total_trading_cost") is not None), 2),
    }


def _group_by(records: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        buckets.setdefault(str(r.get(key) or "unknown"), []).append(r)
    return {k: _summarize_records(v) for k, v in buckets.items()}


def strategy_forward_scorecard(strategy_id: str, *, account_id: str | None = None) -> dict[str, Any]:
    """Part 9's per-strategy scorecard. historical_status/forward_demo_status are kept as
    separate top-level keys (never blended) per the user's explicit instruction."""
    from backend.mt5_strategies import lifecycle as lc

    records = [r for r in _position_records(account_id=account_id) if r["strategy"] == strategy_id]
    forward = _summarize_records(records)
    state = lc.get_state(strategy_id)
    return {
        "strategy_id": strategy_id,
        "lifecycle_state": state["lifecycle_state"],
        "pending_recommendation": state["pending_recommendation"],
        "historical_status": lc._AUDIT_CLASSIFICATIONS.get(strategy_id),
        "forward_demo_status": forward,
        "by_symbol": _group_by(records, "symbol"),
        "by_regime": _group_by(records, "market_regime"),
        "by_session": _group_by(records, "session"),
    }


def account_forward_scorecard(account_id: str) -> dict[str, Any]:
    """Part 7 -- real, live, per-account tracking. Equity/balance/margin/open-risk come from
    the live portfolio snapshot (already refreshed every ~15s by PortfolioManager, plus
    synchronously after every fill since Priority 5.5); trade-level stats come from the real
    closed-position ledger, restricted to the CURRENTLY execution-eligible strategies (an
    account's historical trade mix includes strategies that are no longer live -- mixing them
    into a 'forward validation' number would misrepresent what this account is doing now)."""
    snapshot = portfolio_manager.latest_snapshot(account_id) or {}
    records = [r for r in _position_records(account_id=account_id) if r["strategy"] in CURRENT_EXECUTION_ELIGIBLE]
    overall = _summarize_records(records)
    equity = snapshot.get("equity")
    balance = snapshot.get("balance")
    return {
        "account_id": account_id,
        "as_of": snapshot.get("created_at"),
        "balance": balance, "equity": equity,
        "floating_pnl": snapshot.get("floating_pnl"),
        "realized_pnl_7d": snapshot.get("realized_pnl"),
        "margin_utilization": (snapshot.get("margin") / equity) if (snapshot.get("margin") and equity) else None,
        "open_risk_usd": snapshot.get("open_risk"),
        "max_correlated_currency_exposure_lots": max((abs(v.get("net", 0)) for v in (snapshot.get("exposure_by_currency") or {}).values()), default=0.0),
        "current_execution_eligible_strategies": list(CURRENT_EXECUTION_ELIGIBLE),
        "trade_stats_current_eligible_strategies_only": overall,
        "by_strategy": _group_by(records, "strategy"),
        "by_symbol": _group_by(records, "symbol"),
        "by_regime": _group_by(records, "market_regime"),
        "by_session": _group_by(records, "session"),
    }


def portfolio_forward_status(strategy_ids: tuple[str, ...] = CURRENT_EXECUTION_ELIGIBLE) -> dict[str, Any]:
    """Part 10 -- the combined mean_reversion+trend_pullback portfolio, across all accounts.
    Diversification is reported structurally (regime overlap, simultaneous-signal frequency)
    rather than assumed -- see 'regime_overlap' below, which is empty by construction if the
    two strategies' regime gates never intersect (they don't -- mean_reversion is RANGING/
    UNKNOWN-only, trend_pullback is TRENDING-only, confirmed in Priority 5)."""
    records = [r for r in _position_records(account_id=None) if r["strategy"] in strategy_ids]
    per_strategy = {sid: _summarize_records([r for r in records if r["strategy"] == sid]) for sid in strategy_ids}
    combined = _summarize_records(records)

    regimes_by_strategy = {sid: {r.get("market_regime") for r in records if r["strategy"] == sid} for sid in strategy_ids}
    all_regime_sets = list(regimes_by_strategy.values())
    regime_overlap = set.intersection(*all_regime_sets) if all_regime_sets and all(all_regime_sets) else set()

    # Simultaneous-exposure overlap (closed positions from both strategies open at the same
    # real time) is NOT computed here -- _position_records' returned shape doesn't currently
    # expose opened_at/exit_time (only holding_seconds), and approximating overlap without real
    # timestamps would be a guess dressed up as a measurement. Documented gap, not silently
    # skipped: a true simultaneous-exposure count needs _position_records extended with
    # opened_at/exit_time, which this function deliberately does not fabricate.
    return {
        "strategies": list(strategy_ids), "combined": combined, "per_strategy": per_strategy,
        "regime_gate_overlap": sorted(regime_overlap), "diversification_note": "mean_reversion is regime-gated to RANGING/low_volatility, trend_pullback to TRENDING -- mutually exclusive by STRATEGY_FAMILIES design (see backend/mt5_strategies/models.py), confirmed empirically in Priority 5 (zero simultaneous same-symbol co-firing across 159 recent candidates)",
    }


def ftmo_breach_simulation(*, risk_percent_per_trade: float = 0.25, trades_per_window: int = 30, paths: int = 2000, seed: int = 7) -> dict[str, Any]:
    """Part 8 -- HISTORICAL SIMULATION, explicitly labeled as such (never to be read as actual
    forward performance). Bootstrap-resamples the real historical mean_reversion+trend_pullback
    R-distribution into realistic single-account evaluation-window paths -- same methodology
    validated in Priority 5, packaged here as a reusable function instead of a throwaway script."""
    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
    from backend.shared.db import SessionLocal

    with SessionLocal() as db:
        rows: list[Any] = []
        for strategy_id in CURRENT_EXECUTION_ELIGIBLE:
            q = (
                db.query(HistoricalSetupOutcomeORM.net_outcome_r, HistoricalSetupOutcomeORM.outcome_r)
                .join(HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id)
                .filter(
                    HistoricalPatternFingerprintORM.anchor_strategy == strategy_id,
                    HistoricalSetupOutcomeORM.resolution_status == "RESOLVED",
                    HistoricalSetupOutcomeORM.data_quality != "UNTRUSTED",
                    HistoricalSetupOutcomeORM.outcome_r.isnot(None),
                )
                .order_by(HistoricalPatternFingerprintORM.entry_time.desc())
                .limit(8000)
            )
            rows.extend(q.all())
    r_pool = [float(r[0]) if r[0] is not None else float(r[1]) for r in rows]
    if not r_pool:
        return {"status": "NO_HISTORICAL_DATA", "r_pool_size": 0}

    rng = random.Random(seed)
    terminals: list[float] = []
    max_dds: list[float] = []
    for _ in range(paths):
        sample = [rng.choice(r_pool) for _ in range(trades_per_window)]
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in sample:
            cum += r
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
        terminals.append(cum)
        max_dds.append(max_dd)
    terminals.sort()
    max_dds.sort()
    n = len(max_dds)

    def _pct(values: list[float], p: float) -> float:
        return values[min(n - 1, int(n * p))]

    return {
        "status": "HISTORICAL_SIMULATION", "label": "This is a bootstrap simulation from historical data, NOT a report of actual forward demo performance.",
        "r_pool_size": len(r_pool), "trades_per_window": trades_per_window, "paths": paths, "risk_percent_per_trade": risk_percent_per_trade,
        "probability_window_ends_negative": round(sum(1 for t in terminals if t < 0) / n, 4),
        "max_drawdown_pct_equity": {
            "p50": round(_pct(max_dds, 0.50) * risk_percent_per_trade, 3),
            "p90": round(_pct(max_dds, 0.90) * risk_percent_per_trade, 3),
            "p95": round(_pct(max_dds, 0.95) * risk_percent_per_trade, 3),
            "p99": round(_pct(max_dds, 0.99) * risk_percent_per_trade, 3),
        },
        "configured_buffers_for_comparison": {
            "internal_max_daily_loss_percent": 2.00, "internal_max_total_drawdown_percent": 5.00,
            "note": "FTMO-style 5% daily / 10% total limits are the assumed values coded in backend/brokers/mt5/prop_risk.py::ftmo_2step_limits -- verify against your actual current FTMO account terms before relying on this comparison.",
        },
    }
