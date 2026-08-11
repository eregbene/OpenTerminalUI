from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from backend.adaptive_management.tp_protection import classify_stop_quality_v2, construct_dynamic_stop
from backend.market_structure.engine import analyze_bars
from backend.brokers.mt5 import account_registry
from backend.brokers.mt5.adapter import MT5Adapter, mt5_adapter
from backend.brokers.mt5.config import MT5Config
from backend.brokers.mt5.config import mt5_config
from backend.brokers.mt5.candidate_evaluation import capture_cycle_candidate_evaluations
from backend.brokers.mt5.confidence import compute_trade_confidence, is_autonomous_eligible, rank_candidates
from backend.mt5_strategies.context import build_strategy_context, cheap_prefilter, quick_regime, summarize_smc_evidence
from backend.mt5_strategies.families import evaluate_all
from backend.mt5_strategies.fusion import build_candidates
from backend.mt5_strategies.models import ACTIVE_MT5, all_strategy_ids, multi_strategy_enabled, regime_compatible
from backend.brokers.mt5.execution import MT5ExecutionService
from backend.brokers.mt5.models import MT5ForexInstrument, MT5TradeIntent
from backend.brokers.mt5.persistence import confidence_memory_for_symbol, persist_cycle_result, trade_performance_summary, update_trade_history, update_trade_reconciliation
from backend.brokers.mt5.prop_risk import risk_status
from backend.brokers.mt5.risk_budget import effective_risk_budget_usd
from backend.brokers.mt5.take_profit import MIN_REWARD_MULTIPLE, select_take_profit
from backend.decision_context.service import decision_context_service
from backend.economic_intelligence import macro_context
from backend.economic_intelligence.service import economic_intelligence_service
from backend.intelligence.trading.persistence import get_state, set_state, utcnow
from backend.portfolio_execution.service import portfolio_manager

logger = logging.getLogger(__name__)

# Part 5: bounded concurrency for the pure in-memory (no MT5/adapter calls) multi-strategy
# analysis phase only. Candle/quote fetching always stays fully serial -- see _screen()'s
# comment for why concurrent calls into the MetaTrader5 module are not safe to introduce.
_MULTI_STRATEGY_CONCURRENCY = int(os.getenv("MT5_MULTI_STRATEGY_CONCURRENCY", "6"))


@dataclass
class MT5AutonomousState:
    scheduler_owner: str | None = None
    scheduler_started_at: datetime | None = None
    scheduler_running: bool = False
    current_state: str = "idle"
    current_cycle_id: str | None = None
    last_cycle_time: datetime | None = None
    next_cycle_time: datetime | None = None
    last_result: dict[str, Any] | None = None
    emergency_disabled: bool = False
    unresolved_submission: bool = False
    cycles: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    trades: list[dict[str, Any]] = field(default_factory=list)


