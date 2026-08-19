from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from backend.adaptive_management.tp_protection import classify_stop_quality_v2, construct_dynamic_stop
from backend.market_structure.bar_utils import normalize_bars
from backend.market_structure.configuration import MarketStructureConfig
from backend.market_structure.engine import analyze_bars
from backend.market_structure.liquidity import detect_equal_levels
from backend.market_structure.models import LiquiditySide, TrendLabel
from backend.market_structure.swings import detect_swings
from backend.market_structure.trend import classify_trend
from backend.brokers.mt5 import account_registry
from backend.brokers.mt5.adapter import MT5Adapter, mt5_adapter
from backend.brokers.mt5.config import MT5Config
from backend.brokers.mt5.config import mt5_config
from backend.brokers.mt5.multi_account import adapter_for_account
from backend.brokers.mt5.reconciliation_watchdog import is_account_state_trustworthy, reconcile_account
from backend.brokers.mt5.candidate_evaluation import capture_cycle_candidate_evaluations
from backend.brokers.mt5.confidence import compute_trade_confidence, is_autonomous_eligible, rank_candidates
from backend.mt5_strategies.context import build_strategy_context, cheap_prefilter, quick_regime, summarize_smc_evidence
from backend.mt5_strategies.families import evaluate_all
from backend.mt5_strategies.fusion import build_candidates
from backend.mt5_strategies.models import ACTIVE_MT5, activation_status, all_strategy_ids, multi_strategy_enabled, normalize_strategy_id, regime_compatible
from backend.mt5_strategies import redis_layer
from backend.brokers.mt5.execution import MT5ExecutionService
from backend.brokers.mt5.market_data import candle_quality
from backend.brokers.mt5.models import MT5ForexInstrument, MT5TradeIntent
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.brokers.mt5.persistence import confidence_memory_for_symbol, persist_cycle_result, trade_performance_summary, update_trade_history, update_trade_reconciliation
from backend.brokers.mt5.prop_risk import risk_status
from backend.brokers.mt5.prop_state import evaluate_entry_protection, remaining_safety_budget_usd
from backend.brokers.mt5.risk_budget import effective_risk_budget_usd
from backend.brokers.mt5.take_profit import MIN_REWARD_MULTIPLE, select_take_profit
from backend.decision_context.service import decision_context_service
from backend.economic_intelligence import macro_context
from backend.economic_intelligence.service import economic_intelligence_service
from backend.intelligence.trading.persistence import get_state, set_state, utcnow
from backend.portfolio_execution.service import portfolio_manager
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

# Part 5: bounded concurrency for the pure in-memory (no MT5/adapter calls) multi-strategy
# analysis phase only. Candle/quote fetching always stays fully serial -- see _screen()'s
# comment for why concurrent calls into the MetaTrader5 module are not safe to introduce.
_MULTI_STRATEGY_CONCURRENCY = int(os.getenv("MT5_MULTI_STRATEGY_CONCURRENCY", "6"))
_NO_OPENAI_CALLS = 0

# DEMO-only rolling execution diversity cap: MTFAI1 may account for at most
# MT5_DEMO_MTF_AI1_MAX_TRADES of the last MT5_DEMO_MTF_AI1_WINDOW executed (ACCEPTED) entries
# from this engine. Never applied outside DEMO (see run_cycle's account_mode check) and never
# alters MTFAI1's own confidence/ranking -- it only decides whether MTFAI1's naturally-ranked-
# #1 candidate is allowed to actually execute this cycle, or is deferred in favor of the next-
# ranked ACTIVE_MT5 non-MTFAI1 candidate that still clears the same confidence threshold.
MT5_DEMO_MTF_AI1_MAX_TRADES = int(os.getenv("MT5_DEMO_MTF_AI1_MAX_TRADES", "3"))
MT5_DEMO_MTF_AI1_WINDOW = int(os.getenv("MT5_DEMO_MTF_AI1_WINDOW", "10"))
MTFAI1_STRATEGY_ID = "mtfai1"

# DEMO-only MTFAI1 entry-quality experiment: a standalone MTFAI1 candidate (no other signal
# family agreeing on the same symbol+direction this cycle) may not execute unless at least one
# of these richer, structure/momentum-aware families also produced a valid signal on the same
# symbol+direction this cycle -- independent evidence the move isn't purely MTFAI1's own
# trend-following read. smc_continuation's own evidence requires a valid BOS/MSS + displacement
# (see backend/mt5_strategies/families.py); liquidity_sweep_reversal's requires a confirmed
# liquidity sweep + displacement + structure shift -- so listing those two family ids already
# captures the "BOS/MSS+displacement" / "liquidity-sweep/structure" confirmation sources.
# MTFAI1's own confidence/ranking are never touched by this -- it only decides whether a
# standalone MTFAI1 candidate is allowed to become `best`, exactly like the diversity cap above.
MTFAI1_CONFIRMING_STRATEGY_IDS = {"momentum", "smc_continuation", "breakout", "trend_pullback", "liquidity_sweep_reversal"}

