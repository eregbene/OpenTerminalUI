from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import socket
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from backend.adaptive_management.tp_protection import classify_stop_quality_v2, construct_dynamic_stop
from backend.ai_provider import ProviderRequest, provider_registry
from backend.brokers.mt5.adapter import MT5Adapter, mt5_adapter
from backend.brokers.mt5.config import MT5Config
from backend.brokers.mt5.config import mt5_config
from backend.brokers.mt5.execution import MT5ExecutionService, _round_down
from backend.brokers.mt5.models import MT5ForexInstrument, MT5TradeIntent
from backend.brokers.mt5.persistence import learning_context_for_candidate, persist_cycle_result, trade_performance_summary, update_trade_history, update_trade_reconciliation
from backend.brokers.mt5.prop_risk import risk_status
from backend.brokers.mt5.risk_budget import effective_risk_budget_usd
from backend.brokers.mt5.take_profit import MIN_REWARD_MULTIPLE, select_take_profit
from backend.decision_context.service import decision_context_service
from backend.economic_intelligence.service import economic_intelligence_service
from backend.intelligence.trading.config import ai_trading_config
from backend.intelligence.trading.persistence import get_state, set_state, utcnow
from backend.intelligence.trading.usage import usage_ledger
from backend.portfolio_execution.service import portfolio_manager