class MT5AutonomousTradingService:
    def __init__(self, adapter: MT5Adapter | None = None) -> None:
        self.adapter = adapter or mt5_adapter
        self.config: MT5Config = self.adapter.config
        self.execution = MT5ExecutionService(self.adapter)
        self.state = MT5AutonomousState()
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        self._cycle_lock = asyncio.Lock()
        # Per-cycle only (Part 2): reset at the top of every _screen() call, never carried across
        # cycles -- avoids the "stale cross-cycle cache" risk called out in the spec. A cache miss
        # (including when _screen() itself was never run, e.g. the standalone entry_quality_score
        # API) just falls back to _entry_quality_score's original fetch+analyze behavior.
        self._cycle_context_cache: dict[str, Any] = {}
        self._last_screen_counters: dict[str, Any] = {}
        # Part 13: risk-metadata health cache (broker_symbol -> classify_risk_metadata_status()
        # result), refreshed at scheduler start and on-demand via refresh_risk_metadata_health()
        # -- NEVER re-audited inside a hot cycle (that would defeat Part 1's speed work). Empty
        # cache means "no known issue" (fail open on ABSENCE of audit data, since the per-
        # candidate risk sizing in _submit() is the real, always-on safety net regardless of
        # whether this periodic audit has run yet).
        self._risk_metadata_health: dict[str, str] = {}
        self._risk_metadata_health_refreshed_at: datetime | None = None

    async def refresh_risk_metadata_health(self) -> dict[str, Any]:
        """Part 12/13: read-only diagnostic audit of every symbol in the current MT5 forex
        universe's risk-sizing metadata (order_calc_profit/contract_size/tick_value agreement,
        currency-conversion resolvability, quorum). Updates self._risk_metadata_health so
        _screen() can exclude only the specific unhealthy symbols from execution eligibility
        (RISK_METADATA_UNHEALTHY) -- every healthy symbol is completely unaffected, and a
        failure here never blocks or slows a trading cycle (best-effort, exceptions caught)."""
        from backend.brokers.mt5.risk_calculator import RISK_METADATA_DEGRADED_TWO_METHOD, RISK_METADATA_OK, audit_forex_universe

        try:
            universe = await self.adapter.forex_universe()
            account = await self.adapter.mt5_account()
            mt5_client = self.execution._native_client()
            results = await audit_forex_universe(instruments=universe.items, account_currency=account.currency or "USD", adapter=self.adapter, mt5_client=mt5_client)
        except Exception as exc:
            logger.warning("MT5 risk-metadata universe audit failed (non-fatal, existing health cache retained): %s", exc.__class__.__name__)
            return {"status": "error", "reason": exc.__class__.__name__}
        health = {row["symbol"]: row["status"] for row in results if row.get("symbol")}
        self._risk_metadata_health = health
        self._risk_metadata_health_refreshed_at = utcnow()
        unhealthy = {symbol: status for symbol, status in health.items() if status not in {RISK_METADATA_OK, RISK_METADATA_DEGRADED_TWO_METHOD}}
        if unhealthy:
            logger.warning("MT5 risk-metadata audit: %d/%d symbol(s) unhealthy: %s", len(unhealthy), len(results), unhealthy)
        return {"status": "ok", "refreshed_at": self._risk_metadata_health_refreshed_at.isoformat(), "symbols_audited": len(results), "unhealthy": unhealthy, "results": results}

    async def start(self, *, owner: str | None = None) -> bool:
        if hasattr(self.adapter, "reload_config"):
            self.adapter.reload_config()
        else:
            self.adapter.config = mt5_config()
        self.config = self.adapter.config
        self.execution = MT5ExecutionService(self.adapter)
        if not self.config.enabled:
            logger.warning("MT5 autonomous scheduler not started: MT5 disabled")
            return False
        if self._task and not self._task.done():
            return True
        scheduler_owner = owner or os.getenv("MT5_SCHEDULER_OWNER") or f"mt5:{socket.gethostname()}:{os.getpid()}"
        if not self._acquire_lock(scheduler_owner):
            logger.warning("MT5 autonomous scheduler not started: duplicate owner exists")
            return False
        self._load_state()
        self._stop_event = asyncio.Event()
        self.state.scheduler_owner = scheduler_owner
        self.state.scheduler_started_at = utcnow()
        self.state.next_cycle_time = _next_m5_run()
        self.state.current_state = "sleeping"
        self._task = asyncio.create_task(self._loop(scheduler_owner), name="mt5-autonomous-scheduler")
        logger.warning("MT5 autonomous scheduler started owner=%s next=%s", scheduler_owner, self.state.next_cycle_time.isoformat())
        # Part 13 "at startup": best-effort, fire-and-forget -- never delays scheduler start, and
        # a failure here (caught inside refresh_risk_metadata_health) never blocks trading. The
        # first real cycle may run before this finishes; that is fine, since the per-candidate
        # risk sizing in _submit() is the always-on safety net regardless of this cache's state.
        asyncio.create_task(self.refresh_risk_metadata_health(), name="mt5-risk-metadata-startup-audit")
        return True

    async def stop(self) -> None:
        if not self._task:
            return
        logger.warning("MT5 autonomous scheduler stopping")
        if self._stop_event:
            self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        self.state.current_state = "idle"
        self._release_lock()
        self._persist_state()
        logger.warning("MT5 autonomous scheduler stopped")

    async def _loop(self, owner: str) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            self._heartbeat(owner)
            next_run = _next_m5_run()
            self.state.next_cycle_time = next_run
            self.state.current_state = "sleeping"
            logger.warning("MT5 scheduler sleeping until next completed M5 candle: %s", next_run.isoformat())
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(0, (next_run - utcnow()).total_seconds()))
                break
            except asyncio.TimeoutError:
                pass
            logger.warning("MT5 cycle started")
            try:
                result = await self.run_cycle(owner=owner)
                logger.warning("MT5 cycle finished: %s status=%s openai_calls=%s order_send_calls=%s", result.get("cycle_id"), result.get("status"), result.get("openai_calls"), result.get("order_send_calls"))
            except Exception as exc:
                logger.exception("MT5 cycle failed: %s", exc.__class__.__name__)
                self.state.last_result = {"status": "ERROR", "error": exc.__class__.__name__, "cycle_id": self.state.current_cycle_id}

    async def run_cycle(self, *, owner: str = "local", dry_run: bool = False) -> dict[str, Any]:
        async with self._cycle_lock:
            self._assert_owner(owner)
            self._load_state()
            cycle_time = _completed_m5_time()
            cycle_id = f"MT5_M5_{cycle_time.strftime('%Y%m%d%H%M')}"
            if not dry_run:
                self.state.current_cycle_id = cycle_id
                self.state.last_cycle_time = utcnow()
                self.state.current_state = "running"
            # Part 19: per-cycle timing instrumentation. `_lap(name)` records elapsed wall time
            # since the previous lap under `name`; `_finalize(result)` attaches the full stage
            # breakdown plus total_cycle_ms and the _screen() discovery/pre-filter/analysis
            # counters to whichever result dict this cycle ends up returning.
            cycle_start = time.perf_counter()
            stage_ms: dict[str, float] = {}
            self._last_screen_counters = {}  # avoid attaching a PREVIOUS cycle's stats to this one if _screen() never runs (e.g. TRADING_DISABLED)
            _last_lap = cycle_start
            # Real, measured OpenAI/LLM call telemetry (never a hardcoded literal) -- the MT5
            # autonomous path only ever touches the OpenAI provider via
            # backend.economic_intelligence.macro_context.classify(), and only when a caller
            # opts in with enable_llm_macro_advisory=True. This cycle exclusively calls
            # economic_intelligence_service.evaluate_entry_deterministic(), which hardcodes that
            # to False, so the measured delta below is expected to always be 0 for MT5 -- but it
            # is MEASURED, not assumed, so a future accidental LLM call anywhere in this cycle's
            # call graph would show up here rather than being silently invisible.
            _llm_calls_before = macro_context.classify_call_count()

            def _lap(name: str) -> None:
                nonlocal _last_lap
                now = time.perf_counter()
                stage_ms[name] = round((now - _last_lap) * 1000, 1)
                _last_lap = now

            def _finalize(result: dict[str, Any]) -> dict[str, Any]:
                result["timing_ms"] = {**stage_ms, "total_cycle_ms": round((time.perf_counter() - cycle_start) * 1000, 1)}
                result["screening_stats"] = dict(self._last_screen_counters)
                openai_calls_measured = macro_context.classify_call_count() - _llm_calls_before
                result["openai_calls"] = openai_calls_measured
                if openai_calls_measured > 0:
                    # Loud, explicit invariant violation (Part 6): the MT5 autonomous pipeline
                    # must be genuinely LLM-free end-to-end. This does not retroactively undo a
                    # decision already made this cycle, but it must never pass silently.
                    logger.error("MT5 ARCHITECTURE INVARIANT VIOLATED: autonomous cycle %s made %d OpenAI call(s) -- MT5 execution must be LLM-free end-to-end", cycle_id, openai_calls_measured)
                return result

            if not dry_run and cycle_id in set(_stored().get("processed_candles") or []):
                result = _finalize({"cycle_id": cycle_id, "status": "SKIPPED_DUPLICATE_CANDLE", "order_send_calls": 0})
                self._record_cycle(result, dry_run=dry_run)
                return result
            blockers = await self._global_blockers()
            _lap("global_blockers")
            if blockers:
                result = _finalize({"cycle_id": cycle_id, "status": "TRADING_DISABLED", "blockers": blockers, "order_send_calls": 0})
                self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                return result
            universe = await self.adapter.forex_universe()
            candidates = await self._screen(universe.items, cycle_id=cycle_id)
            _lap("screening")
            eligible = [row for row in candidates if not row["rejection_reasons"]]
            if not eligible:
                result = _finalize({"cycle_id": cycle_id, "status": "NO_TRADE", "symbols_discovered": universe.total_forex_pairs, "eligible_symbols": len([i for i in universe.items if i.eligible]), "candidates": candidates[:25], "order_send_calls": 0})
                self._record_cycle(result, dry_run=dry_run)
                return result

            # --- Deterministic confidence scoring + ranking (no AI/provider call). ---
            # Deep-scores the top-K candidates by the existing multi-timeframe ranking_score
            # (M15/H1/H4 trend alignment, from _score_candidate), then picks the best
            # risk-adjusted candidate whose trade_confidence_score clears the configured
            # threshold (default 75). See backend/brokers/mt5/confidence.py.
            top_k = sorted(eligible, key=lambda row: row["ranking_score"], reverse=True)[: self.config.confidence_top_k_candidates]
            ranked = await self._rank_candidates_by_confidence(cycle_id, top_k)
            _lap("confidence_scoring")
            # Stage 1 safety gate (Parts 20/22): a SHADOW_MT5/DISABLED strategy's candidate is
            # still fully confidence-scored, ranked, and persisted for calibration above -- it
            # is simply never eligible to become `best`/submitted. Defaults to ACTIVE_MT5 for
            # any candidate that predates this field (MTFAI1's own rows always carry it
            # explicitly now, but this keeps older persisted shapes safe too).
            eligible_for_execution = []
            for row in ranked:
                confidence_ok = is_autonomous_eligible(row["trade_confidence"]["overall_score"], min_trade_confidence=self.config.min_trade_confidence)
                activation = row.get("strategy_activation", ACTIVE_MT5)
                shadow = activation != ACTIVE_MT5
                if confidence_ok and shadow:
                    reason = "SHADOW_MODE" if activation == "SHADOW_MT5" else "STRATEGY_DISABLED" if activation == "DISABLED" else "SHADOW_MODE"
                    row["rejection_reasons"] = sorted(set((row.get("rejection_reasons") or []) + [reason]))
                if confidence_ok and not shadow:
                    eligible_for_execution.append(row)
            self._last_screen_counters["eligible_ge_75"] = sum(1 for row in ranked if is_autonomous_eligible(row["trade_confidence"]["overall_score"], min_trade_confidence=self.config.min_trade_confidence))
            if not eligible_for_execution:
                result = _finalize({"cycle_id": cycle_id, "status": "NO_TRADE", "winner": ranked[0] if ranked else None, "candidates": ranked, "order_send_calls": 0})
                self._record_cycle(result, dry_run=dry_run)
                return result

            best = eligible_for_execution[0]
            for row in eligible_for_execution[1:]:
                row["rejection_reasons"] = sorted(set((row.get("rejection_reasons") or []) + ["LOWER_RANKED_CANDIDATE"]))

            best["candidate_id"] = f"{cycle_id}:{best['broker_symbol']}:{best['context_hash'][:16]}"
            context_risk = await decision_context_service.context_risk(best["canonical_pair"])
            best["decision_context"] = context_risk
            context_blockers = self._context_blockers(context_risk)
            try:
                # evaluate_entry_deterministic (never evaluate_entry) -- this is the ONLY
                # economic-risk entry point the MT5 autonomous path may call. It is structurally
                # LLM-free regardless of ff_openai_macro_classification_enabled (Part: MT5
                # OpenAI removal); the calendar guard (scheduled economic events, central-bank
                # classification) remains fully deterministic and active.
                economic_result = await economic_intelligence_service.evaluate_entry_deterministic(canonical_pair=best["canonical_pair"], direction=best["direction"], candidate_id=best["candidate_id"], spread=float(quote_spread) if (quote_spread := best["context"].get("spread")) else None)
            except Exception as exc:
                logger.warning("Economic intelligence evaluation failed (Forex Factory outage must not block the cycle): %s", exc.__class__.__name__)
                economic_result = {"guard": {"decision": "ALLOW", "reason_codes": [f"ECONOMIC_INTELLIGENCE_UNAVAILABLE:{exc.__class__.__name__}"], "size_multiplier": 1.0}, "calendar": None, "news": None, "macro_advisory": None}
            best["economic_context"] = economic_result
            economic_blockers = self._economic_blockers(economic_result)
            _lap("context_and_economic_risk")
            if context_blockers:
                result = _finalize({"cycle_id": cycle_id, "status": "SKIPPED_CONTEXT_RISK", "winner": best, "candidates": ranked, "context_risk": context_risk, "blockers": context_blockers, "order_send_calls": 0})
                self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                return result
            if economic_blockers:
                result = _finalize({"cycle_id": cycle_id, "status": "SKIPPED_ECONOMIC_RISK", "winner": best, "candidates": ranked, "economic_context": economic_result, "blockers": economic_blockers, "order_send_calls": 0})
                self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                return result
            # Portfolio validation is a SEPARATE, binary permission gate -- independent of
            # signal confidence. A 94-confidence candidate can still be rejected here; its
            # stored trade_confidence_score is never rewritten because of this rejection.
            portfolio_allowed, portfolio_blockers = portfolio_manager.can_open_new_trade()
            _lap("portfolio_risk")
            if not portfolio_allowed:
                best["rejection_reasons"] = sorted(set((best.get("rejection_reasons") or []) + ["PORTFOLIO_RISK_LIMIT"]))
                result = _finalize({"cycle_id": cycle_id, "status": "PORTFOLIO_REJECTED", "winner": best, "candidates": ranked, "blockers": portfolio_blockers, "order_send_calls": 0})
                self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                return result
            # Reward:risk is a pure price-geometry ratio, independent of confidence, so it can
            # be re-verified against a FRESH quote before submitting. stop_loss/take_profit
            # were fixed at screening time against an earlier quote; by the time _submit()
            # re-fetches the entry price, price may have drifted enough that the same fixed
            # SL/TP no longer clear MIN_REWARD_MULTIPLE.
            try:
                fresh_quote = await self.adapter.latest_tick(best["broker_symbol"])
                fresh_entry = fresh_quote.ask if best["direction"] == "LONG" else fresh_quote.bid
                if fresh_entry is not None:
                    stale_stop_distance = abs(fresh_entry - Decimal(str(best["stop_loss"])))
                    stale_target_distance = abs(Decimal(str(best["take_profit"])) - fresh_entry)
                    if stale_stop_distance > 0 and (stale_target_distance / stale_stop_distance) < MIN_REWARD_MULTIPLE:
                        result = _finalize({"cycle_id": cycle_id, "status": "SKIPPED_STALE_RISK_REWARD", "winner": best, "candidates": ranked, "blockers": ["RISK_REWARD_DEGRADED_SINCE_SCREENING"], "order_send_calls": 0})
                        self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                        return result
            except Exception as exc:
                logger.warning("Reward:risk pre-check failed (non-fatal, proceeding to submission): %s", exc.__class__.__name__)
            submission = await self._submit(best, confidence=float(best["trade_confidence"]["overall_score"]), dry_run=dry_run)
            _lap("execution")
            result = _finalize({"cycle_id": cycle_id, "status": submission["status"], "winner": best, "candidates": ranked, "trade": submission, "order_send_calls": submission.get("order_send_calls", 0)})
            self._record_cycle(result, dry_run=dry_run)
            return result

    async def _rank_candidates_by_confidence(self, cycle_id: str, top_k: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deep-scores each of the top-K screened candidates (SMC/ICT structure score,
        recorded symbol/strategy performance, portfolio correlation) into a deterministic
        trade_confidence_score, then returns them ranked best-first. Pure local computation
        and DB reads only -- no broker mutation, no AI/provider call."""
        try:
            exposure = portfolio_manager.exposure()
        except Exception:
            exposure = {}
        scored: list[dict[str, Any]] = []
        for candidate in top_k:
            candidate["candidate_id"] = f"{cycle_id}:{candidate['broker_symbol']}:{candidate['context_hash'][:16]}"
            # Preserve the pre-confidence multi-timeframe trend score (screener output) before
            # it's overwritten below -- the calibration layer's candidate_evaluation capture
            # (Part 1) needs both, and ranking_score is about to become the confidence score.
            candidate["raw_trend_score"] = candidate.get("ranking_score")
            candidate["entry_quality"] = await self._entry_quality_score(candidate)
            try:
                symbol_memory, global_memory = confidence_memory_for_symbol(candidate["canonical_pair"])
            except Exception as exc:
                logger.warning("MT5 confidence memory lookup unavailable: %s", exc.__class__.__name__)
                symbol_memory, global_memory = None, None
            penalty_points, correlated_symbols = _correlation_penalty(candidate, exposure)
            confidence = compute_trade_confidence(
                candidate=candidate,
                entry_quality=candidate["entry_quality"],
                symbol_memory=symbol_memory,
                global_memory=global_memory,
                correlation_penalty_points=penalty_points,
                correlated_symbols=correlated_symbols,
            )
            candidate["trade_confidence"] = confidence
            candidate["ranking_score"] = confidence["overall_score"]
            if not is_autonomous_eligible(confidence["overall_score"], min_trade_confidence=self.config.min_trade_confidence):
                candidate["rejection_reasons"] = sorted(set((candidate.get("rejection_reasons") or []) + ["BELOW_CONFIDENCE_THRESHOLD"]))
            scored.append(candidate)
        return rank_candidates(scored)

    async def _global_blockers(self) -> list[str]:
        blockers = await self.execution.safety_blockers()
        store = _stored()
        if store.get("emergency_disabled"):
            blockers.append("EMERGENCY_DISABLED")
        if self.config.manual_acceptance_enabled:
            blockers.append("MANUAL_ACCEPTANCE_ENABLED")
        account = await self.adapter.mt5_account()
        risk = risk_status(self.config, equity=account.equity, balance=account.balance)
        blockers.extend(risk.get("blockers") or [])
        positions = await self.adapter.mt5_positions()
        owned = [p for p in positions if p.magic == self.config.bensim_magic or str(p.comment or "").startswith("BENSIM_AUTO")]
        if len(owned) >= self.config.max_open_positions:
            blockers.append("MAX_OPEN_POSITIONS")
        if any(p.sl in {None, Decimal("0")} or p.tp in {None, Decimal("0")} for p in owned):
            blockers.append("UNPROTECTED_POSITION")
        return blockers

    async def _screen(self, instruments: list[MT5ForexInstrument], *, cycle_id: str = "") -> list[dict[str, Any]]:
        """Two-phase screen (Parts 1-5). Phase 1 (this loop) stays fully serial for every MT5
        adapter call -- quote/candle fetching is the one part of this cycle that must never run
        concurrently (the underlying MetaTrader5 terminal API is not safe for concurrent calls
        from multiple threads). Within phase 1, a cheap strategy-neutral pre-filter (Part 3) and
        a cheap regime pre-check (quick_regime -- pure candle math, no analyze_bars) decide
        whether the expensive multi-timeframe SMC context is worth building at all for this
        instrument this cycle; MTFAI1's own scoring is untouched either way.

        Phase 2 runs the pure, in-memory (no adapter calls, no shared mutable state) multi-
        strategy analysis for every instrument that survived phase 1, bounded and concurrent via
        asyncio.to_thread (Part 5) -- this is the ONLY thing parallelized. self._cycle_context_cache
        is populated here so _entry_quality_score can reuse an already-computed M15 SMC snapshot
        instead of re-running analyze_bars for whichever candidates make top-K (Part 2)."""
        rows: list[dict[str, Any]] = []
        self._cycle_context_cache = {}
        counters = {"symbols_discovered": len(instruments), "symbols_after_prefilter": 0, "full_smc_analyses": 0, "strategy_evaluations": 0}
        market_data_ms = 0.0
        prefilter_regime_ms = 0.0
        account = await self.adapter.mt5_account()
        open_positions = await self.adapter.mt5_positions()
        open_symbols = {p.symbol.upper() for p in open_positions if p.volume != 0}
        multi_strategy_on = multi_strategy_enabled()

        pending: list[dict[str, Any]] = []
        row_by_symbol: dict[str, dict[str, Any]] = {}

        for instrument in instruments:
            reasons = list(instrument.ineligibility_reasons)
            if not instrument.eligible:
                reasons.append("SYMBOL_INELIGIBLE")
            if instrument.broker_symbol.upper() in open_symbols:
                reasons.append("EXISTING_POSITION")
            # Part 13: a symbol flagged unhealthy by the periodic/startup risk-metadata audit
            # (backend.brokers.mt5.risk_calculator.audit_forex_universe) is excluded from
            # execution eligibility ONLY -- it still gets fully screened/scored below (Part 14:
            # confidence must never be lowered because of a sizing-metadata problem), just never
            # becomes `best`. Every other symbol is completely unaffected.
            if self._risk_metadata_health.get(instrument.broker_symbol) in {"CRITICAL_MISMATCH", "UNSUPPORTED_METHOD"}:
                reasons.append("RISK_METADATA_UNHEALTHY")
            _fetch_t0 = time.perf_counter()
            try:
                quote = await self.adapter.latest_tick(instrument.broker_symbol)
                m15 = await self.adapter.candles(instrument.broker_symbol, "M15", count=100)
                h1 = await self.adapter.candles(instrument.broker_symbol, "H1", count=100)
                h4 = await self.adapter.candles(instrument.broker_symbol, "H4", count=100)
            except Exception as exc:
                market_data_ms += (time.perf_counter() - _fetch_t0) * 1000
                rows.append(_candidate(instrument, reasons + [f"DATA_UNAVAILABLE:{exc.__class__.__name__}"]))
                continue
            market_data_ms += (time.perf_counter() - _fetch_t0) * 1000
            if len(m15) < 60 or len(h1) < 50 or len(h4) < 30:
                reasons.append("HISTORY_UNAVAILABLE")
                rows.append(_candidate(instrument, reasons))
                continue
            if quote.bid is None or quote.ask is None:
                reasons.append("NO_QUOTE")
                rows.append(_candidate(instrument, reasons))
                continue
            score, direction, geometry = _score_candidate(quote, m15, h1, h4, instrument.symbol)
            if score < 70:
                reasons.append("WEAK_CONSENSUS")

            m15_rows = [row.model_dump(mode="json") for row in m15]
            should_analyze = False
            regime_info: dict[str, Any] | None = None
            multi_strategy_regime = "insufficient_data"
            if multi_strategy_on:
                _prefilter_t0 = time.perf_counter()
                try:
                    should_analyze, _prefilter_reason = cheap_prefilter(already_ineligible=bool(reasons), m15_rows=m15_rows, spread=quote.spread)
                    if should_analyze:
                        regime_info = quick_regime(m15_rows)
                        multi_strategy_regime = str(regime_info.get("regime") or "insufficient_data")
                        # Part 4: no compatible family for this regime -> the full SMC context
                        # would be built only to feed strategies that evaluate_all() would skip
                        # anyway, so skip building it at all (strategy-neutral: this checks EVERY
                        # non-mtfai1 family's regime compatibility, never MTFAI1's own trend/SMA).
                        if not any(regime_compatible(sid, multi_strategy_regime) for sid in all_strategy_ids()):
                            should_analyze = False
                except Exception as exc:
                    logger.warning("MT5 multi-strategy pre-filter failed for %s (MTFAI1 unaffected): %s", instrument.broker_symbol, exc.__class__.__name__)
                    should_analyze = False
                finally:
                    prefilter_regime_ms += (time.perf_counter() - _prefilter_t0) * 1000

            context = {
                "symbol": instrument.canonical_pair,
                "broker_symbol": instrument.broker_symbol,
                "timestamp": m15[-1].time.isoformat() if m15 else None,
                "bid": str(quote.bid),
                "ask": str(quote.ask),
                "spread": str(quote.spread),
                "direction": direction,
                "score": score,
                "provider_policy": "MT5_ONLY",
                "timeframe_context": {"policy": "MT5_ONLY", "timeframes": ["M15", "H1", "H4"]},
                "broker_server": account.server,
                "account_mode": self.config.account_mode,
                "strategy_id": "mtfai1",
                "strategy_family": "trend_multi_timeframe",
                "regime": multi_strategy_regime,
                "smc_evidence": {},
                **geometry,
            }
            row = {**_candidate(instrument, reasons), "direction": direction, "ranking_score": score, "context": context, "context_hash": _hash(context), "strategy_activation": "ACTIVE_MT5", **geometry}
            rows.append(row)

            if should_analyze:
                counters["symbols_after_prefilter"] += 1
                row_by_symbol[instrument.broker_symbol] = row
                pending.append({
                    "instrument": instrument,
                    "m15_rows": m15_rows,
                    "h1_rows": [c.model_dump(mode="json") for c in h1],
                    "h4_rows": [c.model_dump(mode="json") for c in h4],
                    "bid": quote.bid, "ask": quote.ask, "spread": quote.spread,
                    "regime_info": regime_info,
                })

        _analysis_t0 = time.perf_counter()
        if pending:
            semaphore = asyncio.Semaphore(max(1, _MULTI_STRATEGY_CONCURRENCY))

            async def _run(item: dict[str, Any]):
                async with semaphore:
                    return await asyncio.to_thread(
                        _build_multi_strategy_analysis, item["instrument"], cycle_id,
                        item["m15_rows"], item["h1_rows"], item["h4_rows"],
                        item["bid"], item["ask"], item["spread"], item["regime_info"],
                    )

            analysis_results = await asyncio.gather(*(_run(item) for item in pending), return_exceptions=True)
            for item, result in zip(pending, analysis_results):
                instrument = item["instrument"]
                if isinstance(result, BaseException):
                    logger.warning("MT5 multi-strategy evaluation failed for %s (MTFAI1 unaffected): %s", instrument.broker_symbol, result.__class__.__name__)
                    continue
                regime, smc_evidence, candidates, ctx, signal_count = result
                counters["full_smc_analyses"] += 1
                counters["strategy_evaluations"] += signal_count
                if ctx is not None:
                    self._cycle_context_cache[instrument.broker_symbol] = ctx
                mtfai1_row = row_by_symbol.get(instrument.broker_symbol)
                if mtfai1_row is not None:
                    mtfai1_row["context"]["regime"] = regime
                    mtfai1_row["context"]["smc_evidence"] = smc_evidence
                    mtfai1_row["context_hash"] = _hash(mtfai1_row["context"])
                for candidate in candidates:
                    candidate.setdefault("rejection_reasons", [])
                    candidate["context"]["smc_evidence"] = smc_evidence
                    rows.append(candidate)

        counters["candidates_total"] = len(rows)
        counters["timing_ms"] = {
            "market_data_retrieval": round(market_data_ms, 1),
            "prefilter_and_regime_detection": round(prefilter_regime_ms, 1),
            "structure_analysis_strategy_evaluation_fusion_concurrent": round((time.perf_counter() - _analysis_t0) * 1000, 1),
        }
        self._last_screen_counters = counters
        return sorted(rows, key=lambda row: row["ranking_score"], reverse=True)

    def _risk_budget_adjustment(self, account: Any, economic_context: dict[str, Any], confidence: float | None) -> dict[str, Any]:
        """Real, live inputs for the effective-risk-budget scaling layer (see
        risk_budget.py). drawdown_pct is deliberately a simple, honest equity-vs-balance
        proxy (unrealized drawdown only) -- true peak-equity drawdown tracking would need a
        persisted high-water mark and is noted as a follow-up, not implemented here."""
        try:
            balance = float(account.balance or 0)
            equity = float(account.equity or 0)
            drawdown_pct = max(0.0, (balance - equity) / balance * 100.0) if balance > 0 else 0.0
        except Exception:
            drawdown_pct = 0.0
        try:
            risk_snapshot = portfolio_manager.risk()
            open_risk = float(risk_snapshot.get("open_risk") or 0)
            portfolio_exposure_ratio = open_risk / float(self.config.max_total_open_risk_usd) if self.config.max_total_open_risk_usd else 0.0
        except Exception:
            portfolio_exposure_ratio = 0.0
        try:
            exposure = portfolio_manager.exposure()
            max_currency_exposure = max((abs(row.get("net", 0)) for row in (exposure.get("currency") or {}).values()), default=0.0)
            correlated_cap = float(self.config.max_total_open_risk_usd) * _env_float("MAX_CORRELATED_OPEN_RISK_PCT", 0.75) / max(0.01, self.config.max_total_open_risk_percent)
            correlated_exposure_ratio = max_currency_exposure / correlated_cap if correlated_cap else 0.0
        except Exception:
            correlated_exposure_ratio = 0.0
        guard_decision = str(((economic_context.get("guard") or {}).get("decision")) or "ALLOW").upper()
        economic_risk_level = {"ALLOW": "none", "REDUCE_SIZE": "medium", "DELAY": "high", "BLOCK": "high", "MANAGE_EXISTING_ONLY": "medium"}.get(guard_decision, "none")
        return {
            "drawdown_pct": drawdown_pct,
            "portfolio_exposure_ratio": portfolio_exposure_ratio,
            "correlated_exposure_ratio": correlated_exposure_ratio,
            "economic_risk_level": economic_risk_level,
            # effective_risk_budget_usd expects 0-1; trade_confidence_score is 0-100.
            "strategy_confidence": (confidence / 100.0) if confidence is not None else None,
        }

    async def _entry_quality_score(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Decomposed, explainable entry score (SMC/ICT structure -- order blocks, FVGs,
        liquidity sweeps, BOS/CHoCH, dealing ranges) for decision support only. Computed AFTER
        the candidate is already selected by the existing ranking/AI-decision pipeline -- this
        never changes which candidate is chosen or whether the trade proceeds; it exists purely
        so the entry can be explained (Part 2/8) and the decomposed score can be persisted onto
        the trade record for later probability analysis (Part 1/4). A failure here degrades to
        an empty score, never blocks the cycle.

        Part 2 (redundant analyze_bars elimination): if this cycle's _screen() already built a
        multi-strategy StrategyContext for this symbol, its M15 SMC snapshot is REUSED here
        as-is -- analyze_bars is a pure function of the candle rows, so the reused snapshot is
        byte-identical to what a fresh analyze_bars() call on the same rows would produce; this
        changes nothing about the resulting score, only how many times it's computed. Falls
        back to the original fetch+analyze path on any cache miss (e.g. this symbol's cheap
        pre-filter skipped multi-strategy analysis, or entry_quality_score() was called outside
        a cycle)."""
        cached_ctx = self._cycle_context_cache.get(candidate.get("broker_symbol"))
        if cached_ctx is not None and cached_ctx.m15_snapshot is not None:
            snapshot = cached_ctx.m15_snapshot
            rows = cached_ctx.m15_rows
        else:
            try:
                bars = await self.adapter.candles(candidate["broker_symbol"], "M15", count=100)
                rows = [row.model_dump(mode="json") for row in bars]
                snapshot = analyze_bars(rows, symbol=candidate["broker_symbol"], timeframe="M15")
            except Exception as exc:
                return {"status": "unavailable", "reason": exc.__class__.__name__, "total_score": None, "components": [], "positive_contributors": [], "negative_contributors": [], "reason_codes": [], "explanations": []}
        score = snapshot.score
        components = [component.model_dump() for component in (score.score_components if score else [])]
        positive = [c["name"] for c in components if c["value"] >= 0.5]
        negative = [c["name"] for c in components if c["value"] < 0.5]
        # Data-sufficiency heuristic, NOT a statistically calibrated confidence interval --
        # tightens only with how much bar history fed the structural analysis. A real calibrated
        # interval requires the probability engine (Part 4) comparing scores to actual outcomes
        # across enough closed trades; until then this is honestly a completeness proxy.
        band = 0.05 if len(rows) >= 100 else 0.10 if len(rows) >= 60 else 0.20
        total = score.total_score if score else 0.0
        return {
            "status": "ok",
            "total_score": total,
            "confidence_interval": [round(max(0.0, total - band), 4), round(min(1.0, total + band), 4)],
            "confidence_interval_basis": "bar_data_completeness_heuristic",
            "components": components,
            "positive_contributors": positive,
            "negative_contributors": negative,
            "reason_codes": [f"STRUCTURE_{name.upper()}_CONFIRMED" for name in positive] + [f"STRUCTURE_{name.upper()}_ABSENT" for name in negative],
            "explanations": snapshot.explanations or [],
            "trend_state": snapshot.trend.state if snapshot.trend else None,
            "bars_analyzed": len(rows),
        }

    async def entry_quality_score(self, symbol: str) -> dict[str, Any]:
        """Public, API-facing entry point for GET /api/intelligence/entry-score -- computes the
        same decision-support-only structural score _entry_quality_score already produces for
        the live pipeline's picked candidate, on demand for any requested symbol."""
        return await self._entry_quality_score({"broker_symbol": symbol.upper()})

    async def _submit(self, candidate: dict[str, Any], *, confidence: float | None = None, dry_run: bool = False) -> dict[str, Any]:
        # Part 8 hard guard: multi-strategy activation (STRATEGY_FAMILIES defaults,
        # MT5_STRATEGY_ACTIVATION_<ID>, MT5_MULTI_STRATEGY_ENABLED) is a strategy-selection
        # concern that never touches config.live_trading_enabled/account_mode -- this assertion
        # is defense-in-depth confirming that remains true no matter which strategy produced
        # `candidate`, on top of the existing, independent self.execution.safety_blockers()
        # check that already runs before _screen() every cycle (Part 6/8's "no configuration
        # change should accidentally enable real-money execution").
        if self.config.live_trading_enabled or self.config.account_mode != "DEMO":
            return {"status": "REJECTED", "reasons": ["LIVE_TRADING_BLOCKED"], "order_send_calls": 0}
        account = await self.adapter.mt5_account()
        symbol = await self.adapter.symbol_info(candidate["broker_symbol"])
        quote = await self.adapter.latest_tick(candidate["broker_symbol"])
        entry = quote.ask if candidate["direction"] == "LONG" else quote.bid
        if entry is None:
            return {"status": "REJECTED", "reasons": ["NO_ENTRY_PRICE"], "order_send_calls": 0}
        economic_context_preview = candidate.get("economic_context") or {}
        risk_adjustment = self._risk_budget_adjustment(account, economic_context_preview, confidence)
        base_risk_budget = (account.equity * Decimal(str(self.config.risk_percent_per_trade)) / Decimal("100")).quantize(Decimal("0.01"))
        _, risk_adjustment_detail = effective_risk_budget_usd(base_risk_budget, **risk_adjustment)
        try:
            account_fingerprint = account_registry.fingerprint_account(account).fingerprint_hash
        except Exception:
            account_fingerprint = None
        # Portfolio open-risk headroom: how much more the portfolio can safely risk right now,
        # not just the static per-trade caps -- PART 4 step 8's "compare against portfolio
        # available risk". None (not 0) when no snapshot exists yet, so calculate_risk_size's own
        # min()-of-caps behaves exactly as before this parameter existed rather than blocking
        # every entry before the very first portfolio snapshot has run.
        portfolio_available_risk_usd = None
        latest_snapshot = portfolio_manager.latest_snapshot()
        if latest_snapshot is not None:
            open_risk = float(latest_snapshot.get("open_risk") or 0)
            portfolio_available_risk_usd = Decimal(str(max(0.0, self.config.max_total_open_risk_usd - open_risk)))
        risk = await self.execution.calculate_risk_size(
            account_equity=account.equity, symbol=symbol, direction=candidate["direction"], entry=entry,
            stop=Decimal(str(candidate["stop_loss"])), target=Decimal(str(candidate["take_profit"])),
            risk_budget_adjustment=risk_adjustment_detail, portfolio_available_risk_usd=portfolio_available_risk_usd,
            account_fingerprint=account_fingerprint, account_currency=account.currency,
        )
        if risk.status != "APPROVED":
            return {"status": "RISK_REJECTED", "risk": risk.model_dump(mode="json"), "risk_budget_adjustment": risk_adjustment_detail, "order_send_calls": 0}
        economic_context = candidate.get("economic_context") or {}
        # NOTE (Part: economic-risk double-sizing fix): economic risk already reduced this
        # position's size exactly once, upstream, via _risk_budget_adjustment's
        # economic_risk_factor -> effective_risk_budget_usd -> the smaller effective_risk this
        # calculate_risk_size call was sized against. The economic guard's own `size_multiplier`
        # (economic_context["guard"]["size_multiplier"]) is a SECOND, independently-derived
        # representation of the EXACT SAME decision (every sub-guard sets it to 0.5 iff
        # decision=="REDUCE_SIZE", 1.0 otherwise -- it carries no information economic_risk_factor
        # doesn't already have) and was previously applied AGAIN here on top of the already-
        # adjusted volume, silently halving size a second time for the same condition. Removed --
        # one economic-risk penalty, one clear responsibility, applied exactly once at the budget
        # stage (see _risk_budget_adjustment/risk_budget.compute_risk_multiplier).
        now = utcnow()
        intent_id = f"MT5AUTO_{now.strftime('%Y%m%d%H%M%S')}_{candidate['canonical_pair']}"
        intent = MT5TradeIntent(
            intent_id=intent_id,
            account_id=str(account.login),
            broker_symbol=candidate["broker_symbol"],
            canonical_pair=candidate["canonical_pair"],
            direction=candidate["direction"],
            volume=risk.volume,
            entry_price=entry,
            stop_loss=Decimal(str(candidate["stop_loss"])),
            take_profit=Decimal(str(candidate["take_profit"])),
            magic=self.config.bensim_magic,
            comment=_mt5_order_comment(candidate, now),
            context_hash=candidate["context_hash"],
        )
        if dry_run:
            stop_distance = abs(float(entry) - float(candidate["stop_loss"]))
            atr = float(candidate["atr"]) if candidate.get("atr") else None
            spread = float(candidate["spread"]) if candidate.get("spread") else None
            stops_level = float(getattr(symbol, "trade_stops_level", None) or 0)
            point = float(getattr(symbol, "point", None) or 0)
            broker_min_stop_distance = (stops_level * point) if stops_level and point else None
            stop_quality_v2 = classify_stop_quality_v2(
                sl_distance=stop_distance,
                atr=atr,
                spread=spread,
                structure_distance=None,
                broker_min_stop_distance=broker_min_stop_distance,
                effective_risk_budget_usd=float(risk_adjustment_detail.get("adjusted_risk_budget_usd") or 0),
                projected_monetary_loss_usd=float(risk.projected_loss_usd),
                min_atr_mult=_env_float("ATR_STOP_MULTIPLIER_MIN", 0.8),
            )
            projected_margin = await self.execution.order_calc_margin(intent)
            return {
                "status": "DRY_RUN_OK",
                "intent": intent.model_dump(mode="json"),
                "risk": risk.model_dump(mode="json"),
                "risk_budget_adjustment": risk_adjustment_detail,
                "projected_margin": str(projected_margin) if projected_margin is not None else None,
                "stop_quality_v2": stop_quality_v2,
                "order_send_calls": 0,
            }
        snapshot_id = economic_context.get("snapshot_id")
        if snapshot_id:
            try:
                await economic_intelligence_service.link_execution(snapshot_id, f"mt5-entry:{intent.intent_id}:{intent.context_hash}")
            except Exception as exc:
                logger.warning("Economic intelligence outcome-link failed (non-fatal): %s", exc.__class__.__name__)
        projected_margin = await self.execution.order_calc_margin(intent)
        send_count_before = self.execution.order_send_calls
        result = await self.execution.submit_market_order(intent, economic_context=economic_context)
        order_send_calls = max(0, self.execution.order_send_calls - send_count_before)
        trade = {
            "trade_id": intent_id,
            "intent": intent.model_dump(mode="json"),
            "risk": risk.model_dump(mode="json"),
            "risk_budget_adjustment": risk_adjustment_detail,
            "projected_margin": str(projected_margin) if projected_margin is not None else None,
            "submission": result.model_dump(mode="json"),
            "order_send_calls": order_send_calls,
            "open_timestamp": utcnow().isoformat() if result.status == "ACCEPTED" else None,
        }
        self.state.trades.insert(0, trade | {"created_at": utcnow().isoformat()})
        if result.status == "ACCEPTED":
            store = _stored()
            store["entries_submitted_today"] = int(store.get("entries_submitted_today") or 0) + 1
            set_state("mt5_autonomous", store)
        return trade | {"status": result.status}

    def _economic_blockers(self, economic_result: dict[str, Any]) -> list[str]:
        guard = economic_result.get("guard") or {}
        decision = str(guard.get("decision") or "ALLOW").upper()
        if decision in {"BLOCK", "DELAY"}:
            return sorted(set(f"ECONOMIC_{decision}:{reason}" for reason in (guard.get("reason_codes") or [decision])))
        return []

    def _context_blockers(self, context_risk: dict[str, Any]) -> list[str]:
        blockers = list(context_risk.get("block_reasons") or [])
        if not self.config.block_on_calendar_unavailable:
            blockers = [reason for reason in blockers if reason not in {"CONTEXT_CALENDAR_UNAVAILABLE", "CONTEXT_CALENDAR_STALE", "CONTEXT_CALENDAR_AUTHENTICATION_FAILED"}]
        if self.config.block_on_context_warnings and context_risk.get("acknowledgement_required"):
            blockers.append("CONTEXT_ACK_REQUIRED")
        return sorted(set(blockers))

    def _record_cycle(self, result: dict[str, Any], *, mark_processed: bool = True, dry_run: bool = False) -> None:
        if dry_run:
            # A dry-run cycle must be side-effect free: no cycle-history/status mutation, no
            # processed-candle bookkeeping, no persistence -- so it can never suppress or
            # interfere with a real scheduled cycle for the same candle.
            return
        self.state.last_result = result
        self.state.cycles.insert(0, result | {"created_at": utcnow().isoformat()})
        store = _stored()
        if mark_processed and result.get("cycle_id"):
            processed = list(store.get("processed_candles") or [])
            if result["cycle_id"] not in processed:
                processed.append(result["cycle_id"])
            store["processed_candles"] = processed[-200:]
        store["cycles"] = self.state.cycles[:100]
        store["decisions"] = self.state.decisions[:100]
        store["trades"] = self.state.trades[:100]
        store["last_result"] = result
        set_state("mt5_autonomous", store)
        try:
            persist_cycle_result(result)
        except Exception as exc:
            logger.warning("MT5 cycle persistence failed: %s", exc.__class__.__name__)
        # Confidence Validation & Calibration layer (Part 1) -- observation-only, never
        # affects the decision already made above. Failure here must never surface as a
        # trading-cycle error.
        try:
            capture_cycle_candidate_evaluations(result)
        except Exception as exc:
            logger.warning("MT5 candidate evaluation capture failed: %s", exc.__class__.__name__)

    def status(self) -> dict[str, Any]:
        store = _stored()
        return {
            "scheduler_running": bool(self._task and not self._task.done()),
            "scheduler_owner": self.state.scheduler_owner,
            "scheduler_started_at": self.state.scheduler_started_at.isoformat() if self.state.scheduler_started_at else None,
            "last_cycle_time": self.state.last_cycle_time.isoformat() if self.state.last_cycle_time else None,
            "next_cycle_time": self.state.next_cycle_time.isoformat() if self.state.next_cycle_time else None,
            "current_cycle_id": self.state.current_cycle_id,
            "current_state": self.state.current_state,
            "last_result": store.get("last_result"),
            # AI/provider usage is not part of the deterministic autonomous cycle -- kept as
            # 0 (not removed) so existing dashboard/API consumers reading these keys don't break.
            "provider_calls_today": 0,
            "estimated_cost_today": 0.0,
            "min_trade_confidence": self.config.min_trade_confidence,
            "confidence_top_k_candidates": self.config.confidence_top_k_candidates,
            "emergency_disable": bool(store.get("emergency_disabled")),
            "autonomous_submission_enabled": self.config.autonomous_submission_enabled,
            "entries_submitted_today": int(store.get("entries_submitted_today") or 0),
            "daily_trade_cap": self.config.max_trades_per_day,
            "rollout_complete": True,
            "rollout_max_lots": None,
            "live_trading_enabled": self.config.live_trading_enabled,
        }

    def cycles(self) -> list[dict[str, Any]]:
        return list(_stored().get("cycles") or [])

    def decisions(self) -> list[dict[str, Any]]:
        return list(_stored().get("decisions") or [])

    def trades(self) -> list[dict[str, Any]]:
        return list(_stored().get("trades") or [])

    def candidates(self) -> list[dict[str, Any]]:
        cycles = self.cycles()
        if not cycles:
            return []
        latest = cycles[0]
        if latest.get("candidates"):
            return latest.get("candidates") or []
        if latest.get("winner"):
            return [latest["winner"]]
        return []

    async def risk_status(self) -> dict[str, Any]:
        account = await self.adapter.mt5_account()
        return risk_status(self.config, equity=account.equity, balance=account.balance)

    async def performance(self) -> dict[str, Any]:
        return trade_performance_summary()

    async def reconciliation(self) -> dict[str, Any]:
        positions = await self.adapter.mt5_positions()
        orders = await self.adapter.mt5_orders()
        owned_positions = [p.model_dump(mode="json") for p in positions if p.magic == self.config.bensim_magic or str(p.comment or "").startswith(("BENSIM_AUTO", "BSM|"))]
        owned_orders = [o.model_dump(mode="json") for o in orders if o.magic == self.config.bensim_magic or str(o.comment or "").startswith(("BENSIM_AUTO", "BSM|"))]
        status = "MATCHED_EMPTY" if not owned_positions and not owned_orders else "MATCHED_OPEN"
        if any(p.get("sl") in {None, "0"} or p.get("tp") in {None, "0"} for p in owned_positions):
            status = "PROTECTION_MISMATCH"
        result = {"status": status, "positions": owned_positions, "orders": owned_orders, "created_at": utcnow().isoformat()}
        try:
            update_trade_reconciliation(result)
            deals = await self.adapter.history_deals(days=14)
            result["history_sync"] = update_trade_history([deal.model_dump(mode="json") for deal in deals])
        except Exception as exc:
            logger.warning("MT5 reconciliation persistence failed: %s", exc.__class__.__name__)
            result["history_sync"] = {"status": "UNAVAILABLE", "error": exc.__class__.__name__}
        return result

    async def emergency_disable(self) -> dict[str, Any]:
        store = _stored()
        store["emergency_disabled"] = True
        set_state("mt5_autonomous", store)
        return {"emergency_disabled": True}

    async def emergency_enable(self) -> dict[str, Any]:
        blockers = await self._global_blockers()
        blockers = [b for b in blockers if b != "EMERGENCY_DISABLED"]
        if blockers:
            return {"emergency_disabled": True, "enabled": False, "blockers": blockers}
        store = _stored()
        store["emergency_disabled"] = False
        set_state("mt5_autonomous", store)
        return {"emergency_disabled": False, "enabled": True}

    def _load_state(self) -> None:
        store = _stored()
        self.state.emergency_disabled = bool(store.get("emergency_disabled"))
        self.state.cycles = list(store.get("cycles") or [])
        self.state.decisions = list(store.get("decisions") or [])
        self.state.trades = list(store.get("trades") or [])
        self.state.last_result = store.get("last_result")

    def _persist_state(self) -> None:
        store = _stored()
        store["cycles"] = self.state.cycles[:100]
        store["decisions"] = self.state.decisions[:100]
        store["trades"] = self.state.trades[:100]
        store["last_result"] = self.state.last_result
        set_state("mt5_autonomous", store)

    def _acquire_lock(self, owner: str) -> bool:
        state = get_state("mt5_autonomous_scheduler_lock")
        heartbeat = _parse_dt(state.get("heartbeat_at"))
        if state.get("owner") and heartbeat and utcnow() - heartbeat < timedelta(minutes=20):
            self.state.scheduler_owner = state.get("owner")
            return state.get("owner") == owner
        set_state("mt5_autonomous_scheduler_lock", {"owner": owner, "heartbeat_at": utcnow().isoformat(), "started_at": utcnow().isoformat()})
        return True

    def _release_lock(self) -> None:
        state = get_state("mt5_autonomous_scheduler_lock")
        if state.get("owner") == self.state.scheduler_owner:
            set_state("mt5_autonomous_scheduler_lock", {})

    def _heartbeat(self, owner: str) -> None:
        set_state("mt5_autonomous_scheduler_lock", {"owner": owner, "heartbeat_at": utcnow().isoformat(), "started_at": self.state.scheduler_started_at.isoformat() if self.state.scheduler_started_at else utcnow().isoformat()})

    def _assert_owner(self, owner: str) -> None:
        if self.state.scheduler_owner and owner not in {self.state.scheduler_owner, "local", "api"}:
            raise RuntimeError("MT5 autonomous scheduler owner mismatch")


def _build_multi_strategy_analysis(
    instrument: MT5ForexInstrument, cycle_id: str,
    m15_rows: list[dict[str, Any]], h1_rows: list[dict[str, Any]], h4_rows: list[dict[str, Any]],
    bid: Any, ask: Any, spread: Any, regime_info: dict[str, Any] | None,
) -> tuple[str, dict[str, Any], list[dict[str, Any]], Any, int]:
    """Builds the shared strategy context once, evaluates every regime-compatible canonical
    strategy family, fuses same-direction agreement and resolves opposite-direction conflicts
    (backend/mt5_strategies/fusion.py). Pure, in-memory, no broker/adapter calls and no shared
    mutable state -- this is the function Part 5's bounded thread pool runs concurrently, one
    call per instrument, each independent. Returns data rather than mutating a shared list
    (mutating a shared list from multiple threads would be a race condition).

    Returns (regime, smc_evidence, candidates, ctx, signal_count) -- regime/smc_evidence are
    also attached to MTFAI1's own row by the caller (Part 18: every candidate, including the
    existing strategy, carries regime/strategy identity and standardized SMC evidence); ctx is
    cached by the caller so _entry_quality_score never re-runs analyze_bars for this symbol
    within the same cycle (Part 2)."""
    ctx = build_strategy_context(
        symbol=instrument.canonical_pair, broker_symbol=instrument.broker_symbol,
        m15_rows=m15_rows, h1_rows=h1_rows, h4_rows=h4_rows,
        bid=Decimal(str(bid)), ask=Decimal(str(ask)), spread=Decimal(str(spread or 0)),
        regime_info=regime_info, symbol_info=instrument.symbol,
    )
    if ctx is None:
        return "insufficient_data", {}, [], None, 0
    smc_evidence = summarize_smc_evidence(ctx)
    signals = evaluate_all(ctx)
    candidates = build_candidates(
        symbol=instrument.canonical_pair, broker_symbol=instrument.broker_symbol, asset_class=instrument.asset_class,
        cycle_id=cycle_id, signals=signals, htf_trend_h4=ctx.htf_trend_h4, now=ctx.generated_at,
    )
    return ctx.regime, smc_evidence, candidates, ctx, len(signals)


def _score_candidate(quote: Any, m15: list[Any], h1: list[Any], h4: list[Any], symbol_info: Any = None) -> tuple[float, str, dict[str, str]]:
    closes = [Decimal(str(c.close)) for c in m15[-50:]]
    h1_close = Decimal(str(h1[-1].close))
    h1_avg = sum(Decimal(str(c.close)) for c in h1[-20:]) / Decimal("20")
    h4_close = Decimal(str(h4[-1].close))
    h4_avg = sum(Decimal(str(c.close)) for c in h4[-20:]) / Decimal("20")
    fast = sum(closes[-10:]) / Decimal("10")
    slow = sum(closes[-30:]) / Decimal("30")
    direction = "LONG" if fast > slow and h1_close > h1_avg and h4_close > h4_avg else "SHORT" if fast < slow and h1_close < h1_avg and h4_close < h4_avg else "NO_TRADE"
    entry = Decimal(str(quote.ask if direction == "LONG" else quote.bid or closes[-1]))
    atr = sum(abs(Decimal(str(c.high)) - Decimal(str(c.low))) for c in m15[-14:]) / Decimal("14")
    if atr <= 0 or direction == "NO_TRADE":
        return 0, "NO_TRADE", {"entry": str(entry), "stop_loss": str(entry), "take_profit": str(entry)}
    spread = Decimal(str(quote.spread or "0"))
    structure_level = _swing_level(m15, direction)
    broker_min_stop_distance = None
    stops_level = getattr(symbol_info, "trade_stops_level", None)
    point = getattr(symbol_info, "point", None)
    if stops_level and point:
        try:
            broker_min_stop_distance = float(Decimal(str(stops_level)) * Decimal(str(point)))
        except Exception:
            broker_min_stop_distance = None
    stop_price = construct_dynamic_stop(
        direction,
        float(entry),
        float(structure_level) if structure_level is not None else None,
        float(atr),
        float(spread),
        min_atr_mult=_env_float("MT5_AUTO_SL_MIN_ATR_MULT", 1.0),
        max_atr_mult=_env_float("MT5_AUTO_SL_MAX_ATR_MULT", 3.0),
        min_spread_ratio=_env_float("MT5_AUTO_SL_MIN_SPREAD_RATIO", 3.0),
        broker_min_stop_distance=broker_min_stop_distance,
    )
    if stop_price is None:
        return 0, "NO_TRADE", {"entry": str(entry), "stop_loss": str(entry), "take_profit": str(entry)}
    stop = Decimal(str(stop_price))
    # Opposing structure level: the swing level on the OPPOSITE side of the trade direction is
    # the nearest meaningful liquidity/resistance-or-support a move would need to clear -- a
    # real target, not a fixed multiple of the stop distance unrelated to actual price structure.
    opposing_structure = _swing_level(m15, "SHORT" if direction == "LONG" else "LONG")
    tp_selection = select_take_profit(direction=direction, entry=entry, stop_loss=stop, opposing_structure_level=opposing_structure, atr=atr)
    target = tp_selection["tp1"]
    spread_penalty = min(30, float(spread / atr * Decimal("100"))) if atr > 0 else 30
    score = 88 - spread_penalty
    return round(score, 2), direction, {
        "entry": str(entry),
        "stop_loss": str(stop),
        "take_profit": str(target),
        "take_profit_tp2": str(tp_selection["tp2"]),
        "take_profit_runner": str(tp_selection["runner"]),
        "take_profit_basis": tp_selection["basis"],
        "risk_reward": str(tp_selection["reward_multiple"]) if tp_selection["reward_multiple"] is not None else "1.8",
        "atr": str(atr),
        "spread": str(spread),
    }


def _swing_level(candles: list[Any], direction: str, lookback: int = 20) -> Decimal | None:
    window = candles[-lookback:]
    if len(window) < 5:
        return None
    if direction == "LONG":
        return min(Decimal(str(c.low)) for c in window)
    return max(Decimal(str(c.high)) for c in window)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _candidate(instrument: MT5ForexInstrument, reasons: list[str]) -> dict[str, Any]:
    return {"canonical_pair": instrument.canonical_pair, "broker_symbol": instrument.broker_symbol, "asset_class": instrument.asset_class, "ranking_score": 0, "direction": "NO_TRADE", "rejection_reasons": sorted(set(reasons)), "context_hash": ""}


def _correlation_penalty(candidate: dict[str, Any], exposure: dict[str, Any]) -> tuple[float, list[str]]:
    """Bounded, deterministic correlation/crowding penalty (points, 0-60) for the
    confidence engine's correlation_quality component -- reuses
    portfolio_manager.exposure()'s currency net exposure (in lots) rather than
    recomputing anything. This reflects SIGNAL diversification quality only; it is
    independent of, and does not substitute for, the separate hard portfolio-limits
    gate (portfolio_manager.can_open_new_trade()) applied after a candidate is
    selected."""
    pair = str(candidate.get("canonical_pair") or "").upper()
    if len(pair) < 6:
        return 0.0, []
    base, quote = pair[:3], pair[3:6]
    direction_sign = 1.0 if candidate.get("direction") == "LONG" else -1.0
    currency_exposure = exposure.get("currency") or {}
    penalty = 0.0
    correlated: list[str] = []
    base_net = float((currency_exposure.get(base) or {}).get("net") or 0.0)
    if base_net * direction_sign > 0:
        penalty += min(40.0, abs(base_net) * 15.0)
        correlated.append(base)
    quote_net = float((currency_exposure.get(quote) or {}).get("net") or 0.0)
    if quote_net * direction_sign < 0:
        penalty += min(40.0, abs(quote_net) * 15.0)
        correlated.append(quote)
    return min(60.0, penalty), correlated


# Deterministic, compact strategy attribution for the MT5 order comment. Real STRATEGY_FAMILIES
# ids are mostly longer than the comment's tight length budget allows once a fused candidate has
# more than one contributor -- these are only used as the SECOND-tier fallback (see
# _candidate_strategy_label), after the actual full strategy_id(s) have already been tried.
_STRATEGY_SHORT_CODES: dict[str, str] = {
    "mtfai1": "mtfai1",
    "ema_trend": "ema",
    "trend_pullback": "pullback",
    "breakout": "breakout",
    "mean_reversion": "meanrev",
    "liquidity_sweep_reversal": "liqsweep",
    "smc_continuation": "smc_cont",
    "support_resistance_bounce": "srbounce",
    "momentum": "momentum",
    "session_breakout": "sessbrk",
    "vwap_reversion": "vwaprev",
}
_COMMENT_TOTAL_MAX = 31
_COMMENT_PREFIX = "BSM|"


def _strategy_short_code(strategy_id: str) -> str:
    return _STRATEGY_SHORT_CODES.get(strategy_id, strategy_id[:8]) or "strategy"


def _candidate_strategy_label(candidate: dict[str, Any], *, max_len: int) -> str:
    """Deterministic strategy attribution for the MT5 order comment, read directly from the
    already-selected candidate's own context (strategy_id/contributing_strategies) -- never
    re-derived from market conditions. The anchor strategy is always first and is never dropped
    or abbreviated away entirely; only the CONTRIBUTING strategies beyond the anchor degrade
    (full names -> short codes -> a bare count) as the broker comment's length budget requires,
    so which strategy was primary is never ambiguous even when the label must be shortened."""
    context = candidate.get("context") or {}
    anchor = str(context.get("strategy_id") or "mtfai1")
    contributing = context.get("contributing_strategies") or []
    others = sorted({str(sid) for sid in contributing if sid and str(sid) != anchor})

    full = anchor if not others else f"{anchor}+{'+'.join(others)}"
    if len(full) <= max_len:
        return full

    anchor_code = _strategy_short_code(anchor)
    other_codes = [_strategy_short_code(o) for o in others]
    compact = anchor_code if not others else f"{anchor_code}+{'+'.join(other_codes)}"
    if len(compact) <= max_len:
        return compact

    counted = anchor_code if not others else f"{anchor_code}+{len(others)}"
    if len(counted) <= max_len:
        return counted

    return anchor_code[:max_len] or "strategy"


def _mt5_order_comment(candidate: dict[str, Any], timestamp: datetime) -> str:
    """The "BSM|" prefix is load-bearing -- position/order ownership detection elsewhere
    (_global_blockers, reconciliation()) matches on comment.startswith(("BENSIM_AUTO", "BSM|")),
    never on anything after it, so everything past the prefix is free to carry the actual
    strategy attribution instead of the previous hardcoded "MTFAI1" label. Symbol and timeframe
    are intentionally no longer duplicated here -- MT5's own terminal/API already reports symbol
    per position/order, and timeframe was always "M15" for every trade this bot places -- freeing
    that space for the strategy label, which is the part that actually varies and was wrong.
    `magic` (config.bensim_magic), not this comment, remains the authoritative reconciliation/
    ownership key, so shortening/reformatting this string cannot break reconciliation."""
    suffix = f"|{timestamp.strftime('%H%M')}"
    budget = max(1, _COMMENT_TOTAL_MAX - len(_COMMENT_PREFIX) - len(suffix))
    label = _candidate_strategy_label(candidate, max_len=budget)
    return f"{_COMMENT_PREFIX}{label}{suffix}"[:_COMMENT_TOTAL_MAX]


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _stored() -> dict[str, Any]:
    return get_state("mt5_autonomous")


def _completed_m5_time() -> datetime:
    now = utcnow().replace(second=0, microsecond=0)
    minute = (now.minute // 5) * 5
    current = now.replace(minute=minute)
    return current - timedelta(minutes=5)


def _next_m5_run() -> datetime:
    return _completed_m5_time() + timedelta(minutes=10, seconds=10)


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


mt5_autonomous_service = MT5AutonomousTradingService()
