"""Same as scratch_strategy_robustness_report.py, but for the PRIOR 3-year window (years 3-6 ago,
not the most recent 3 years) -- per explicit follow-up direction to check "another 3 years back"
and compare against what the recent-3-year run already found. Same light compute budget (the
first run was fast enough to not warrant further reduction)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from backend.historical_intelligence import execution_costs, walk_forward
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.historical_intelligence.strategy_robustness import (
    cost_realism_summary, drawdown_duration_stats, effective_sample_size, monte_carlo_r_space,
    robustness_scorecard, walk_forward_verdict, _Row,
)
from backend.shared.db import SessionLocal

STRATEGIES = [
    "mtfai1", "support_resistance_bounce", "momentum", "session_breakout", "vwap_reversion",
    "breakout", "ema_trend", "mean_reversion", "smc_continuation", "trend_pullback",
    "liquidity_sweep_reversal", "wyckoff",
]
WINDOW_START_YEARS_AGO = 6
WINDOW_END_YEARS_AGO = 3
SAMPLE_CAP = 8000
BOOTSTRAP_PATHS = 500
MONTE_CARLO_PATHS = 500

_REAL_MEDIAN_SPREAD = {
    "XAUUSD": 0.19, "EURJPY": 0.003, "USDCAD": 0.00001, "USDJPY": 0.004, "EURUSD": 0.00001,
    "AUDUSD": 0.00001, "GBPJPY": 0.008, "USDCHF": 0.00002, "GBPUSD": 0.00002, "NZDUSD": 0.00001,
}


def _fetch_capped_rows(strategy: str, limit: int, *, start: datetime, end: datetime) -> list[_Row]:
    with SessionLocal() as db:
        query = (
            db.query(HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM)
            .join(HistoricalSetupOutcomeORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id)
            .filter(
                HistoricalPatternFingerprintORM.anchor_strategy == strategy,
                HistoricalPatternFingerprintORM.entry_time >= start,
                HistoricalPatternFingerprintORM.entry_time < end,
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
            net_r = gross_r - spread_cost_r
            spread_prov = execution_costs.CONFIG_FALLBACK
        else:
            net_r = None
            spread_prov = execution_costs.UNKNOWN
        out.append(_Row(
            entry_time=entry_time, canonical_symbol=fp.canonical_symbol, gross_r=gross_r, net_r=net_r,
            spread_provenance=spread_prov, commission_provenance=execution_costs.COMMISSION_UNKNOWN,
        ))
    out.reverse()
    return out


def _fmt(x, nd=3):
    if x is None:
        return "N/A"
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return str(x)


def main() -> None:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=365 * WINDOW_START_YEARS_AGO)
    end = now - timedelta(days=365 * WINDOW_END_YEARS_AGO)
    print(f"window: {start.date()} to {end.date()}", flush=True)

    rows_out = []
    full_reports = {}
    for strategy in STRATEGIES:
        t0 = time.time()
        rows = _fetch_capped_rows(strategy, SAMPLE_CAP, start=start, end=end)
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
            "sample_note": f"most recent {SAMPLE_CAP} rows (or fewer) within {start.date()} to {end.date()}",
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

    print("\n\n=== MARKDOWN TABLE (prior 3-6 years ago) ===\n")
    print("| Strategy | Trades (sample) | Gross Exp R | Net Exp R | Cost Drag | PF gross | PF net | OOS Net Exp (gross) | PSR | DSR | ESS | MC P95 DD (R) | Robustness Verdict | Walk-Forward |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows_out:
        print(f"| {r['strategy']} | {r['trades']} | {_fmt(r['gross_exp'])} | {_fmt(r['net_exp'])} | {_fmt(r['cost_drag'])} | "
              f"{_fmt(r['pf_gross'])} | {_fmt(r['pf_net'])} | {_fmt(r['oos_net_exp'])} | {_fmt(r['psr'])} | {_fmt(r['dsr'])} | "
              f"{_fmt(r['ess'],0)} | {_fmt(r['mc_p95_dd'])} | {r['verdict']} | {r['wf_verdict']} |")

    with open("/app/scratch_strategy_robustness_prior3y_full.json", "w") as f:
        json.dump(full_reports, f, indent=2, default=str)
    print("\nfull JSON written to /app/scratch_strategy_robustness_prior3y_full.json", flush=True)


if __name__ == "__main__":
    main()