# 2026-08-18: user-requested kill switch for the confirmation gate itself, distinct from
# MT5_STRATEGY_ACTIVATION_MTFAI1 (which controls whether mtfai1 can execute at all). Defaults to
# "true" (today's unchanged behavior: standalone mtfai1 still needs independent confirmation).
# Set to a falsy value to let mtfai1 execute alone again, same as before this gate existed.
MT5_MTFAI1_CONFIRMATION_REQUIRED = os.getenv("MT5_MTFAI1_CONFIRMATION_REQUIRED", "true").strip().lower() not in {"false", "0", "off", "no"}


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

    @property
    def account_id(self) -> str:
        return getattr(self.config, "account_id", "demo_10k") or "demo_10k"

    def _state_key(self) -> str:
        return "mt5_autonomous" if self.account_id == "demo_10k" else f"mt5_autonomous:{self.account_id}"

    def _lock_key(self) -> str:
        return "mt5_autonomous_scheduler_lock" if self.account_id == "demo_10k" else f"mt5_autonomous_scheduler_lock:{self.account_id}"

    def _stored(self) -> dict[str, Any]:
        return get_state(self._state_key())

    def _set_stored(self, store: dict[str, Any]) -> None:
        set_state(self._state_key(), store)

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
        # Part 11: cache each symbol's validated risk-metadata result (calc mode, tick size,
        # tick-value health, contract size, currency conversion, quorum, OK/DEGRADED/CRITICAL)
        # for fast dashboard/API reads. A symbol just found unhealthy gets its stale cached
        # metadata (and any previously-cached "healthy" risk-metadata entry) invalidated
        # immediately (Part 11: "do not allow stale healthy metadata to override a current
        # critical mismatch") and an mt5.risk.metadata_unhealthy event published -- this NEVER
        # affects self._risk_metadata_health above, which is the always-authoritative in-memory
        # source _screen() actually gates execution eligibility on.
        for row in results:
            symbol = row.get("symbol")
            if not symbol:
                continue
            if row.get("status") in {RISK_METADATA_OK, RISK_METADATA_DEGRADED_TWO_METHOD}:
                await redis_layer.set_risk_metadata_status("mt5", symbol, row)
            else:
                await redis_layer.invalidate_risk_metadata("mt5", symbol)
                await redis_layer.invalidate_symbol_metadata("mt5", symbol)
                await redis_layer.publish_event("mt5.risk.metadata_unhealthy", {"symbol": symbol, "status": row.get("status")})
        return {"status": "ok", "refreshed_at": self._risk_metadata_health_refreshed_at.isoformat(), "symbols_audited": len(results), "unhealthy": unhealthy, "results": results}

    async def _prewarm_market_data_cache(self) -> None:
        """Startup-only, best-effort cache prewarm (Redis integration follow-up: cold-cycle
        latency). Profiling showed the first cycle after a cold start/redeploy spent ~46s of its
        ~72s total in market_data_retrieval alone (40 sequential MT5 calls -- 10 symbols x
        tick/M15/H1/H4 -- all cache misses); the very next cycle, once Redis was warm, dropped to
        ~0.8s for the same step. This closes that gap by running the SAME cache-populating fetch
        functions _screen() already uses (redis_layer.cached_symbol_info/cached_candles) once,
        here, during the idle window between scheduler start and the first scheduled cycle
        (typically 2-5 minutes -- see _next_m5_run()), so the first REAL cycle finds Redis
        already warm instead of paying the cold cost live.

        Deliberately excludes ticks: their TTL is only 1-5s (MT5_REDIS_TICK_TTL_SECONDS), so a
        tick prewarmed minutes before the first cycle would already be expired by the time it
        ran -- prewarming it would cost a real MT5 call for zero benefit. M15/H1/H4 candles and
        symbol metadata have TTLs measured in minutes to hours, so they are still valid when the
        first cycle actually runs.

        Holds self._cycle_lock for its entire duration -- the SAME mutex run_cycle() already
        uses -- so its MT5 calls can never run concurrently with a live cycle's; this is the one
        thing this integration must never do (the MetaTrader5 terminal API is not safe for
        concurrent calls from multiple threads/tasks, see _screen()'s own comment). If a cycle
        is already running when this fires, this simply waits its turn like anything else that
        needs the lock. Never introduces any concurrency into MT5 access itself -- the fetch
        loop below is exactly as serial as _screen()'s always was.

        Fire-and-forget from start() (never awaited there): a slow or failing prewarm can never
        delay scheduler start or block trading. Every per-symbol failure is caught and skipped
        rather than aborting the whole prewarm."""
        try:
            async with self._cycle_lock:
                universe = await self.adapter.forex_universe()
                symbols = [i.broker_symbol for i in universe.items if i.eligible]
                warmed = 0
                for broker_symbol in symbols:
                    try:
                        await redis_layer.cached_symbol_info(self.adapter, broker_symbol)
                        await redis_layer.cached_candles(self.adapter, broker_symbol, "M15", count=100)
                        await redis_layer.cached_candles(self.adapter, broker_symbol, "H1", count=100)
                        await redis_layer.cached_candles(self.adapter, broker_symbol, "H4", count=100)
                        warmed += 1
                    except Exception as exc:
                        logger.debug("MT5 cache prewarm skipped %s: %s", broker_symbol, exc.__class__.__name__)
                        continue
                logger.warning("MT5 cache prewarm completed: %d/%d symbol(s)", warmed, len(symbols))
        except Exception as exc:
            logger.warning("MT5 cache prewarm failed (non-fatal, first cycle will warm normally): %s", exc.__class__.__name__)

    async def start(self, *, owner: str | None = None) -> bool:
        if hasattr(self.adapter, "reload_config"):
            self.adapter.reload_config()
        elif self.account_id == "demo_10k":
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
        # Redis integration follow-up: prewarm the market-data cache during the idle window
        # before the first scheduled cycle -- see _prewarm_market_data_cache's own docstring.
        # Fire-and-forget, same pattern as the risk-metadata audit above.
        asyncio.create_task(self._prewarm_market_data_cache(), name="mt5-redis-cache-prewarm")
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

    def _mtfai1_rolling_count(self, window: int) -> int:
        """Counts MTFAI1 occurrences among the most recent `window` ACCEPTED (actually
        executed) entries in self.state.trades -- this engine's own submission history, newest
        first (see _submit's `self.state.trades.insert(0, ...)`), already loaded fresh by
        _load_state() at the top of run_cycle(). Only ACCEPTED submissions count as "executed"
        -- a rejected/errored attempt never reached the broker as a real position. Older MTFAI1
        entries roll out of the window automatically as new trades are appended; no extra state
        to age out manually."""
        executed = [t for t in self.state.trades if (t.get("submission") or {}).get("status") == "ACCEPTED"]
        recent = executed[:window]
        return sum(1 for t in recent if t.get("strategy_id") == MTFAI1_STRATEGY_ID)

    def _apply_mtfai1_diversity_cap(self, cycle_id: str, eligible_for_execution: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """DEMO-only MTFAI1 rolling execution diversity cap. `eligible_for_execution` (already
        confidence->=75 and ACTIVE_MT5 filtered, in ranked order) is never mutated in its
        ordering/scores -- MTFAI1's confidence and ranking are never lowered or altered. This
        only decides which already-eligible candidate becomes `best` (the one actually
        submitted this cycle) when MTFAI1 is naturally #1 but has already accounted for
        MT5_DEMO_MTF_AI1_MAX_TRADES of the last MT5_DEMO_MTF_AI1_WINDOW ACCEPTED executions from
        this engine. Never engages outside DEMO -- live mode always keeps the natural #1 as
        `best`, unconditionally. Returns (best_or_None, diversity_cap_metadata); best is None
        only when MTFAI1 was deferred and no qualifying non-MTFAI1 alternative exists (the
        caller must never force a trade in that case)."""
        natural_best = eligible_for_execution[0]
        natural_best_strategy_id = (natural_best.get("context") or {}).get("strategy_id")
        diversity_cap: dict[str, Any] = {
            "enabled": False,
            "window": MT5_DEMO_MTF_AI1_WINDOW,
            "max_trades": MT5_DEMO_MTF_AI1_MAX_TRADES,
            "rolling_mtfai1_count": None,
            "natural_rank1_candidate_id": f"{cycle_id}:{natural_best['broker_symbol']}:{natural_best['context_hash'][:16]}",
            "natural_rank1_strategy_id": natural_best_strategy_id,
            "natural_rank1_confidence": natural_best["trade_confidence"]["overall_score"],
            "deferred": False,
            "defer_reason": None,
            "executed_strategy_id": None,
        }
        best = natural_best
        if self.config.account_mode == "DEMO":
            diversity_cap["enabled"] = True
            rolling_count = self._mtfai1_rolling_count(MT5_DEMO_MTF_AI1_WINDOW)
            diversity_cap["rolling_mtfai1_count"] = rolling_count
            if natural_best_strategy_id == MTFAI1_STRATEGY_ID and rolling_count >= MT5_DEMO_MTF_AI1_MAX_TRADES:
                diversity_cap["deferred"] = True
                diversity_cap["defer_reason"] = "TEST_DIVERSITY_DEFERRED"
                # Next-ranked ACTIVE_MT5, non-MTFAI1 candidate that still clears the same
                # confidence/activation bar eligible_for_execution already enforced above --
                # skips PAST any further MTFAI1 entries too (picking another MTFAI1 trade would
                # not solve the concentration this cap exists to prevent).
                best = next((row for row in eligible_for_execution[1:] if (row.get("context") or {}).get("strategy_id") != MTFAI1_STRATEGY_ID), None)

        for row in eligible_for_execution:
            if row is best:
                continue
            reason = "TEST_DIVERSITY_DEFERRED" if (diversity_cap["deferred"] and row is natural_best) else "LOWER_RANKED_CANDIDATE"
            row["rejection_reasons"] = sorted(set((row.get("rejection_reasons") or []) + [reason]))

        if best is not None:
            diversity_cap["executed_strategy_id"] = (best.get("context") or {}).get("strategy_id")
        return best, diversity_cap

    @staticmethod
    def _confirming_strategy_ids(candidate: dict[str, Any]) -> set[str]:
        """Strategy ids this candidate itself represents/carries (its own anchor strategy_id
        plus, for a fused multi-strategy candidate, every family that agreed on it), intersected
        with MTFAI1_CONFIRMING_STRATEGY_IDS. Used to find an INDEPENDENT confirming signal on a
        different candidate row, never to inspect MTFAI1's own row (which never has a
        contributing family since MTFAI1 is screened outside build_candidates())."""
        context = candidate.get("context") or {}
        ids = {context.get("strategy_id")} | set(context.get("contributing_strategies") or [])
        return {sid for sid in ids if sid in MTFAI1_CONFIRMING_STRATEGY_IDS}

    def _apply_mtfai1_confirmation_gate(self, cycle_id: str, all_candidates: list[dict[str, Any]], eligible_for_execution: list[dict[str, Any]], best: dict[str, Any] | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """DEMO-only MTFAI1 entry-quality experiment (separate from, and runs after, the
        diversity cap above). A standalone MTFAI1 `best` -- no independent confirming signal
        (see MTFAI1_CONFIRMING_STRATEGY_IDS) on the same symbol+direction this cycle -- is not
        allowed to execute; the next-ranked ACTIVE_MT5 non-MTFAI1 candidate that still clears
        the same confidence threshold is considered instead, exactly like the diversity cap's
        own fallback. MTFAI1's confidence/ranking/threshold (75) are never touched -- this only
        decides which already-eligible candidate becomes `best`. A no-op (returns best
        unchanged) whenever `best` is None, not MTFAI1, or account_mode is not DEMO."""
        gate: dict[str, Any] = {
            "enabled": self.config.account_mode == "DEMO",
            "applicable": False,
            "confirmed": None,
            "confirming_strategy_ids": [],
            "deferred": False,
            "defer_reason": None,
            "executed_strategy_id": (best.get("context") or {}).get("strategy_id") if best else None,
        }
        if best is None or self.config.account_mode != "DEMO":
            return best, gate
        best_strategy_id = (best.get("context") or {}).get("strategy_id")
        if best_strategy_id != MTFAI1_STRATEGY_ID:
            return best, gate
        if not MT5_MTFAI1_CONFIRMATION_REQUIRED:
            gate["enabled"] = False
            return best, gate

        gate["applicable"] = True
        confirming: set[str] = set()
        for row in all_candidates:
            if row is best or row.get("canonical_pair") != best.get("canonical_pair") or row.get("direction") != best.get("direction"):
                continue
            confirming |= self._confirming_strategy_ids(row)
        gate["confirming_strategy_ids"] = sorted(confirming)
        gate["confirmed"] = bool(confirming)
        if gate["confirmed"]:
            gate["executed_strategy_id"] = best_strategy_id
            return best, gate

        best["rejection_reasons"] = sorted(set((best.get("rejection_reasons") or []) + ["MTFAI1_CONFIRMATION_REQUIRED"]))
        gate["deferred"] = True
        gate["defer_reason"] = "MTFAI1_CONFIRMATION_REQUIRED"
        alternative = next((row for row in eligible_for_execution if row is not best and (row.get("context") or {}).get("strategy_id") != MTFAI1_STRATEGY_ID), None)
        if alternative is not None:
            # The diversity-cap pass above already tagged every non-winner with a "not chosen"
            # reason before this gate ran; if this alternative becomes the actual winner here,
            # that earlier tag is now stale and must not linger on the executed candidate.
            alternative["rejection_reasons"] = [r for r in (alternative.get("rejection_reasons") or []) if r not in {"LOWER_RANKED_CANDIDATE", "TEST_DIVERSITY_DEFERRED"}]
        gate["executed_strategy_id"] = (alternative.get("context") or {}).get("strategy_id") if alternative else None
        return alternative, gate

    async def run_cycle(self, *, owner: str = "local", dry_run: bool = False, cycle_time: datetime | None = None) -> dict[str, Any]:
        async with self._cycle_lock:
            self._assert_owner(owner)
            self._load_state()
            cycle_time = cycle_time or _completed_m5_time()
            base_cycle_id = f"MT5_M5_{cycle_time.strftime('%Y%m%d%H%M')}"
            cycle_id = base_cycle_id if self.account_id == "demo_10k" else f"{self.account_id}:{base_cycle_id}"
            redis_metrics_before = redis_layer.metrics_snapshot()
            # Captured here (before the distributed cycle lock below, which can return early)
            # rather than further down, so even a SKIPPED_CYCLE_LOCKED result MEASURES its
            # openai_calls delta like every other exit path instead of hardcoding 0 -- matching
            # this file's existing "measured, not assumed" principle for LLM-call telemetry.
            _llm_calls_before = macro_context.classify_call_count()
            if not dry_run:
                # Distributed cycle lock (Part 7): this process's self._cycle_lock already
                # serializes cycles WITHIN one process; this adds the cross-process guard so a
                # second backend instance/process waking for the SAME cycle_id skips it instead
                # of duplicating the work. TTL-bounded (no explicit release) rather than
                # release-on-exit -- cycle_id is unique per 5-minute window and never recurs, so
                # letting the lock self-expire after a generous margin over normal cycle
                # duration is simpler and avoids any release-ordering edge case; a crashed
                # process just means the lock harmlessly expires with it. Fail-open (see
                # try_lock docstring): a Redis outage here degrades to today's single-process
                # behavior, never blocks a cycle from running.
                cycle_lock_acquired = await redis_layer.try_lock(f"mt5:account:{self.account_id}:cycle-lock:{cycle_id}", f"{owner}:{id(self)}", 180, metric="cycle_lock")
                if not cycle_lock_acquired:
                    result = {"account_id": self.account_id, "cycle_id": cycle_id, "status": "SKIPPED_CYCLE_LOCKED", "order_send_calls": 0, "timing_ms": {"total_cycle_ms": 0.0}, "screening_stats": {}, "openai_calls": macro_context.classify_call_count() - _llm_calls_before}
                    logger.warning("MT5 cycle %s skipped: distributed cycle lock held by another process", cycle_id)
                    return result
                self.state.current_cycle_id = cycle_id
                self.state.last_cycle_time = utcnow()
                self.state.current_state = "running"
                await redis_layer.publish_event("mt5.cycle.started", {"cycle_id": cycle_id})
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
            # call graph would show up here rather than being silently invisible. (_llm_calls_
            # before is captured earlier, right after cycle_id, so SKIPPED_CYCLE_LOCKED measures
            # it too rather than hardcoding 0.)

            def _lap(name: str) -> None:
                nonlocal _last_lap
                now = time.perf_counter()
                stage_ms[name] = round((now - _last_lap) * 1000, 1)
                _last_lap = now

            async def _finalize(result: dict[str, Any]) -> dict[str, Any]:
                result["account_id"] = self.account_id
                result["timing_ms"] = {**stage_ms, "total_cycle_ms": round((time.perf_counter() - cycle_start) * 1000, 1)}
                result["screening_stats"] = dict(self._last_screen_counters)
                openai_calls_measured = macro_context.classify_call_count() - _llm_calls_before
                result["openai_calls"] = openai_calls_measured
                if openai_calls_measured > 0:
                    # Loud, explicit invariant violation (Part 6): the MT5 autonomous pipeline
                    # must be genuinely LLM-free end-to-end. This does not retroactively undo a
                    # decision already made this cycle, but it must never pass silently.
                    logger.error("MT5 ARCHITECTURE INVARIANT VIOLATED: autonomous cycle %s made %d OpenAI call(s) -- MT5 execution must be LLM-free end-to-end", cycle_id, openai_calls_measured)
                # Part 16: per-cycle Redis metrics delta -- computed exactly like openai_calls
                # above (before/after snapshot of a cumulative counter), never a running total.
                redis_after = redis_layer.metrics_snapshot()
                result["redis_cache_hits"] = int(redis_after["cache_hits"] - redis_metrics_before["cache_hits"])
                result["redis_cache_misses"] = int(redis_after["cache_misses"] - redis_metrics_before["cache_misses"])
                result["mt5_data_fetches"] = int(redis_after["broker_fetches"] - redis_metrics_before["broker_fetches"])
                result["smc_cache_hits"] = int(redis_after["smc_cache_hits"] - redis_metrics_before["smc_cache_hits"])
                result["smc_cache_misses"] = int(redis_after["smc_cache_misses"] - redis_metrics_before["smc_cache_misses"])
                calls_delta = redis_after["redis_calls_total"] - redis_metrics_before["redis_calls_total"]
                result["redis_latency_ms_avg"] = round((redis_after["redis_latency_ms_total"] - redis_metrics_before["redis_latency_ms_total"]) / calls_delta, 2) if calls_delta else None
                failures_delta = redis_after["redis_failures"] - redis_metrics_before["redis_failures"]
                result["redis_degraded"] = bool(failures_delta > 0 or redis_layer.get_client() is None)
                if not dry_run:
                    await redis_layer.publish_event("mt5.cycle.completed", {"cycle_id": cycle_id, "status": result.get("status"), "order_send_calls": result.get("order_send_calls", 0), "total_cycle_ms": result["timing_ms"]["total_cycle_ms"]})
                return result

            if not dry_run and cycle_id in set(self._stored().get("processed_candles") or []):
                result = await _finalize({"cycle_id": cycle_id, "status": "SKIPPED_DUPLICATE_CANDLE", "order_send_calls": 0})
                self._record_cycle(result, dry_run=dry_run)
                return result
            blockers = await self._global_blockers()
            _lap("global_blockers")
            if blockers:
                result = await _finalize({"cycle_id": cycle_id, "status": "TRADING_DISABLED", "blockers": blockers, "order_send_calls": 0})
                self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                return result
            # Phase 3 (Forex/MT5 roadmap): one real reconciliation pass per cycle, sequential
            # (awaited, never concurrent with any other MT5 terminal call -- still inside
            # self._cycle_lock) so this cycle's own _screen() sees an up-to-date verdict via
            # is_account_state_trustworthy() rather than a stale one from a prior cycle.
            # reconcile_account() never raises (broker-fetch failures are themselves persisted
            # as an untrusted-state finding), but this is belt-and-suspenders: a reconciliation
            # failure must never abort a trading cycle.
            try:
                await reconcile_account(self.account_id)
            except Exception as exc:
                logger.warning("MT5 reconciliation pass failed for account_id=%s (non-fatal): %s", self.account_id, exc.__class__.__name__)
            _lap("reconciliation")
            universe = await self.adapter.forex_universe()
            candidates = await self._screen(universe.items, cycle_id=cycle_id)
            _lap("screening")
            eligible = [row for row in candidates if not row["rejection_reasons"]]
            if not eligible:
                result = await _finalize({"cycle_id": cycle_id, "status": "NO_TRADE", "symbols_discovered": universe.total_forex_pairs, "eligible_symbols": len([i for i in universe.items if i.eligible]), "candidates": candidates[:25], "order_send_calls": 0})
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
                result = await _finalize({"cycle_id": cycle_id, "status": "NO_TRADE", "winner": ranked[0] if ranked else None, "candidates": ranked, "order_send_calls": 0})
                self._record_cycle(result, dry_run=dry_run)
                return result

            best, diversity_cap = self._apply_mtfai1_diversity_cap(cycle_id, eligible_for_execution)
            if best is None:
                # Never force a trade: MTFAI1 was deferred and no qualifying alternative exists
                # this cycle. eligible_for_execution[0] (still rank #1 in `ranked`/analytics,
                # unchanged) is reported as the winner for visibility, but nothing is submitted.
                result = await _finalize({"cycle_id": cycle_id, "status": "NO_TRADE", "winner": eligible_for_execution[0], "candidates": ranked, "diversity_cap": diversity_cap, "order_send_calls": 0})
                self._record_cycle(result, dry_run=dry_run)
                return result

            best, confirmation_gate = self._apply_mtfai1_confirmation_gate(cycle_id, candidates, eligible_for_execution, best)
            if best is None:
                # Same never-force-a-trade guarantee as the diversity cap above: a standalone
                # MTFAI1 candidate lacked independent confirmation and no qualifying non-MTFAI1
                # alternative existed this cycle.
                result = await _finalize({"cycle_id": cycle_id, "status": "NO_TRADE", "winner": eligible_for_execution[0], "candidates": ranked, "diversity_cap": diversity_cap, "confirmation_gate": confirmation_gate, "order_send_calls": 0})
                self._record_cycle(result, dry_run=dry_run)
                return result

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
                result = await _finalize({"cycle_id": cycle_id, "status": "SKIPPED_CONTEXT_RISK", "winner": best, "candidates": ranked, "context_risk": context_risk, "blockers": context_blockers, "diversity_cap": diversity_cap, "confirmation_gate": confirmation_gate, "order_send_calls": 0})
                self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                return result
            if economic_blockers:
                result = await _finalize({"cycle_id": cycle_id, "status": "SKIPPED_ECONOMIC_RISK", "winner": best, "candidates": ranked, "economic_context": economic_result, "blockers": economic_blockers, "diversity_cap": diversity_cap, "confirmation_gate": confirmation_gate, "order_send_calls": 0})
                self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                return result
            # Portfolio validation is a SEPARATE, binary permission gate -- independent of
            # signal confidence. A 94-confidence candidate can still be rejected here; its
            # stored trade_confidence_score is never rewritten because of this rejection.
            portfolio_allowed, portfolio_blockers = portfolio_manager.can_open_new_trade(self.account_id)
            _lap("portfolio_risk")
            if not portfolio_allowed:
                best["rejection_reasons"] = sorted(set((best.get("rejection_reasons") or []) + ["PORTFOLIO_RISK_LIMIT"]))
                result = await _finalize({"cycle_id": cycle_id, "status": "PORTFOLIO_REJECTED", "winner": best, "candidates": ranked, "blockers": portfolio_blockers, "diversity_cap": diversity_cap, "confirmation_gate": confirmation_gate, "order_send_calls": 0})
                self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                return result
            # 2026-08-18 fix: max_trades_per_day/max_trades_per_symbol_per_day/
            # post_trade_cooldown_minutes were declared in config.py but never enforced anywhere
            # in this file -- see _trade_frequency_blockers' own docstring. Same binary-gate
            # pattern as the portfolio check just above.
            trade_frequency_blockers = self._trade_frequency_blockers(best)
            if trade_frequency_blockers:
                best["rejection_reasons"] = sorted(set((best.get("rejection_reasons") or []) + trade_frequency_blockers))
                result = await _finalize({"cycle_id": cycle_id, "status": "TRADE_FREQUENCY_LIMITED", "winner": best, "candidates": ranked, "blockers": trade_frequency_blockers, "diversity_cap": diversity_cap, "confirmation_gate": confirmation_gate, "order_send_calls": 0})
                self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                return result
            # 2026-08-19 fix: real incident -- three independent strategies each shorted the same
            # symbol repeatedly through a sustained adverse move, each getting invalidated in
            # turn, across multiple accounts. See _symbol_direction_losing_streak_blockers'
            # own docstring.
            losing_streak_blockers = self._symbol_direction_losing_streak_blockers(best)
            if losing_streak_blockers:
                best["rejection_reasons"] = sorted(set((best.get("rejection_reasons") or []) + losing_streak_blockers))
                result = await _finalize({"cycle_id": cycle_id, "status": "SYMBOL_DIRECTION_LOSING_STREAK", "winner": best, "candidates": ranked, "blockers": losing_streak_blockers, "diversity_cap": diversity_cap, "confirmation_gate": confirmation_gate, "order_send_calls": 0})
                self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                return result
            # 2026-08-19: companion to the losing-streak gate above, for the case that gate can't
            # catch -- accounts piling into the SAME symbol+direction near-simultaneously, before
            # any of them has closed a losing trade yet to trip the streak check. See
            # _cross_account_concurrent_exposure_blockers' own docstring.
            concurrent_exposure_blockers = self._cross_account_concurrent_exposure_blockers(best)
            if concurrent_exposure_blockers:
                best["rejection_reasons"] = sorted(set((best.get("rejection_reasons") or []) + concurrent_exposure_blockers))
                result = await _finalize({"cycle_id": cycle_id, "status": "CROSS_ACCOUNT_CONCURRENT_EXPOSURE", "winner": best, "candidates": ranked, "blockers": concurrent_exposure_blockers, "diversity_cap": diversity_cap, "confirmation_gate": confirmation_gate, "order_send_calls": 0})
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
                        result = await _finalize({"cycle_id": cycle_id, "status": "SKIPPED_STALE_RISK_REWARD", "winner": best, "candidates": ranked, "blockers": ["RISK_REWARD_DEGRADED_SINCE_SCREENING"], "diversity_cap": diversity_cap, "confirmation_gate": confirmation_gate, "order_send_calls": 0})
                        self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                        return result
            except Exception as exc:
                logger.warning("Reward:risk pre-check failed (non-fatal, proceeding to submission): %s", exc.__class__.__name__)
            if not dry_run:
                await redis_layer.publish_event("mt5.candidate.selected", {"cycle_id": cycle_id, "symbol": best["broker_symbol"], "direction": best["direction"], "strategy": best.get("context", {}).get("strategy_id"), "confidence": best["trade_confidence"]["overall_score"]})
            submission = await self._submit(best, confidence=float(best["trade_confidence"]["overall_score"]), dry_run=dry_run)
            _lap("execution")
            result = await _finalize({"cycle_id": cycle_id, "status": submission["status"], "winner": best, "candidates": ranked, "trade": submission, "diversity_cap": diversity_cap, "confirmation_gate": confirmation_gate, "order_send_calls": submission.get("order_send_calls", 0)})
            self._record_cycle(result, dry_run=dry_run)
            return result

    async def _build_context_for_historical_intelligence(self, candidate: dict[str, Any]) -> Any | None:
        """Coverage-gap fix (Historical-Intelligence-Semantics-Audit directive, Phase 2): real
        root cause traced -- self._cycle_context_cache is populated ONLY for instruments where
        _screen()'s cheap_prefilter/regime_compatible check decided a full multi-strategy SMC
        context was worth building (Part 4's documented, deliberate optimization: "no compatible
        family for this regime -> the full context would be built only to feed strategies that
        evaluate_all() would skip anyway, so skip building it at all"). MTFAI1 candidates are
        scored WITHOUT that context (its own trend/SMA logic doesn't need it) and are exempted
        from that same regime-compatibility check -- so whenever the regime happens to be
        incompatible with every OTHER strategy family, an mtfai1 candidate reaches this ranking
        loop with no cached ctx at all, and Historical Intelligence (which DOES need ctx for
        fingerprinting: regime_broad, ATR, structure) silently had nothing to evaluate.

        This mirrors _entry_quality_score's own established cache-miss fallback exactly: refetch
        M15/H1/H4 candles (the SAME redis_layer.cached_candles L2 cache _screen() already
        populated this cycle -- a real cache hit, not a fresh broker call) and build a real
        StrategyContext fresh. Bounded to the handful of candidates that reach THIS ranking loop
        (top_k, not every discovered instrument), so this does not reintroduce the per-instrument
        cost Part 4's optimization was written to avoid. Returns None (never fabricates a partial
        context) if candles/quote aren't available or don't clear the SMC engine's minimum
        window -- build_strategy_context's own contract."""
        broker_symbol = candidate.get("broker_symbol")
        context = candidate.get("context") or {}
        canonical_symbol = context.get("symbol") or candidate.get("canonical_pair") or broker_symbol
        if not broker_symbol:
            return None
        try:
            m15 = await redis_layer.cached_candles(self.adapter, broker_symbol, "M15", count=100)
            h1 = await redis_layer.cached_candles(self.adapter, broker_symbol, "H1", count=100)
            h4 = await redis_layer.cached_candles(self.adapter, broker_symbol, "H4", count=100)
            bid = Decimal(str(context.get("bid"))) if context.get("bid") is not None else None
            ask = Decimal(str(context.get("ask"))) if context.get("ask") is not None else None
            spread = Decimal(str(context.get("spread"))) if context.get("spread") is not None else None
            if bid is None or ask is None:
                quote = await redis_layer.cached_latest_tick(self.adapter, broker_symbol)
                bid, ask, spread = quote.bid, quote.ask, quote.spread
            if bid is None or ask is None or len(m15) < 60 or len(h1) < 50 or len(h4) < 30:
                return None
            m15_rows = [row.model_dump(mode="json") for row in m15]
            h1_rows = [row.model_dump(mode="json") for row in h1]
            h4_rows = [row.model_dump(mode="json") for row in h4]
            return build_strategy_context(symbol=str(canonical_symbol), broker_symbol=str(broker_symbol), m15_rows=m15_rows, h1_rows=h1_rows, h4_rows=h4_rows, bid=bid, ask=ask, spread=spread or Decimal("0"))
        except Exception as exc:
            logger.warning("Historical intelligence fallback context build failed for %s (non-fatal): %s", broker_symbol, exc.__class__.__name__)
            return None

    async def _apply_historical_intelligence(self, candidate: dict[str, Any], confidence: dict[str, Any]) -> None:
        """Historical-Intelligence-Semantics-Audit directive: the live-influencing evaluation
        path (historical_intelligence.entry_intelligence.evaluate_historical_intelligence) was
        fully built, tested, and documented as "called from the live screening path" -- but had
        NO actual caller anywhere in production; the only real caller was record_observations()
        (backend/historical_intelligence/entry_intelligence.py), which runs strictly AFTER a
        cycle's decision is already finalized, for persistence only. This is the fix: called
        HERE, before the confidence-threshold eligibility check below, so real POSITIVE evidence
        can nudge a borderline candidate's score up, real NEGATIVE evidence can nudge it down or
        (for the strongest, most reliable negative signal) flag it for exclusion the same way
        BELOW_CONFIDENCE_THRESHOLD already does -- and MIXED/INSUFFICIENT evidence changes
        nothing, exactly as entry_intelligence.py's own gating already guarantees.

        Bounded, observation-safe, and never a hard override: the adjustment is capped at
        +/-10 points (entry_intelligence._MAX_LIVE_RANKING_ADJUSTMENT) on a confidence score
        that itself has an independent threshold gate, and a REJECT verdict only ever ADDS a
        rejection reason -- it never bypasses the confidence check, portfolio/risk blockers,
        FTMO limits, or execution safety, all of which run downstream of this, unchanged. Any
        failure here (missing context, Redis/Postgres error, mode not DEMO_ACTIVE) degrades to
        a strict no-op, matching entry_intelligence.py's own fail-open contract -- this method
        never raises.

        Every eligible candidate that reaches this method ends up with a non-None
        candidate["historical_intelligence"] dict (Phase 3 -- "do not silently skip candidates
        because cycle context is unavailable"): either a real evaluation, or an explicit
        {"status": "UNAVAILABLE", "reason": "HIST_INTEL_CONTEXT_UNAVAILABLE"} when context truly
        could not be reconstructed -- never fabricated, never left unset."""
        rank_before = candidate.get("ranking_score")
        try:
            from backend.historical_intelligence.entry_intelligence import evaluate_historical_intelligence

            broker_symbol = candidate.get("broker_symbol")
            context = candidate.get("context") or {}
            strategy_id = context.get("strategy_id")
            if not broker_symbol or not strategy_id:
                candidate["historical_intelligence"] = {"status": "UNAVAILABLE", "reason": "HIST_INTEL_CONTEXT_UNAVAILABLE", "ranking_adjustment": 0.0, "defer_reject_reason": None, "rank_before": rank_before, "rank_after": rank_before}
                return

            cached_ctx = self._cycle_context_cache.get(broker_symbol)
            context_source = "cycle_cache"
            if cached_ctx is None:
                cached_ctx = await self._build_context_for_historical_intelligence(candidate)
                context_source = "fallback_rebuild" if cached_ctx is not None else "unavailable"
            if cached_ctx is None:
                candidate["historical_intelligence"] = {"status": "UNAVAILABLE", "reason": "HIST_INTEL_CONTEXT_UNAVAILABLE", "ranking_adjustment": 0.0, "defer_reject_reason": None, "context_source": context_source, "rank_before": rank_before, "rank_after": rank_before}
                return

            evaluation = await evaluate_historical_intelligence(
                ctx=cached_ctx, strategy_id=str(strategy_id),
                contributing_strategies=list(context.get("contributing_strategies") or [strategy_id]),
                strategy_family=context.get("strategy_family"),
                entry=float(candidate["entry"]), stop_loss=float(candidate["stop_loss"]), take_profit=float(candidate["take_profit"]),
                entry_time=utcnow(), confidence_band=confidence.get("band"),
            )
            evaluation["context_source"] = context_source
        except Exception as exc:
            logger.warning("Historical intelligence ranking evaluation failed for %s (non-fatal, candidate unaffected): %s", candidate.get("broker_symbol"), exc.__class__.__name__)
            candidate["historical_intelligence"] = {"status": "UNAVAILABLE", "reason": f"HISTORICAL_INTELLIGENCE_UNAVAILABLE:{exc.__class__.__name__}", "ranking_adjustment": 0.0, "defer_reject_reason": None, "rank_before": rank_before, "rank_after": rank_before}
            return

        adjustment = float(evaluation.get("ranking_adjustment") or 0.0)
        if adjustment:
            confidence["overall_score"] = max(0.0, min(100.0, confidence["overall_score"] + adjustment))
            candidate["ranking_score"] = confidence["overall_score"]
        if evaluation.get("defer_reject_reason"):
            candidate["rejection_reasons"] = sorted(set((candidate.get("rejection_reasons") or []) + ["HISTORICAL_EVIDENCE_STRONGLY_NEGATIVE"]))
        evaluation["rank_before"] = rank_before
        evaluation["rank_after"] = candidate.get("ranking_score")
        candidate["historical_intelligence"] = evaluation

    async def _rank_candidates_by_confidence(self, cycle_id: str, top_k: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deep-scores each of the top-K screened candidates (SMC/ICT structure score,
        recorded symbol/strategy performance, portfolio correlation) into a deterministic
        trade_confidence_score, then returns them ranked best-first. Pure local computation
        and DB reads only -- no broker mutation, no AI/provider call."""
        try:
            exposure = portfolio_manager.exposure(self.account_id)
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
                symbol_memory, global_memory = confidence_memory_for_symbol(candidate["canonical_pair"], self.account_id)
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
            # Hard timeout (Part 15/24 -- "must not materially delay M5 cycles", "throttle
            # historical workers if they degrade M5 cadence"): a real M5 cycle stalled well
            # beyond any previously observed duration on the very first live run of this new
            # evaluation path -- root cause not yet isolated (a Redis/Postgres connection-pool
            # wait with no explicit timeout is the leading suspect, but unconfirmed). Rather
            # than risk repeating that stall indefinitely while investigating, this bounds the
            # call so a single candidate's historical-intelligence evaluation can never hold up
            # the cycle past a few seconds -- a timeout here degrades to the SAME safe no-op
            # _apply_historical_intelligence's own except-Exception path already produces.
            try:
                await asyncio.wait_for(self._apply_historical_intelligence(candidate, confidence), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Historical intelligence evaluation timed out for %s (candidate unaffected, cycle continues)", candidate.get("broker_symbol"))
                candidate.setdefault("historical_intelligence", {"status": "UNAVAILABLE", "reason": "HISTORICAL_INTELLIGENCE_TIMEOUT", "ranking_adjustment": 0.0, "defer_reject_reason": None})
            if not is_autonomous_eligible(confidence["overall_score"], min_trade_confidence=self.config.min_trade_confidence):
                candidate["rejection_reasons"] = sorted(set((candidate.get("rejection_reasons") or []) + ["BELOW_CONFIDENCE_THRESHOLD"]))
            scored.append(candidate)
        return rank_candidates(scored)

    async def _global_blockers(self) -> list[str]:
        blockers = await self.execution.safety_blockers()
        store = self._stored()
        if store.get("emergency_disabled"):
            blockers.append("EMERGENCY_DISABLED")
        if self.config.manual_acceptance_enabled:
            blockers.append("MANUAL_ACCEPTANCE_ENABLED")
        account = await self.adapter.mt5_account()
        with SessionLocal() as db:
            protection = evaluate_entry_protection(db, self.account_id, self.config, balance=account.balance, equity=account.equity)
        # PROP_DAILY_LOSS_ENTRY_BLOCK / PROP_MAX_LOSS_ENTRY_BLOCK / PROP_INTERNAL_DAILY_BUFFER_BLOCK /
        # PROP_INTERNAL_MAX_LOSS_BUFFER_BLOCK -- fed a real persisted daily baseline and real live
        # equity, not the zero-P&L defaults that previously made this check permanently inert.
        blockers.extend(protection["entry_block_reasons"])
        risk = risk_status(
            self.config,
            equity=account.equity,
            balance=account.balance,
            daily_pnl=protection["daily_pnl_total"],
            total_pnl=account.equity - protection["initial_balance"],
        )
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
        # Phase 3 (Forex/MT5 roadmap): a read-only, account-level reconciliation verdict computed
        # once per cycle -- never a fresh broker comparison inline here (that's a separate,
        # scheduled/on-demand pass; see backend.brokers.mt5.reconciliation_watchdog). Deliberately
        # conservative: this only blocks NEW entries for this account. It never touches sizing,
        # confidence, or existing-position management (the Adaptive Manager reads broker state
        # directly every cycle, independent of this verdict), and it never affects other accounts.
        account_state_trustworthy = is_account_state_trustworthy(self.account_id)

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
            if not account_state_trustworthy:
                reasons.append("ACCOUNT_STATE_UNTRUSTED")
            _fetch_t0 = time.perf_counter()
            try:
                # L1 (_cycle_context_cache, below) -> Redis L2 -> MT5 broker fetch. Candle-
                # boundary-keyed (not just TTL), so a symbol re-fetched later THIS SAME cycle
                # (_entry_quality_score, _submit's tick) or by another process reuses these
                # exact rows until the next M15/H1/H4 bar actually closes -- never across it.
                quote = await redis_layer.cached_latest_tick(self.adapter, instrument.broker_symbol)
                m15 = await redis_layer.cached_candles(self.adapter, instrument.broker_symbol, "M15", count=100)
                h1 = await redis_layer.cached_candles(self.adapter, instrument.broker_symbol, "H1", count=100)
                h4 = await redis_layer.cached_candles(self.adapter, instrument.broker_symbol, "H4", count=100)
            except Exception as exc:
                market_data_ms += (time.perf_counter() - _fetch_t0) * 1000
                rows.append(_candidate(instrument, reasons + [f"DATA_UNAVAILABLE:{exc.__class__.__name__}"]))
                continue
            market_data_ms += (time.perf_counter() - _fetch_t0) * 1000
            if len(m15) < 60 or len(h1) < 50 or len(h4) < 30:
                reasons.append("HISTORY_UNAVAILABLE")
                rows.append(_candidate(instrument, reasons))
                continue
            # Phase 8 (Forex/MT5 roadmap): market_data.candle_quality() already detects
            # impossible OHLC (high<low, non-positive open/close) -- it was computed by the
            # eligible_candles() API but never actually consulted by this, the real live
            # eligibility path. The length check above stays exactly as tuned; this only adds
            # the OHLC-sanity half candle_quality already implements, never touching thresholds.
            if any("DATA_QUALITY_FAILED" in candle_quality(tf_rows) for tf_rows in (m15, h1, h4)):
                reasons.append("DATA_QUALITY_FAILED")
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
            # 2026-08-17: was hardcoded "ACTIVE_MT5" -- MTFAI1 was the only strategy in the whole
            # multi-strategy layer with NO working activation kill switch (its own scoring is
            # inline here, entirely outside STRATEGY_FAMILIES/EVALUATORS, so it never went
            # through activation_status() the way every other family already does). This wires it
            # into the SAME gate mechanism just below (eligible_for_execution's shadow check,
            # "MTFAI1's own rows always carry it explicitly now" -- that comment predates this
            # fix and was aspirational until now). MT5_STRATEGY_ACTIVATION_MTFAI1=SHADOW_MT5 now
            # actually demotes it (still scored/tracked for calibration, never executed) instead
            # of silently doing nothing.
            row = {**_candidate(instrument, reasons), "direction": direction, "ranking_score": score, "context": context, "context_hash": _hash(context), "strategy_activation": activation_status("mtfai1"), **geometry}
            rows.append(row)

            if should_analyze:
                counters["symbols_after_prefilter"] += 1
                row_by_symbol[instrument.broker_symbol] = row
                # Part 5: analyze_bars() cache lookup happens HERE, in the main event loop
                # (redis_layer's Redis client belongs to this loop) -- never inside the
                # asyncio.to_thread() worker below, which runs in its own OS thread with no
                # event loop of its own. A hit is threaded into _build_multi_strategy_analysis
                # so that worker skips analyze_bars() entirely for that timeframe; a miss still
                # computes it in the thread as before, and the fresh result is written back to
                # Redis after asyncio.gather() returns below (still the main loop).
                cache_version = redis_layer.cache_version()
                cached_m15 = await redis_layer.cache_get(redis_layer.context_key(cache_version, instrument.broker_symbol, "M15"))
                cached_h1 = await redis_layer.cache_get(redis_layer.context_key(cache_version, instrument.broker_symbol, "H1"))
                cached_h4 = await redis_layer.cache_get(redis_layer.context_key(cache_version, instrument.broker_symbol, "H4"))
                for _cached in (cached_m15, cached_h1, cached_h4):
                    redis_layer.note_smc_cache_result(_cached is not None)
                pending.append({
                    "instrument": instrument,
                    "m15_rows": m15_rows,
                    "h1_rows": [c.model_dump(mode="json") for c in h1],
                    "h4_rows": [c.model_dump(mode="json") for c in h4],
                    "bid": quote.bid, "ask": quote.ask, "spread": quote.spread,
                    "regime_info": regime_info,
                    "cached_m15": cached_m15, "cached_h1": cached_h1, "cached_h4": cached_h4,
                })

        _analysis_t0 = time.perf_counter()
        if pending:
            semaphore = asyncio.Semaphore(max(1, _MULTI_STRATEGY_CONCURRENCY))

            def _reconstruct_snapshot(cached: Any) -> Any:
                if cached is None:
                    return None
                try:
                    from backend.market_structure.models import MarketStructureSnapshot

                    return MarketStructureSnapshot.model_validate(cached)
                except Exception:
                    return None  # malformed cache entry -- worker recomputes, never raises

            async def _run(item: dict[str, Any]):
                async with semaphore:
                    return await asyncio.to_thread(
                        _build_multi_strategy_analysis, item["instrument"], cycle_id,
                        item["m15_rows"], item["h1_rows"], item["h4_rows"],
                        item["bid"], item["ask"], item["spread"], item["regime_info"],
                        m15_snapshot=_reconstruct_snapshot(item.get("cached_m15")),
                        h1_snapshot=_reconstruct_snapshot(item.get("cached_h1")),
                        h4_snapshot=_reconstruct_snapshot(item.get("cached_h4")),
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
                    # Write back only what was actually a MISS above -- re-writing an already-
                    # cached hit would just reset its TTL for no benefit (it's still valid for
                    # the same bar identity either way).
                    cache_version = redis_layer.cache_version()
                    if item.get("cached_m15") is None:
                        await redis_layer.cache_set(redis_layer.context_key(cache_version, instrument.broker_symbol, "M15"), ctx.m15_snapshot.model_dump(mode="json"), redis_layer.seconds_until_next_bar("M15"))
                    if item.get("cached_h1") is None and ctx.h1_snapshot is not None:
                        await redis_layer.cache_set(redis_layer.context_key(cache_version, instrument.broker_symbol, "H1"), ctx.h1_snapshot.model_dump(mode="json"), redis_layer.seconds_until_next_bar("H1"))
                    if item.get("cached_h4") is None and ctx.h4_snapshot is not None:
                        await redis_layer.cache_set(redis_layer.context_key(cache_version, instrument.broker_symbol, "H4"), ctx.h4_snapshot.model_dump(mode="json"), redis_layer.seconds_until_next_bar("H4"))
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
        # Summary, not per-candidate (a cycle can screen dozens of symbols x strategies --
        # one event per candidate would be unbounded volume for an observability channel).
        if cycle_id:
            await redis_layer.publish_event("mt5.candidate.generated", {"cycle_id": cycle_id, "candidates_total": counters["candidates_total"], "symbols_after_prefilter": counters["symbols_after_prefilter"]})
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
            risk_snapshot = portfolio_manager.risk(self.account_id)
            open_risk = float(risk_snapshot.get("open_risk") or 0)
            portfolio_exposure_ratio = open_risk / float(self.config.max_total_open_risk_usd) if self.config.max_total_open_risk_usd else 0.0
        except Exception:
            portfolio_exposure_ratio = 0.0
        try:
            exposure = portfolio_manager.exposure(self.account_id)
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
                bars = await redis_layer.cached_candles(self.adapter, candidate["broker_symbol"], "M15", count=100)
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
        # symbol_info (contract spec: stops_level/point/tick_value/...) changes on the order of
        # minutes to never, so it's cached (Part 11) -- but the entry-price quote right before
        # order submission is deliberately fetched fresh, uncached, every time: this is the one
        # number that directly determines the executed price, and Part 6 explicitly disallows
        # reusing anything execution-decision-adjacent across a recomputation boundary.
        symbol = await redis_layer.cached_symbol_info(self.adapter, candidate["broker_symbol"])
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
        # every entry before the very first portfolio snapshot has run. The aggregate cap itself
        # is account-equity-scaled (max_total_open_risk_percent), not the old flat
        # max_total_open_risk_usd constant that capped every account at the same dollar figure.
        portfolio_available_risk_usd = None
        latest_snapshot = portfolio_manager.latest_snapshot(self.account_id)
        if latest_snapshot is not None:
            open_risk = float(latest_snapshot.get("open_risk") or 0)
            aggregate_cap_usd = float(account.equity) * self.config.max_total_open_risk_percent / 100.0
            portfolio_available_risk_usd = Decimal(str(max(0.0, aggregate_cap_usd - open_risk)))
        with SessionLocal() as db:
            protection = evaluate_entry_protection(db, self.account_id, self.config, balance=account.balance, equity=account.equity)
        prop_remaining_budget_usd = remaining_safety_budget_usd(protection)
        risk = await self.execution.calculate_risk_size(
            account_equity=account.equity, symbol=symbol, direction=candidate["direction"], entry=entry,
            stop=Decimal(str(candidate["stop_loss"])), target=Decimal(str(candidate["take_profit"])),
            risk_budget_adjustment=risk_adjustment_detail, portfolio_available_risk_usd=portfolio_available_risk_usd,
            prop_remaining_budget_usd=prop_remaining_budget_usd,
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
            account_id=self.account_id,
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
                await economic_intelligence_service.link_execution(snapshot_id, f"mt5-entry:{self.account_id}:{intent.intent_id}:{intent.context_hash}")
            except Exception as exc:
                logger.warning("Economic intelligence outcome-link failed (non-fatal): %s", exc.__class__.__name__)
        projected_margin = await self.execution.order_calc_margin(intent)
        send_count_before = self.execution.order_send_calls
        await redis_layer.publish_event("mt5.order.submitted", {"symbol": candidate["broker_symbol"], "direction": candidate["direction"], "strategy": candidate.get("context", {}).get("strategy_id"), "intent_id": intent_id})
        result = await self.execution.submit_market_order(intent, economic_context=economic_context)
        order_send_calls = max(0, self.execution.order_send_calls - send_count_before)
        await redis_layer.publish_event(
            "mt5.order.accepted" if result.status == "ACCEPTED" else "mt5.order.rejected",
            {"symbol": candidate["broker_symbol"], "direction": candidate["direction"], "intent_id": intent_id, "status": result.status, "ticket": result.order_ticket},
        )
        trade = {
            "trade_id": intent_id,
            "intent": intent.model_dump(mode="json"),
            "risk": risk.model_dump(mode="json"),
            "risk_budget_adjustment": risk_adjustment_detail,
            "projected_margin": str(projected_margin) if projected_margin is not None else None,
            "submission": result.model_dump(mode="json"),
            "order_send_calls": order_send_calls,
            "open_timestamp": utcnow().isoformat() if result.status == "ACCEPTED" else None,
            # Part: MTFAI1 DEMO diversity cap -- strategy attribution carried onto the execution
            # record itself (not just the candidate) so _mtfai1_rolling_count can compute the
            # rolling last-N-executed window from self.state.trades without re-deriving it from
            # anywhere else.
            "strategy_id": normalize_strategy_id((candidate.get("context") or {}).get("strategy_id")),
        }
        self.state.trades.insert(0, trade | {"created_at": utcnow().isoformat()})
        if result.status == "ACCEPTED":
            store = self._daily_trade_state()
            store["entries_submitted_today"] = int(store.get("entries_submitted_today") or 0) + 1
            symbol = candidate.get("broker_symbol")
            if symbol:
                entries_by_symbol = dict(store.get("entries_by_symbol") or {})
                entries_by_symbol[symbol] = int(entries_by_symbol.get(symbol) or 0) + 1
                store["entries_by_symbol"] = entries_by_symbol
                last_entry_at_by_symbol = dict(store.get("last_entry_at_by_symbol") or {})
                last_entry_at_by_symbol[symbol] = utcnow().isoformat()
                store["last_entry_at_by_symbol"] = last_entry_at_by_symbol
            self._set_stored(store)
        return trade | {"status": result.status}

    def _economic_blockers(self, economic_result: dict[str, Any]) -> list[str]:
        guard = economic_result.get("guard") or {}
        decision = str(guard.get("decision") or "ALLOW").upper()
        if decision in {"BLOCK", "DELAY"}:
            return sorted(set(f"ECONOMIC_{decision}:{reason}" for reason in (guard.get("reason_codes") or [decision])))
        return []

    def _daily_trade_state(self) -> dict[str, Any]:
        """Day-boundary-aware trade-frequency counters (2026-08-18 fix -- config.py's
        max_trades_per_day/max_trades_per_symbol_per_day/post_trade_cooldown_minutes were
        declared but never enforced anywhere in this file: entries_submitted_today was
        incremented in _submit() but never compared against max_trades_per_day, and the DB-backed
        get_state/set_state store it lives in has no TTL/expiry, so the counter never reset at a
        day boundary either -- confirmed live: 65-85 real entries on single days against a
        nominal cap of 20. Mirrors backend.intelligence.trading.auto_paper.py's own
        _ensure_daily_state pattern (same underlying get_state/set_state store), which already
        enforces the identical fields correctly for the AI paper-trading path.

        Resets entries_submitted_today/entries_by_symbol/last_entry_at_by_symbol whenever the
        stored date no longer matches today's UTC date, and persists the reset immediately."""
        store = self._stored()
        today = utcnow().date().isoformat()
        if store.get("trade_cap_date") != today:
            store["trade_cap_date"] = today
            store["entries_submitted_today"] = 0
            store["entries_by_symbol"] = {}
            store["last_entry_at_by_symbol"] = {}
            self._set_stored(store)
        return store

    def _trade_frequency_blockers(self, candidate: dict[str, Any]) -> list[str]:
        """Binary gate, independent of confidence -- same pattern as _context_blockers/
        _economic_blockers/portfolio_manager.can_open_new_trade, called alongside them in
        run_cycle. Never partially enforced: a candidate is either allowed through cleanly or
        rejected with an explicit reason, exactly matching auto_paper.py's DAILY_TRADE_LIMIT/
        SYMBOL_DAILY_TRADE_LIMIT/COOLDOWN vocabulary."""
        store = self._daily_trade_state()
        blockers: list[str] = []
        if int(store.get("entries_submitted_today") or 0) >= self.config.max_trades_per_day:
            blockers.append("DAILY_TRADE_LIMIT")
        symbol = candidate.get("broker_symbol")
        if symbol:
            entries_by_symbol = store.get("entries_by_symbol") or {}
            if int(entries_by_symbol.get(symbol) or 0) >= self.config.max_trades_per_symbol_per_day:
                blockers.append("SYMBOL_DAILY_TRADE_LIMIT")
            last_entry_raw = (store.get("last_entry_at_by_symbol") or {}).get(symbol)
            last_entry_at = _parse_dt(last_entry_raw) if last_entry_raw else None
            if last_entry_at and utcnow() < last_entry_at + timedelta(minutes=self.config.post_trade_cooldown_minutes):
                blockers.append("COOLDOWN")
        return blockers

    def _symbol_direction_losing_streak_blockers(self, candidate: dict[str, Any]) -> list[str]:
        """2026-08-19: user-reported real incident -- three independent strategies
        (ema_trend/smc_continuation/mtfai1) each shorted XAUUSD overnight, across multiple
        accounts, while gold ground higher through a sustained (choppy but net-bullish) move;
        each got invalidated/critical in turn and a fresh SHORT was re-entered anyway. No
        existing gate looks at this -- trade-frequency limits cap volume, not direction; the
        adaptive manager cuts a bad trade AFTER entry but has no say over the NEXT entry.

        Binary gate, same pattern as _trade_frequency_blockers: if the last
        MT5_LOSING_STREAK_THRESHOLD (default 3) CLOSED trades on this exact symbol+direction --
        across every account, not just this one, since the incident hit several accounts making
        the identical directional call independently -- all ended with an adverse thesis
        classification (exit_reason invalidated/critical), new entries in that SAME direction on
        that symbol are blocked for MT5_LOSING_STREAK_COOLDOWN_HOURS (default 2) from the most
        recent one. The OPPOSITE direction on the same symbol is untouched -- this is a
        directional read-check, not a symbol-wide pause. Fails open (never blocks) on any error,
        insufficient history, or a mixed streak -- one healthy exit anywhere in the last N breaks
        it. Reversible via MT5_LOSING_STREAK_CONFIRMATION_REQUIRED (default true)."""
        if os.getenv("MT5_LOSING_STREAK_CONFIRMATION_REQUIRED", "true").strip().lower() in {"false", "0", "off", "no"}:
            return []
        symbol = candidate.get("broker_symbol")
        direction = candidate.get("direction")
        if not symbol or direction not in {"LONG", "SHORT"}:
            return []
        threshold = _env_int("MT5_LOSING_STREAK_THRESHOLD", 3)
        cooldown_hours = _env_float("MT5_LOSING_STREAK_COOLDOWN_HOURS", 2.0)
        adverse_reasons = {"invalidated", "critical"}
        try:
            with SessionLocal() as db:
                rows = (
                    db.query(MT5CandidateEvaluationORM.exit_reason, MT5CandidateEvaluationORM.created_at)
                    .filter(
                        MT5CandidateEvaluationORM.broker_symbol == symbol,
                        MT5CandidateEvaluationORM.direction == direction,
                        MT5CandidateEvaluationORM.outcome_type == "EXECUTED",
                        MT5CandidateEvaluationORM.outcome_status == "CLOSED",
                    )
                    .order_by(MT5CandidateEvaluationORM.created_at.desc())
                    .limit(threshold)
                    .all()
                )
        except Exception as exc:
            logger.warning("MT5 losing-streak check failed for %s %s (fails open): %s", symbol, direction, exc.__class__.__name__)
            return []
        if len(rows) < threshold or any(reason not in adverse_reasons for reason, _created_at in rows):
            return []
        most_recent_adverse_at = rows[0][1]
        if most_recent_adverse_at and most_recent_adverse_at.tzinfo is None:
            most_recent_adverse_at = most_recent_adverse_at.replace(tzinfo=timezone.utc)
        if most_recent_adverse_at and utcnow() < most_recent_adverse_at + timedelta(hours=cooldown_hours):
            return ["SYMBOL_DIRECTION_LOSING_STREAK"]
        return []

    def _cross_account_concurrent_exposure_blockers(self, candidate: dict[str, Any]) -> list[str]:
        """2026-08-19: same incident as _symbol_direction_losing_streak_blockers, but that gate
        only fires AFTER MT5_LOSING_STREAK_THRESHOLD unanimous adverse closes accumulate.
        Tonight's actual gold cluster was near-simultaneous instead: ftmo_demo_100k and
        ftmo_demo_25k both went SHORT XAUUSD (mtfai1) 18 minutes apart, independently, before
        either had a losing trade on record -- the losing-streak gate would not have caught
        that. Each of the 4 accounts runs its own independent run_cycle() (see
        MT5MultiAccountAutonomousOrchestrator.run_cycle), so nothing previously stopped them
        from turning one directional read into several separately-sized copies of the same bet,
        multiplying the loss when that read was wrong.

        Reads each OTHER account's latest PortfolioSnapshotORM (refreshed independently every
        ~15s by PortfolioManager -- a cheap DB read, not a live MT5 round-trip) and counts how
        many already hold an open position in this exact symbol+direction. Once that count
        reaches MT5_CONCURRENT_EXPOSURE_MAX_ACCOUNTS (default 2 -- shared conviction across a
        couple of accounts is fine, but a hard stop on every account piling into one idea),
        new entries into it from any additional account are blocked. A stale or missing
        snapshot for another account excludes that account from the count rather than blocking
        (fails open per-account) -- a snapshot gap elsewhere must not stop trading here; this is
        the opposite of can_open_new_trade's fail-CLOSED staleness rule, which protects the
        SAME account's own margin/risk state, not a best-effort cross-account read. Reversible
        via MT5_CONCURRENT_EXPOSURE_CONFIRMATION_REQUIRED (default true)."""
        if os.getenv("MT5_CONCURRENT_EXPOSURE_CONFIRMATION_REQUIRED", "true").strip().lower() in {"false", "0", "off", "no"}:
            return []
        symbol = candidate.get("broker_symbol")
        direction = candidate.get("direction")
        if not symbol or direction not in {"LONG", "SHORT"}:
            return []
        max_accounts = _env_int("MT5_CONCURRENT_EXPOSURE_MAX_ACCOUNTS", 2)
        snapshot_max_age_seconds = _env_float("MT5_CONCURRENT_EXPOSURE_SNAPSHOT_MAX_AGE_SECONDS", 180.0)
        wanted_type = 0 if direction == "LONG" else 1
        holder_accounts: set[str] = set()
        for profile in account_registry.configured_profiles():
            if not profile.enabled or profile.account_id == self.account_id:
                continue
            try:
                snapshot = portfolio_manager.latest_snapshot(profile.account_id)
            except Exception as exc:
                logger.warning("MT5 concurrent-exposure check failed reading snapshot for %s (excluded, fails open): %s", profile.account_id, exc.__class__.__name__)
                continue
            if not snapshot:
                continue
            created_at = _parse_dt(snapshot.get("created_at"))
            if created_at is not None:
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                if (utcnow() - created_at).total_seconds() > snapshot_max_age_seconds:
                    continue
            positions = (snapshot.get("raw_payload") or {}).get("positions") or []
            if any(pos.get("symbol") == symbol and pos.get("type") == wanted_type for pos in positions):
                holder_accounts.add(profile.account_id)
        if len(holder_accounts) >= max_accounts:
            return ["CROSS_ACCOUNT_CONCURRENT_EXPOSURE"]
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
        store = self._stored()
        if mark_processed and result.get("cycle_id"):
            processed = list(store.get("processed_candles") or [])
            if result["cycle_id"] not in processed:
                processed.append(result["cycle_id"])
            store["processed_candles"] = processed[-200:]
        store["cycles"] = self.state.cycles[:100]
        store["decisions"] = self.state.decisions[:100]
        store["trades"] = self.state.trades[:100]
        store["last_result"] = result
        self._set_stored(store)
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
        # Historical Intelligence Phase 2 (Option B) -- observation-only, reads only the
        # in-memory per-cycle context cache already populated by _screen(), never re-fetches
        # from the broker, never affects the decision already made above. Failure here must
        # never surface as a trading-cycle error.
        try:
            from backend.historical_intelligence.snapshot_capture import capture_decision_snapshots

            capture_decision_snapshots(self, result)
        except Exception as exc:
            logger.warning("Historical intelligence decision snapshot capture failed: %s", exc.__class__.__name__)
        # Historical Intelligence Phase 3/4 (Part 21) -- STRICTLY OBSERVATION ONLY. Runs after
        # the cycle's decision is already finalized; computes (but never applies) a historical
        # evidence score per candidate for observability. Gated internally by
        # modes.demo_active_enabled() and per-strategy trust -- a no-op (records nothing beyond
        # an UNAVAILABLE status) whenever those gates aren't met. Failure here must never
        # surface as a trading-cycle error.
        try:
            from backend.historical_intelligence.entry_intelligence import record_observations

            record_observations(self, result)
        except Exception as exc:
            logger.warning("Historical intelligence observation capture failed: %s", exc.__class__.__name__)

    def status(self) -> dict[str, Any]:
        store = self._stored()
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
        return list(self._stored().get("cycles") or [])

    def decisions(self) -> list[dict[str, Any]]:
        return list(self._stored().get("decisions") or [])

    def trades(self) -> list[dict[str, Any]]:
        return list(self._stored().get("trades") or [])

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
        with SessionLocal() as db:
            protection = evaluate_entry_protection(db, self.account_id, self.config, balance=account.balance, equity=account.equity)
        internal = risk_status(
            self.config,
            equity=account.equity,
            balance=account.balance,
            daily_pnl=protection["daily_pnl_total"],
            total_pnl=account.equity - protection["initial_balance"],
        )
        return {
            **internal,
            "account_id": self.account_id,
            "daily_state": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in protection.items() if k != "challenge_status"},
            "prop_challenge_status": protection["challenge_status"],
            "entry_block_reasons": protection["entry_block_reasons"],
        }

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
        result = {"account_id": self.account_id, "status": status, "positions": owned_positions, "orders": owned_orders, "created_at": utcnow().isoformat()}
        try:
            update_trade_reconciliation(result)
            deals = await self.adapter.history_deals(days=14)
            result["history_sync"] = update_trade_history([deal.model_dump(mode="json") for deal in deals], self.account_id)
        except Exception as exc:
            logger.warning("MT5 reconciliation persistence failed: %s", exc.__class__.__name__)
            result["history_sync"] = {"status": "UNAVAILABLE", "error": exc.__class__.__name__}
        return result

    async def emergency_disable(self) -> dict[str, Any]:
        store = self._stored()
        store["emergency_disabled"] = True
        self._set_stored(store)
        return {"emergency_disabled": True}

    async def emergency_enable(self) -> dict[str, Any]:
        blockers = await self._global_blockers()
        blockers = [b for b in blockers if b != "EMERGENCY_DISABLED"]
        if blockers:
            return {"emergency_disabled": True, "enabled": False, "blockers": blockers}
        store = self._stored()
        store["emergency_disabled"] = False
        self._set_stored(store)
        return {"emergency_disabled": False, "enabled": True}

    def _load_state(self) -> None:
        store = self._stored()
        self.state.emergency_disabled = bool(store.get("emergency_disabled"))
        self.state.cycles = list(store.get("cycles") or [])
        self.state.decisions = list(store.get("decisions") or [])
        self.state.trades = list(store.get("trades") or [])
        self.state.last_result = store.get("last_result")

    def _persist_state(self) -> None:
        store = self._stored()
        store["cycles"] = self.state.cycles[:100]
        store["decisions"] = self.state.decisions[:100]
        store["trades"] = self.state.trades[:100]
        store["last_result"] = self.state.last_result
        self._set_stored(store)

    def _acquire_lock(self, owner: str) -> bool:
        state = get_state(self._lock_key())
        heartbeat = _parse_dt(state.get("heartbeat_at"))
        if state.get("owner") and heartbeat and utcnow() - heartbeat < timedelta(minutes=20):
            self.state.scheduler_owner = state.get("owner")
            return state.get("owner") == owner
        set_state(self._lock_key(), {"owner": owner, "heartbeat_at": utcnow().isoformat(), "started_at": utcnow().isoformat()})
        return True

    def _release_lock(self) -> None:
        state = get_state(self._lock_key())
        if state.get("owner") == self.state.scheduler_owner:
            set_state(self._lock_key(), {})

    def _heartbeat(self, owner: str) -> None:
        set_state(self._lock_key(), {"owner": owner, "heartbeat_at": utcnow().isoformat(), "started_at": self.state.scheduler_started_at.isoformat() if self.state.scheduler_started_at else utcnow().isoformat()})

    def _assert_owner(self, owner: str) -> None:
        if self.state.scheduler_owner and owner not in {self.state.scheduler_owner, "local", "api"}:
            raise RuntimeError("MT5 autonomous scheduler owner mismatch")


class MT5MultiAccountAutonomousOrchestrator:
    """Scheduler facade that fans each M5 autonomous cycle across enabled MT5 accounts.

    The 10K service remains the default compatibility target for private helpers and legacy
    routes, while the scheduler/API run path evaluates every enabled configured profile with
    account-scoped state, locks, portfolio checks, sizing, and bridge routing.
    """

    def __init__(self, default_service: MT5AutonomousTradingService | None = None) -> None:
        self.default_service = default_service or MT5AutonomousTradingService()
        self._services: dict[str, MT5AutonomousTradingService] = {"demo_10k": self.default_service}
        self.state = MT5AutonomousState()
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    @property
    def config(self) -> MT5Config:
        return self.default_service.config

    def __getattr__(self, name: str) -> Any:
        return getattr(self.default_service, name)

    def _enabled_profiles(self) -> list[account_registry.MT5AccountProfile]:
        return [profile for profile in account_registry.configured_profiles() if profile.enabled]

    def _enabled_services(self) -> dict[str, MT5AutonomousTradingService]:
        services: dict[str, MT5AutonomousTradingService] = {}
        for profile in self._enabled_profiles():
            if profile.account_id == "demo_10k":
                service = self.default_service
                if hasattr(service.adapter, "reload_config"):
                    service.adapter.reload_config()
                service.config = service.adapter.config
                service.execution = MT5ExecutionService(service.adapter)
            else:
                service = self._services.get(profile.account_id)
                if service is None:
                    service = MT5AutonomousTradingService(adapter_for_account(profile.account_id))
                    self._services[profile.account_id] = service
            services[profile.account_id] = service
        return services

    async def start(self, *, owner: str | None = None) -> bool:
        services = self._enabled_services()
        if not services:
            logger.warning("MT5 multi-account autonomous scheduler not started: no enabled MT5 account profiles")
            return False
        if self._task and not self._task.done():
            return True
        scheduler_owner = owner or os.getenv("MT5_SCHEDULER_OWNER") or f"mt5:{socket.gethostname()}:{os.getpid()}"
        if not self.default_service._acquire_lock(scheduler_owner):
            logger.warning("MT5 multi-account autonomous scheduler not started: duplicate owner exists")
            return False
        for service in services.values():
            service._load_state()
            service.state.scheduler_owner = scheduler_owner
            service.state.scheduler_started_at = utcnow()
            service.state.next_cycle_time = _next_m5_run()
            service.state.current_state = "sleeping"
            asyncio.create_task(service.refresh_risk_metadata_health(), name=f"mt5-risk-metadata-startup-audit-{service.account_id}")
            asyncio.create_task(service._prewarm_market_data_cache(), name=f"mt5-redis-cache-prewarm-{service.account_id}")
        self._stop_event = asyncio.Event()
        self.state.scheduler_owner = scheduler_owner
        self.state.scheduler_started_at = utcnow()
        self.state.scheduler_running = True
        self.state.next_cycle_time = _next_m5_run()
        self.state.current_state = "sleeping"
        self._task = asyncio.create_task(self._loop(scheduler_owner), name="mt5-multi-account-autonomous-scheduler")
        logger.warning("MT5 multi-account autonomous scheduler started owner=%s accounts=%s next=%s", scheduler_owner, ",".join(services), self.state.next_cycle_time.isoformat())
        return True

    async def stop(self) -> None:
        if not self._task:
            return
        logger.warning("MT5 multi-account autonomous scheduler stopping")
        if self._stop_event:
            self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        self.state.scheduler_running = False
        self.state.current_state = "idle"
        for service in self._services.values():
            service.state.current_state = "idle"
            service._persist_state()
        self.default_service._release_lock()
        logger.warning("MT5 multi-account autonomous scheduler stopped")

    async def _loop(self, owner: str) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            self.default_service._heartbeat(owner)
            next_run = _next_m5_run()
            self.state.next_cycle_time = next_run
            self.state.current_state = "sleeping"
            for service in self._enabled_services().values():
                service.state.next_cycle_time = next_run
                service.state.current_state = "sleeping"
            logger.warning("MT5 multi-account scheduler sleeping until next completed M5 candle: %s", next_run.isoformat())
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=max(0, (next_run - utcnow()).total_seconds()))
                break
            except asyncio.TimeoutError:
                pass
            logger.warning("MT5 multi-account cycle started")
            try:
                result = await self.run_cycle(owner=owner)
                summary = result.get("summary") or {}
                logger.warning("MT5 multi-account cycle finished: %s accounts=%s order_send_calls=%s openai_calls=%s", result.get("cycle_id"), summary, result.get("order_send_calls"), result.get("openai_calls"))
            except Exception as exc:
                logger.exception("MT5 multi-account cycle failed: %s", exc.__class__.__name__)
                self.state.last_result = {"status": "ERROR", "error": exc.__class__.__name__, "cycle_id": self.state.current_cycle_id}

    async def run_cycle(self, *, owner: str = "local", dry_run: bool = False) -> dict[str, Any]:
        services = self._enabled_services()
        cycle_time = _completed_m5_time()
        base_cycle_id = f"MT5_M5_{cycle_time.strftime('%Y%m%d%H%M')}"
        self.state.current_cycle_id = base_cycle_id
        self.state.last_cycle_time = utcnow()
        self.state.current_state = "running"
        accounts: dict[str, dict[str, Any]] = {}
        for account_id, service in services.items():
            try:
                accounts[account_id] = await service.run_cycle(owner=owner, dry_run=dry_run, cycle_time=cycle_time)
            except Exception as exc:
                logger.exception("MT5 account cycle failed account_id=%s error=%s", account_id, exc.__class__.__name__)
                accounts[account_id] = {
                    "account_id": account_id,
                    "cycle_id": base_cycle_id if account_id == "demo_10k" else f"{account_id}:{base_cycle_id}",
                    "status": "ERROR",
                    "error": exc.__class__.__name__,
                    "order_send_calls": 0,
                    "openai_calls": _NO_OPENAI_CALLS,
                }
        summary = {account_id: result.get("status") for account_id, result in accounts.items()}
        result = {
            "cycle_id": base_cycle_id,
            "status": "MULTI_ACCOUNT_CYCLE",
            "accounts": accounts,
            "summary": summary,
            "evaluated_accounts": list(accounts),
            "order_send_calls": sum(int(result.get("order_send_calls") or 0) for result in accounts.values()),
            "openai_calls": sum(int(result.get("openai_calls") or 0) for result in accounts.values()),
            "created_at": utcnow().isoformat(),
        }
        self.state.last_result = result
        self.state.current_state = "sleeping" if self._task and not self._task.done() else "idle"
        return result

    def account_status(self, account_id: str) -> dict[str, Any] | None:
        service = self._enabled_services().get(account_id) or self._services.get(account_id)
        return service.status() if service else None

    def status(self) -> dict[str, Any]:
        services = self._enabled_services()
        accounts = {account_id: service.status() for account_id, service in services.items()}
        default_status = self.default_service.status()
        emergency_disabled = any(bool(status.get("emergency_disable")) for status in accounts.values()) if accounts else bool(default_status.get("emergency_disable"))
        return {
            **default_status,
            "scheduler_running": bool(self._task and not self._task.done()),
            "scheduler_owner": self.state.scheduler_owner,
            "scheduler_started_at": self.state.scheduler_started_at.isoformat() if self.state.scheduler_started_at else None,
            "last_cycle_time": self.state.last_cycle_time.isoformat() if self.state.last_cycle_time else None,
            "next_cycle_time": self.state.next_cycle_time.isoformat() if self.state.next_cycle_time else default_status.get("next_cycle_time"),
            "current_cycle_id": self.state.current_cycle_id,
            "current_state": self.state.current_state,
            "last_result": self.state.last_result or default_status.get("last_result"),
            "emergency_disable": emergency_disabled,
            "multi_account_enabled": True,
            "enabled_accounts": list(accounts),
            "accounts": accounts,
        }

    def _flatten(self, accessor: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for account_id, service in self._enabled_services().items():
            for row in getattr(service, accessor)():
                items.append({"account_id": row.get("account_id") or account_id, **row})
        return sorted(items, key=lambda row: str(row.get("created_at") or row.get("cycle_id") or ""), reverse=True)

    def cycles(self) -> list[dict[str, Any]]:
        return self._flatten("cycles")

    def decisions(self) -> list[dict[str, Any]]:
        return self._flatten("decisions")

    def trades(self) -> list[dict[str, Any]]:
        return self._flatten("trades")

    def candidates(self) -> list[dict[str, Any]]:
        return self._flatten("candidates")

    async def refresh_risk_metadata_health(self) -> dict[str, Any]:
        accounts = {account_id: await service.refresh_risk_metadata_health() for account_id, service in self._enabled_services().items()}
        return {"status": "ok" if all(row.get("status") == "ok" for row in accounts.values()) else "partial", "accounts": accounts}

    async def risk_status(self) -> dict[str, Any]:
        accounts = {account_id: await service.risk_status() for account_id, service in self._enabled_services().items()}
        return {"accounts": accounts, "default": accounts.get("demo_10k")}

    async def performance(self) -> dict[str, Any]:
        return await self.default_service.performance()

    async def reconciliation(self) -> dict[str, Any]:
        accounts = {account_id: await service.reconciliation() for account_id, service in self._enabled_services().items()}
        status = "MATCHED_EMPTY" if all(row.get("status") == "MATCHED_EMPTY" for row in accounts.values()) else "MATCHED_OPEN"
        if any(row.get("status") == "PROTECTION_MISMATCH" for row in accounts.values()):
            status = "PROTECTION_MISMATCH"
        return {"status": status, "accounts": accounts, "created_at": utcnow().isoformat()}

    async def emergency_disable(self) -> dict[str, Any]:
        accounts = {account_id: await service.emergency_disable() for account_id, service in self._enabled_services().items()}
        return {"emergency_disabled": True, "accounts": accounts}

    async def emergency_enable(self) -> dict[str, Any]:
        accounts = {account_id: await service.emergency_enable() for account_id, service in self._enabled_services().items()}
        enabled = all(not row.get("emergency_disabled") and row.get("enabled", True) for row in accounts.values())
        blockers = {account_id: row.get("blockers") for account_id, row in accounts.items() if row.get("blockers")}
        return {"emergency_disabled": not enabled, "enabled": enabled, "accounts": accounts, "blockers": blockers}


def _build_multi_strategy_analysis(
    instrument: MT5ForexInstrument, cycle_id: str,
    m15_rows: list[dict[str, Any]], h1_rows: list[dict[str, Any]], h4_rows: list[dict[str, Any]],
    bid: Any, ask: Any, spread: Any, regime_info: dict[str, Any] | None,
    *, m15_snapshot: Any = None, h1_snapshot: Any = None, h4_snapshot: Any = None,
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
        m15_snapshot=m15_snapshot, h1_snapshot=h1_snapshot, h4_snapshot=h4_snapshot,
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


_MTFAI1_STRUCTURE_CONFIG = MarketStructureConfig()

# 2026-08-18: user-requested -- mtfai1's own direction call is pure moving-average alignment
# (fast/slow SMA crossover on M15, confirmed by H1/H4 close-vs-average); it never looked at real
# swing-point structure (higher-high/higher-low vs lower-high/lower-low), unlike smc_continuation/
# breakout/etc which already go through the full market_structure engine. This adds a lightweight
# swing-trend confirmation -- detect_swings()+classify_trend() only, NOT the full analyze_bars()
# pipeline (BOS/CHOCH/liquidity/dealing-range) mtfai1 was deliberately built to skip for
# performance (see the "never MTFAI1's own trend/SMA" comment above). Fails OPEN (does not block
# mtfai1) on any error or on RANGING/TRANSITIONAL/UNKNOWN/insufficient-swings states -- only an
# outright DISAGREEMENT (bearish structure under a LONG SMA signal, or vice versa) blocks the
# trade. Reversible via MT5_MTFAI1_TREND_STRUCTURE_CONFIRMATION_REQUIRED (default true).
MT5_MTFAI1_TREND_STRUCTURE_CONFIRMATION_REQUIRED = os.getenv("MT5_MTFAI1_TREND_STRUCTURE_CONFIRMATION_REQUIRED", "true").strip().lower() not in {"false", "0", "off", "no"}


def _mtfai1_trend_structure_agrees(m15: list[Any], direction: str, symbol: str) -> bool:
    if not MT5_MTFAI1_TREND_STRUCTURE_CONFIRMATION_REQUIRED or direction not in {"LONG", "SHORT"}:
        return True
    try:
        rows = [row.model_dump(mode="json") if hasattr(row, "model_dump") else row for row in m15]
        bars = normalize_bars(rows, symbol=symbol, timeframe="M15")
        swings = detect_swings(bars, _MTFAI1_STRUCTURE_CONFIG, symbol=symbol, timeframe="M15")
        trend = classify_trend(bars, swings, _MTFAI1_STRUCTURE_CONFIG, symbol=symbol, timeframe="M15")
    except Exception as exc:
        logger.warning("MT5 mtfai1 trend-structure check failed for %s (fails open, mtfai1 unaffected): %s", symbol, exc.__class__.__name__)
        return True
    state = trend.state.value if hasattr(trend.state, "value") else trend.state
    if direction == "LONG" and state == TrendLabel.BEARISH.value:
        return False
    if direction == "SHORT" and state == TrendLabel.BULLISH.value:
        return False
    return True


# 2026-08-18: user-requested follow-up to the trend-structure confirmation above -- equal-highs/
# equal-lows (EQH/EQL) liquidity pools, same ICT/SMC concept liquidity_sweep_reversal already
# trades off of (see detect_equal_levels' docstring). Applied here as a proximity check, not a
# sweep-and-displacement entry trigger like that strategy (mtfai1 is a trend-following model, not
# a reversal one): an UNSWEPT equal-level sitting immediately ahead of price in the trade's own
# direction is real, concrete resistance/support the trade would have to fight through right at
# entry -- almost always where price gets drawn to and often reverses, since that's the whole
# point of a liquidity pool. Blocks only when one sits within MT5_MTFAI1_EQUAL_LEVEL_MIN_ATR_MULT
# (default 0.5) ATRs of entry; fails open (does not block) on any error, no swings, or no equal
# levels detected -- same posture as the trend-structure check. Reversible via
# MT5_MTFAI1_EQUAL_LEVEL_CONFIRMATION_REQUIRED (default true).
MT5_MTFAI1_EQUAL_LEVEL_CONFIRMATION_REQUIRED = os.getenv("MT5_MTFAI1_EQUAL_LEVEL_CONFIRMATION_REQUIRED", "true").strip().lower() not in {"false", "0", "off", "no"}


def _mtfai1_equal_level_clear(m15: list[Any], direction: str, symbol: str, entry: Decimal, atr: Decimal) -> bool:
    if not MT5_MTFAI1_EQUAL_LEVEL_CONFIRMATION_REQUIRED or direction not in {"LONG", "SHORT"} or atr <= 0:
        return True
    try:
        rows = [row.model_dump(mode="json") if hasattr(row, "model_dump") else row for row in m15]
        bars = normalize_bars(rows, symbol=symbol, timeframe="M15")
        swings = detect_swings(bars, _MTFAI1_STRUCTURE_CONFIG, symbol=symbol, timeframe="M15")
        levels = detect_equal_levels(bars, swings, _MTFAI1_STRUCTURE_CONFIG, symbol=symbol, timeframe="M15")
    except Exception as exc:
        logger.warning("MT5 mtfai1 equal-level check failed for %s (fails open, mtfai1 unaffected): %s", symbol, exc.__class__.__name__)
        return True
    # LONG runs into resistance at an equal-HIGH (BUY_SIDE liquidity, per detect_equal_levels'
    # own swing-high->BUY_SIDE convention) above entry; SHORT runs into support at an equal-LOW
    # (SELL_SIDE) below entry.
    ahead_side = LiquiditySide.BUY_SIDE if direction == "LONG" else LiquiditySide.SELL_SIDE
    min_mult = Decimal(str(_env_float("MT5_MTFAI1_EQUAL_LEVEL_MIN_ATR_MULT", 0.5)))
    for level in levels:
        if level.side != ahead_side:
            continue
        price = Decimal(str(level.level))
        ahead = (price > entry) if direction == "LONG" else (price < entry)
        if ahead and abs(price - entry) < atr * min_mult:
            return False
    return True


def _score_candidate(quote: Any, m15: list[Any], h1: list[Any], h4: list[Any], symbol_info: Any = None) -> tuple[float, str, dict[str, str]]:
    closes = [Decimal(str(c.close)) for c in m15[-50:]]
    h1_close = Decimal(str(h1[-1].close))
    h1_avg = sum(Decimal(str(c.close)) for c in h1[-20:]) / Decimal("20")
    h4_close = Decimal(str(h4[-1].close))
    h4_avg = sum(Decimal(str(c.close)) for c in h4[-20:]) / Decimal("20")
    fast = sum(closes[-10:]) / Decimal("10")
    slow = sum(closes[-30:]) / Decimal("30")
    direction = "LONG" if fast > slow and h1_close > h1_avg and h4_close > h4_avg else "SHORT" if fast < slow and h1_close < h1_avg and h4_close < h4_avg else "NO_TRADE"
    symbol_label = symbol_info if isinstance(symbol_info, str) else getattr(symbol_info, "name", "UNKNOWN")
    if direction != "NO_TRADE" and not _mtfai1_trend_structure_agrees(m15, direction, symbol_label):
        direction = "NO_TRADE"
    entry = Decimal(str(quote.ask if direction == "LONG" else quote.bid or closes[-1]))
    atr = sum(abs(Decimal(str(c.high)) - Decimal(str(c.low))) for c in m15[-14:]) / Decimal("14")
    if direction != "NO_TRADE" and atr > 0 and not _mtfai1_equal_level_clear(m15, direction, symbol_label, entry, atr):
        direction = "NO_TRADE"
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


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
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
    "wyckoff": "wyckoff",
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


mt5_default_autonomous_service = MT5AutonomousTradingService()
mt5_autonomous_service = MT5MultiAccountAutonomousOrchestrator(mt5_default_autonomous_service)
