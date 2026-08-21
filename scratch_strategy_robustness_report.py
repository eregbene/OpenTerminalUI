"""Strategy-by-strategy robustness report (QuantConnect/LEAN gap-analysis roadmap Phase 1, item
11) -- READ-ONLY against the real historical corpus, capped per strategy, and does NOT write
net_outcome_r back to the corpus. The full-corpus backfill (all ~2M rows) is deliberately deferred
to the already-scheduled weekend job rather than run now against tables the live trading system
reads from every cycle -- this script only needs a representative sample to answer "does cost
realism change the picture", not a full rewrite.

Cost fields are computed in-memory per row (never persisted) using the SAME execution_costs.py
tier logic (CONFIG_FALLBACK sourced from real recent median decision-snapshot spreads, commission
excluded since MT5TradeRecordORM.commission/close_timestamp are not currently populated for this
account -- see execution_costs.py's own empirical check, which correctly returns UNKNOWN rather
than trusting an unverifiable assumption)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from backend.historical_intelligence import execution_costs, walk_forward
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.historical_intelligence.strategy_robustness import (
    cost_realism_summary, drawdown_duration_stats, effective_sample_size, monte_carlo_r_space,
    robustness_scorecard, walk_forward_verdict, _Row,
)
from backend.shared.db import SessionLocal, engine

STRATEGIES = [
    "mtfai1", "support_resistance_bounce", "momentum", "session_breakout", "vwap_reversion",
    "breakout", "ema_trend", "mean_reversion", "smc_continuation", "trend_pullback",
    "liquidity_sweep_reversal", "wyckoff",
]
# Recency window + row cap + lighter bootstrap, per explicit direction: "we don't need 8 years,
# 2 or 4 years also [works]" -- 3-year lookback, and both the row cap and Monte Carlo/bootstrap
# path counts reduced from the first (aborted, CPU-contending) attempt so this stays light enough
# to run alongside live trading without contention.
LOOKBACK_YEARS = 3
SAMPLE_CAP = 8000
BOOTSTRAP_PATHS = 500
MONTE_CARLO_PATHS = 500

# Same real, recently-observed median spreads (mt5_decision_snapshots, n=559-1675/symbol) used by
# the (deferred) full backfill -- CONFIG_FALLBACK tier only, disclosed as current-representative,
# not period-exact.
_REAL_MEDIAN_SPREAD = {
    "XAUUSD": 0.19, "EURJPY": 0.003, "USDCAD": 0.00001, "USDJPY": 0.004, "EURUSD": 0.00001,
    "AUDUSD": 0.00001, "GBPJPY": 0.008, "USDCHF": 0.00002, "GBPUSD": 0.00002, "NZDUSD": 0.00001,
}


def _fetch_capped_rows(strategy: str, limit: int, *, lookback_years: int) -> list[_Row]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=365 * lookback_years)
    with SessionLocal() as db:
        query = (
            db.query(HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM)
            .join(HistoricalSetupOutcomeORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id)
            .filter(
                HistoricalPatternFingerprintORM.anchor_strategy == strategy,
                HistoricalPatternFingerprintORM.entry_time >= cutoff,
                HistoricalSetupOutcomeORM.resolution_status == "RESOLVED",
                HistoricalSetupOutcomeORM.data_quality != "UNTRUSTED",
                HistoricalSetupOutcomeORM.outcome_r.isnot(None),
            )
            .order_by(HistoricalPatternFingerprintORM.entry_time.desc())
            .limit(limit)
        )
        rows = query.all()

    out: list[_Row] = []
    for fp, oc in rows:
        entry_time = fp.entry_time if fp.entry_time.tzinfo else fp.entry_time.replace(tzinfo=timezone.utc)
        risk = abs(float(fp.entry) - float(fp.stop_loss))
        gross_r = float(oc.outcome_r)
        median = _REAL_MEDIAN_SPREAD.get(fp.canonical_symbol.upper())
        if median and risk > 0:
            spread_cost_r = median / risk
            net_r = gross_r - spread_cost_r  # commission excluded (UNKNOWN, not populated -- see module docstring)
            spread_prov = execution_costs.CONFIG_FALLBACK
        else:
            net_r = None
            spread_prov = execution_costs.UNKNOWN
        out.append(_Row(
            entry_time=entry_time, canonical_symbol=fp.canonical_symbol, gross_r=gross_r, net_r=net_r,
            spread_provenance=spread_prov, commission_provenance=execution_costs.COMMISSION_UNKNOWN,
        ))
    out.reverse()  # chronological order (query was DESC for the LIMIT, robustness fns want ASC)
    return out


def _fmt(x, nd=3):
    if x is None:
        return "N/A"
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return str(x)


def main() -> None:
    with engine.connect() as conn:
        total_rows = conn.execute(text(
            "SELECT count(*) FROM historical_pattern_fingerprints hpf "
            "JOIN historical_setup_outcomes hso ON hso.fingerprint_id = hpf.fingerprint_id "
            "WHERE hpf.anchor_strategy = ANY(:strats) AND hso.resolution_status='RESOLVED'"
        ), {"strats": STRATEGIES}).scalar()
    print(f"total resolved rows across all 12 strategies (for context, NOT all sampled): {total_rows}", flush=True)

    rows_out = []
    full_reports = {}
    for strategy in STRATEGIES:
        t0 = time.time()
        rows = _fetch_capped_rows(strategy, SAMPLE_CAP, lookback_years=LOOKBACK_YEARS)
        gross_r = [r.gross_r for r in rows]
        entry_times = [r.entry_time for r in rows]

        cost = cost_realism_summary(rows)
        scorecard = robustness_scorecard(gross_r, entry_times, bootstrap_paths=BOOTSTRAP_PATHS)
        mc = monte_carlo_r_space(gross_r, paths=MONTE_CARLO_PATHS)
        dd = drawdown_duration_stats(rows)
        wf = walk_forward_verdict(strategy)
        ess = effective_sample_size(gross_r)
        elapsed = time.time() - t0

        report = {
            "strategy": strategy, "sample_size": len(rows),
            "sample_note": f"most recent {SAMPLE_CAP} rows (or fewer) within the last {LOOKBACK_YEARS} years",
            "historical_evidence": {**cost, "effective_sample_size": ess},
            "statistical_robustness": {
                "sharpe_annualized": scorecard.get("annual_sharpe"), "psr": scorecard.get("psr"), "dsr": scorecard.get("dsr"),
                "min_track_record_length_trades": scorecard.get("min_track_record_length"),
                "bootstrap_sharpe_ci": (scorecard.get("bootstrap") or {}).get("sharpe"),
                "monte_carlo": mc, "verdict": str(scorecard.get("verdict", "insufficient")).upper(),
                "verdict_reasons": scorecard.get("verdict_reasons"),
            },
            "drawdown": dd, "walk_forward": wf,
        }
        full_reports[strategy] = report

        dd_p95 = (mc.get("drawdown_r_percentiles") or {}).get("p95")
        rows_out.append({
            "strategy": strategy, "trades": len(rows), "gross_exp": cost["gross_expectancy_r"], "net_exp": cost["net_expectancy_r"],
            "cost_drag": cost["cost_drag_r"], "pf_gross": cost["gross_profit_factor"], "pf_net": cost["net_profit_factor"],
            "oos_net_exp": wf.get("oos_expectancy_r_gross"), "psr": scorecard.get("psr"), "dsr": scorecard.get("dsr"),
            "ess": ess, "mc_p95_dd": dd_p95, "verdict": str(scorecard.get("verdict", "insufficient")).upper(),
            "wf_verdict": wf.get("verdict"), "directional_edge": wf.get("directional_edge"),
        })
        print(f"{strategy}: n={len(rows)} gross={_fmt(cost['gross_expectancy_r'])} net={_fmt(cost['net_expectancy_r'])} "
              f"verdict={rows_out[-1]['verdict']} wf={wf.get('verdict')} ({elapsed:.1f}s)", flush=True)

    print("\n\n=== MARKDOWN TABLE ===\n")
    print("| Strategy | Trades (sample) | Gross Exp R | Net Exp R | Cost Drag | PF gross | PF net | OOS Net Exp (gross) | PSR | DSR | ESS | MC P95 DD (R) | Robustness Verdict | Walk-Forward |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows_out:
        print(f"| {r['strategy']} | {r['trades']} | {_fmt(r['gross_exp'])} | {_fmt(r['net_exp'])} | {_fmt(r['cost_drag'])} | "
              f"{_fmt(r['pf_gross'])} | {_fmt(r['pf_net'])} | {_fmt(r['oos_net_exp'])} | {_fmt(r['psr'])} | {_fmt(r['dsr'])} | "
              f"{_fmt(r['ess'],0)} | {_fmt(r['mc_p95_dd'])} | {r['verdict']} | {r['wf_verdict']} |")

    with open("/app/scratch_strategy_robustness_full.json", "w") as f:
        json.dump(full_reports, f, indent=2, default=str)
    print("\nfull JSON written to /app/scratch_strategy_robustness_full.json", flush=True)


if __name__ == "__main__":
    main()
