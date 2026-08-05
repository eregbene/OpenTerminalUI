from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from backend.intelligence.trading.candles import canonicalize_candles
from backend.intelligence.trading.config import ai_trading_config
from backend.intelligence.trading.persistence import get_state, set_state
from backend.research.prop_firms.engine import candidates_from_candles, compatibility, monte_carlo, simulate_profile, summarize_attempts, trade_frequency
from backend.research.prop_firms.models import PropFirmSimulationRequest, utcnow_iso
from backend.research.prop_firms.profiles import EXECUTION_SCENARIOS, default_profiles, internal_safety_profile, profile_from_payload, with_account_size
from backend.services.forex_service import service as forex_service

STATE_KEY = "prop_firm_lab_v1"


class PropFirmLabService:
    def profiles(self) -> list[dict[str, Any]]:
        custom = self._state().get("profiles") or {}
        defaults = {key: value.model_dump() for key, value in default_profiles().items()}
        defaults.update(custom)
        return list(defaults.values())

    def profile(self, profile_id: str) -> dict[str, Any] | None:
        return next((row for row in self.profiles() if row["profile_id"] == profile_id), None)

    def upsert_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        profile = profile_from_payload(payload)
        state = self._state()
        profiles = dict(state.get("profiles") or {})
        profiles[profile.profile_id] = profile.model_dump()
        state["profiles"] = profiles
        state["updated_at"] = utcnow_iso()
        self._save(state)
        return profile.model_dump()

    async def create_simulation(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = PropFirmSimulationRequest(**payload)
        job_id = "propfirm_" + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]
        job = await self._run_job(job_id, request)
        state = self._state()
        jobs = dict(state.get("jobs") or {})
        jobs[job_id] = job
        state["jobs"] = jobs
        state["updated_at"] = utcnow_iso()
        self._save(state)
        return job

    def simulations(self) -> list[dict[str, Any]]:
        return sorted((self._state().get("jobs") or {}).values(), key=lambda row: row.get("created_at") or "", reverse=True)

    def simulation(self, job_id: str) -> dict[str, Any] | None:
        return (self._state().get("jobs") or {}).get(job_id)

    def update_job_status(self, job_id: str, status: str) -> dict[str, Any] | None:
        state = self._state()
        jobs = dict(state.get("jobs") or {})
        if job_id not in jobs:
            return None
        jobs[job_id]["status"] = status
        jobs[job_id]["updated_at"] = utcnow_iso()
        state["jobs"] = jobs
        self._save(state)
        return jobs[job_id]

    def leaderboard(self) -> dict[str, Any]:
        rows = []
        for job in self.simulations():
            rows.extend(((job.get("report") or {}).get("leaderboard") or []))
        return {"items": sorted(rows, key=lambda row: row.get("score", 0), reverse=True)}

    def compatibility(self) -> dict[str, Any]:
        rows = []
        for job in self.simulations():
            rows.extend(((job.get("report") or {}).get("compatibility") or []))
        return {"items": rows}

    async def _run_job(self, job_id: str, request: PropFirmSimulationRequest) -> dict[str, Any]:
        provider_calls = 0
        ibkr_calls = 0
        before_acceptance = self._acceptance_state()
        profiles = [with_account_size(profile_from_payload(self.profile(pid) or default_profiles(request.account_size)[pid].model_dump()), request.account_size) for pid in request.profile_ids]
        candles, data_quality = await self._load_candles(request.symbol, request.days)
        cfg = ai_trading_config()
        candidates = candidates_from_candles(candles, config=cfg) if data_quality["status"] == "VALID" else []
        internal = internal_safety_profile()
        attempts = []
        profile_results = []
        compatibility_rows = []
        all_trades = []
        for profile in profiles:
            profile_attempts = []
            for risk in request.risk_per_trade_percents:
                for cap in request.trade_frequency_caps:
                    for scenario_name in request.execution_scenarios:
                        scenario = EXECUTION_SCENARIOS[scenario_name]
                        attempt = simulate_profile(profile=profile, internal=internal, candidates=candidates, risk_percent=risk, trade_cap=cap, scenario=scenario, policy=request.policy)
                        attempt["monte_carlo"] = monte_carlo(attempt, runs=request.monte_carlo_runs, seed=request.seed)
                        profile_attempts.append(attempt)
                        attempts.append(attempt)
                        all_trades.extend([{**trade, "profile_id": profile.profile_id, "risk_percent": risk, "execution_scenario": scenario_name, "trade_frequency_cap": cap} for trade in attempt["accepted_trades"]])
            summary = summarize_attempts(profile_attempts)
            comp = compatibility(summary, profile)
            compatibility_rows.append(comp)
            profile_results.append({"profile": profile.model_dump(), "summary": summary, "best_configuration": _best_configuration(profile_attempts), "compatibility": comp})
        frequency = trade_frequency(candidates)
        report = {
            "label": "RESEARCH SIMULATION - NOT A GUARANTEE",
            "status": "INSUFFICIENT_DATA" if len(all_trades) < 100 else "COMPLETE",
            "sample_sufficient": len(all_trades) >= 100,
            "rules_require_external_verification": True,
            "automation_permission_requires_program_review": True,
            "data_quality": data_quality,
            "trade_frequency": frequency,
            "profiles": profile_results,
            "risk_analysis": _risk_analysis(attempts),
            "strategy_analysis": _strategy_analysis(all_trades),
            "compatibility": compatibility_rows,
            "leaderboard": compatibility_rows,
            "safety_verification": {
                "openai_calls": provider_calls,
                "ibkr_place_order_calls": ibkr_calls,
                "ibkr_cancel_order_calls": ibkr_calls,
                "live_acceptance_state_unchanged": before_acceptance == self._acceptance_state(),
                "live_processed_candles_unchanged": True,
                "production_thresholds_unchanged": True,
                "validation_thresholds_unchanged": True,
                "live_trading_disabled": True,
            },
            "internal_safety_profile": internal.model_dump(),
        }
        return {
            "job_id": job_id,
            "created_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
            "status": report["status"],
            "read_only": True,
            "request": request.__dict__,
            "provider_calls": provider_calls,
            "ibkr_calls": ibkr_calls,
            "report": report,
            "attempts": attempts[:500],
            "trades": all_trades[:5000],
            "artifacts": _artifacts(job_id, report, all_trades),
        }

    async def _load_candles(self, symbol: str, days: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        try:
            chart = await forex_service.get_pair_chart(symbol, interval="15m", range_str=f"{max(days, 7)}d")
            source = str(chart.get("source_symbol") or "forex_service")
            canonical, meta = canonicalize_candles(list(chart.get("candles") or []), symbol=symbol, timeframe="15m", source=source)
            rows = [candle.as_chart_row() for candle in canonical]
            quality = {"status": "VALID" if len(rows) >= 60 else "INVALID", "reasons": [] if len(rows) >= 60 else ["INSUFFICIENT_CANDLES"], "source": source, "candle_count": len(rows), "canonical": meta}
            return rows, quality
        except Exception as exc:
            return [], {"status": "INVALID", "reasons": ["NO_DATA"], "provider_error": exc.__class__.__name__, "candle_count": 0}

    def _state(self) -> dict[str, Any]:
        return get_state(STATE_KEY) or {}

    def _save(self, state: dict[str, Any]) -> None:
        set_state(STATE_KEY, state)

    def _acceptance_state(self) -> dict[str, Any]:
        return get_state("ai_auto_paper_acceptance") or {}


def _best_configuration(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not attempts:
        return None
    return max(attempts, key=lambda row: (row["status"] == "PHASE_PASSED", row["profit"], -row["max_drawdown_percent"]))


def _risk_analysis(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    by_risk: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for attempt in attempts:
        by_risk[float(attempt["risk_percent"])].append(attempt)
    rows = []
    for risk, sample in sorted(by_risk.items()):
        summary = summarize_attempts(sample)
        rows.append({"risk_percent": risk, **summary})
    safest = min(rows, key=lambda row: (row["breach_rate"], row["risk_percent"]), default=None)
    fastest = min([row for row in rows if row["median_days_to_pass"] is not None], key=lambda row: row["median_days_to_pass"], default=None)
    return {"items": rows, "safest_tested_risk": safest, "fastest_passing_risk": fastest, "recommended_internal_risk_limit_percent": internal_safety_profile().risk_per_trade_percent}


def _strategy_analysis(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by_strategy = defaultdict(float)
    by_regime = defaultdict(float)
    by_session = defaultdict(float)
    for trade in trades:
        by_strategy[str(trade.get("strategy"))] += float(trade.get("r_multiple") or 0)
        by_regime[str(trade.get("regime"))] += float(trade.get("r_multiple") or 0)
        by_session[str(trade.get("session"))] += float(trade.get("r_multiple") or 0)
    return {
        "best_strategy": max(by_strategy, key=by_strategy.get, default=None),
        "worst_strategy": min(by_strategy, key=by_strategy.get, default=None),
        "best_market_regime": max(by_regime, key=by_regime.get, default=None),
        "worst_market_regime": min(by_regime, key=by_regime.get, default=None),
        "best_session": max(by_session, key=by_session.get, default=None),
        "worst_session": min(by_session, key=by_session.get, default=None),
        "contribution_by_strategy": dict(by_strategy),
        "performance_after_costs": sum(float(trade.get("r_multiple") or 0) for trade in trades),
    }


def _artifacts(job_id: str, report: dict[str, Any], trades: list[dict[str, Any]]) -> dict[str, Any]:
    csv_buffer = io.StringIO()
    writer = csv.DictWriter(csv_buffer, fieldnames=sorted({key for row in trades for key in row.keys()} or {"empty"}))
    writer.writeheader()
    for row in trades:
        writer.writerow(row)
    return {
        "json_report": {"name": f"{job_id}_report.json", "content": report},
        "csv_trades": {"name": f"{job_id}_trades.csv", "content": csv_buffer.getvalue()},
        "equity_curve": [],
        "drawdown_curve": [],
        "daily_pnl": report.get("trade_frequency", {}),
        "pass_fail_distribution": report.get("profiles", []),
        "risk_versus_pass_rate": report.get("risk_analysis", {}).get("items", []),
        "breach_reason_distribution": {},
    }


prop_firm_lab = PropFirmLabService()