logger = logging.getLogger(__name__)


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
            if not dry_run and cycle_id in set(_stored().get("processed_candles") or []):
                result = {"cycle_id": cycle_id, "status": "SKIPPED_DUPLICATE_CANDLE", "openai_calls": 0, "order_send_calls": 0}
                self._record_cycle(result, dry_run=dry_run)
                return result
            blockers = await self._global_blockers()
            if blockers:
                result = {"cycle_id": cycle_id, "status": "SKIPPED_BLOCKED", "blockers": blockers, "openai_calls": 0, "order_send_calls": 0}
                self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                return result
            universe = await self.adapter.forex_universe()
            candidates = await self._screen(universe.items)
            eligible = [row for row in candidates if not row["rejection_reasons"]]
            if not eligible:
                result = {"cycle_id": cycle_id, "status": "SKIPPED_NO_CANDIDATE", "symbols_discovered": universe.total_forex_pairs, "eligible_symbols": len([i for i in universe.items if i.eligible]), "candidates": candidates[:25], "openai_calls": 0, "order_send_calls": 0}
                self._record_cycle(result, dry_run=dry_run)
                if not dry_run:
                    usage_ledger.record_skip(len(candidates))
                return result
            best = sorted(eligible, key=lambda row: row["ranking_score"], reverse=True)[0]
            best["candidate_id"] = f"{cycle_id}:{best['broker_symbol']}:{best['context_hash'][:16]}"
            context_risk = await decision_context_service.context_risk(best["canonical_pair"])
            best["decision_context"] = context_risk
            context_blockers = self._context_blockers(context_risk)
            try:
                economic_result = await economic_intelligence_service.evaluate_entry(canonical_pair=best["canonical_pair"], direction=best["direction"], candidate_id=best["candidate_id"], spread=float(quote_spread) if (quote_spread := best["context"].get("spread")) else None)
            except Exception as exc:
                logger.warning("Economic intelligence evaluation failed (Forex Factory outage must not block the cycle): %s", exc.__class__.__name__)
                economic_result = {"guard": {"decision": "ALLOW", "reason_codes": [f"ECONOMIC_INTELLIGENCE_UNAVAILABLE:{exc.__class__.__name__}"], "size_multiplier": 1.0}, "calendar": None, "news": None, "macro_advisory": None}
            best["economic_context"] = economic_result
            economic_blockers = self._economic_blockers(economic_result)
            if context_blockers:
                result = {"cycle_id": cycle_id, "status": "SKIPPED_CONTEXT_RISK", "winner": best, "context_risk": context_risk, "blockers": context_blockers, "openai_calls": 0, "order_send_calls": 0}
                self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                return result
            if economic_blockers:
                result = {"cycle_id": cycle_id, "status": "SKIPPED_ECONOMIC_RISK", "winner": best, "economic_context": economic_result, "blockers": economic_blockers, "openai_calls": 0, "order_send_calls": 0}
                self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                return result
            can_request, reasons = usage_ledger.can_request(ai_trading_config(), best["context_hash"])
            if not can_request:
                result = {"cycle_id": cycle_id, "status": "SKIPPED_PROVIDER_LIMIT", "blockers": reasons, "winner": best, "openai_calls": 0, "order_send_calls": 0}
                self._record_cycle(result, dry_run=dry_run)
                return result
            # Reward:risk is a pure price-geometry ratio, independent of the AI's confidence
            # output, so it can be re-verified against a FRESH quote before spending a paid AI
            # call. stop_loss/take_profit were fixed at screening time against an earlier quote;
            # by the time _submit() re-fetches the entry price, price may have drifted enough
            # that the same fixed SL/TP no longer clear MIN_REWARD_MULTIPLE -- which would
            # otherwise only be discovered by calculate_risk_size() AFTER the AI call already ran.
            try:
                fresh_quote = await self.adapter.latest_tick(best["broker_symbol"])
                fresh_entry = fresh_quote.ask if best["direction"] == "LONG" else fresh_quote.bid
                if fresh_entry is not None:
                    stale_stop_distance = abs(fresh_entry - Decimal(str(best["stop_loss"])))
                    stale_target_distance = abs(Decimal(str(best["take_profit"])) - fresh_entry)
                    if stale_stop_distance > 0 and (stale_target_distance / stale_stop_distance) < MIN_REWARD_MULTIPLE:
                        result = {"cycle_id": cycle_id, "status": "SKIPPED_STALE_RISK_REWARD", "winner": best, "blockers": ["RISK_REWARD_DEGRADED_SINCE_SCREENING"], "openai_calls": 0, "order_send_calls": 0}
                        self._record_cycle(result, mark_processed=False, dry_run=dry_run)
                        return result
            except Exception as exc:
                logger.warning("Reward:risk pre-check failed (non-fatal, proceeding to AI decision): %s", exc.__class__.__name__)
            decision = await self._ai_decision(best)
            if decision["decision"] == "NO_TRADE":
                result = {"cycle_id": cycle_id, "status": "NO_TRADE", "winner": best, "ai_decision": decision, "openai_calls": 1, "order_send_calls": 0}
                self._record_cycle(result, dry_run=dry_run)
                return result
            if decision["decision"] != best["direction"] or Decimal(str(decision["confidence"])) < Decimal(str(ai_trading_config().min_confidence)):
                result = {"cycle_id": cycle_id, "status": "AI_REJECTED", "winner": best, "ai_decision": decision, "openai_calls": 1, "order_send_calls": 0}
                self._record_cycle(result, dry_run=dry_run)
                return result
            submission = await self._submit(best, ai_confidence=float(decision.get("confidence") or 0.0), dry_run=dry_run)
            result = {"cycle_id": cycle_id, "status": submission["status"], "winner": best, "ai_decision": decision, "trade": submission, "openai_calls": 1, "order_send_calls": submission.get("order_send_calls", 0)}
            self._record_cycle(result, dry_run=dry_run)
            return result

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

    async def _screen(self, instruments: list[MT5ForexInstrument]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        account = await self.adapter.mt5_account()
        open_positions = await self.adapter.mt5_positions()
        open_symbols = {p.symbol.upper() for p in open_positions if p.volume != 0}
        for instrument in instruments:
            reasons = list(instrument.ineligibility_reasons)
            if not instrument.eligible:
                reasons.append("SYMBOL_INELIGIBLE")
            if instrument.broker_symbol.upper() in open_symbols:
                reasons.append("EXISTING_POSITION")
            try:
                quote = await self.adapter.latest_tick(instrument.broker_symbol)
                m15 = await self.adapter.candles(instrument.broker_symbol, "M15", count=100)
                h1 = await self.adapter.candles(instrument.broker_symbol, "H1", count=100)
                h4 = await self.adapter.candles(instrument.broker_symbol, "H4", count=100)
            except Exception as exc:
                rows.append(_candidate(instrument, reasons + [f"DATA_UNAVAILABLE:{exc.__class__.__name__}"]))
                continue
            if len(m15) < 60 or len(h1) < 50 or len(h4) < 30:
                reasons.append("HISTORY_UNAVAILABLE")
                rows.append(_candidate(instrument, reasons))
                continue
            if quote.bid is None or quote.ask is None:
                reasons.append("NO_QUOTE")
                rows.append(_candidate(instrument, reasons))
                continue
            score, direction, geometry = _score_candidate(quote, m15, h1, h4)
            if score < 70:
                reasons.append("WEAK_CONSENSUS")
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
                **geometry,
            }
            rows.append({**_candidate(instrument, reasons), "direction": direction, "ranking_score": score, "context": context, "context_hash": _hash(context), **geometry})
        return sorted(rows, key=lambda row: row["ranking_score"], reverse=True)

    async def _ai_decision(self, candidate: dict[str, Any]) -> dict[str, Any]:
        context = dict(candidate["context"])
        if candidate.get("decision_context"):
            context["decision_context"] = candidate["decision_context"]
        if candidate.get("economic_context"):
            context["economic_context"] = candidate["economic_context"]
        try:
            context["trade_memory"] = learning_context_for_candidate(candidate)
        except Exception as exc:
            logger.warning("MT5 trade memory context unavailable: %s", exc.__class__.__name__)
        prompt = "Return strict JSON with decision LONG, SHORT, or NO_TRADE and confidence 0-1 for this MT5 demo forex setup. Use decision_context news, macro, and calendar warnings as risk context, not as automatic rejection. " + json.dumps(context, sort_keys=True)
        response = await provider_registry.get("openai").complete(
            ProviderRequest(prompt=prompt, model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"), max_tokens=self.config.max_output_tokens, temperature=0, timeout_seconds=30, idempotency_key=candidate["context_hash"])
        )
        usage_ledger.record_request(context_hash=candidate["context_hash"], input_tokens=response.prompt_tokens, output_tokens=response.completion_tokens, estimated_cost_usd=float(response.estimated_cost or 0))
        parsed = _parse_decision(response.text)
        decision = {"decision_id": f"MT5AI_{candidate.get('candidate_id') or candidate['context_hash'][:24]}", "decision": parsed[0], "confidence": parsed[1], "model": os.getenv("OPENAI_MODEL", "gpt-4.1-mini"), "raw": response.text[:500], "input_tokens": response.prompt_tokens, "output_tokens": response.completion_tokens, "estimated_cost": response.estimated_cost}
        self.state.decisions.insert(0, decision | {"candidate": candidate["canonical_pair"], "created_at": utcnow().isoformat()})
        return decision

    def _risk_budget_adjustment(self, account: Any, economic_context: dict[str, Any], ai_confidence: float | None) -> dict[str, Any]:
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
            "strategy_confidence": ai_confidence,
        }

    async def _submit(self, candidate: dict[str, Any], *, ai_confidence: float | None = None, dry_run: bool = False) -> dict[str, Any]:
        account = await self.adapter.mt5_account()
        symbol = await self.adapter.symbol_info(candidate["broker_symbol"])
        quote = await self.adapter.latest_tick(candidate["broker_symbol"])
        entry = quote.ask if candidate["direction"] == "LONG" else quote.bid
        if entry is None:
            return {"status": "REJECTED", "reasons": ["NO_ENTRY_PRICE"], "order_send_calls": 0}
        economic_context_preview = candidate.get("economic_context") or {}
        risk_adjustment = self._risk_budget_adjustment(account, economic_context_preview, ai_confidence)
        base_risk_budget = (account.equity * Decimal(str(self.config.risk_percent_per_trade)) / Decimal("100")).quantize(Decimal("0.01"))
        _, risk_adjustment_detail = effective_risk_budget_usd(base_risk_budget, **risk_adjustment)
        risk = await self.execution.calculate_risk_size(account_equity=account.equity, symbol=symbol, direction=candidate["direction"], entry=entry, stop=Decimal(str(candidate["stop_loss"])), target=Decimal(str(candidate["take_profit"])), risk_budget_adjustment=risk_adjustment_detail)
        if risk.status != "APPROVED":
            return {"status": "RISK_REJECTED", "risk": risk.model_dump(mode="json"), "risk_budget_adjustment": risk_adjustment_detail, "order_send_calls": 0}
        economic_context = candidate.get("economic_context") or {}
        size_multiplier = float(((economic_context.get("guard") or {}).get("size_multiplier")) or 1.0)
        if size_multiplier < 1.0:
            step = symbol.volume_step or Decimal("0.01")
            minimum = symbol.volume_min or Decimal("0.01")
            scaled = _round_down(risk.volume * Decimal(str(size_multiplier)), step)
            if scaled >= minimum:
                risk.volume = scaled
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
            comment=_mt5_order_comment(candidate["canonical_pair"], now),
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

    def status(self) -> dict[str, Any]:
        store = _stored()
        usage = usage_ledger.status(ai_trading_config(), {})
        return {
            "scheduler_running": bool(self._task and not self._task.done()),
            "scheduler_owner": self.state.scheduler_owner,
            "scheduler_started_at": self.state.scheduler_started_at.isoformat() if self.state.scheduler_started_at else None,
            "last_cycle_time": self.state.last_cycle_time.isoformat() if self.state.last_cycle_time else None,
            "next_cycle_time": self.state.next_cycle_time.isoformat() if self.state.next_cycle_time else None,
            "current_cycle_id": self.state.current_cycle_id,
            "current_state": self.state.current_state,
            "last_result": store.get("last_result"),
            "provider_calls_today": usage["requests_today"],
            "estimated_cost_today": usage["estimated_cost_today"],
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


def _score_candidate(quote: Any, m15: list[Any], h1: list[Any], h4: list[Any]) -> tuple[float, str, dict[str, str]]:
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
    stop_price = construct_dynamic_stop(
        direction,
        float(entry),
        float(structure_level) if structure_level is not None else None,
        float(atr),
        float(spread),
        min_atr_mult=_env_float("MT5_AUTO_SL_MIN_ATR_MULT", 1.0),
        max_atr_mult=_env_float("MT5_AUTO_SL_MAX_ATR_MULT", 3.0),
        min_spread_ratio=_env_float("MT5_AUTO_SL_MIN_SPREAD_RATIO", 3.0),
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


def _parse_decision(text: str) -> tuple[str, float]:
    try:
        data = json.loads(_json_payload(text))
        decision = str(data.get("decision") or "NO_TRADE").upper()
        confidence = float(data.get("confidence") or 0)
        return (decision if decision in {"LONG", "SHORT", "NO_TRADE"} else "NO_TRADE", confidence)
    except Exception:
        return "NO_TRADE", 0.0


def _mt5_order_comment(pair: str, timestamp: datetime, *, strategy_code: str = "MTFAI1", timeframe: str = "M15") -> str:
    symbol = "".join(ch for ch in pair.upper() if ch.isalnum())[:6] or "FX"
    return f"BSM|{strategy_code}|{timeframe}|{symbol}{timestamp.strftime('%H%M')}"[:31]


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _json_payload(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


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
