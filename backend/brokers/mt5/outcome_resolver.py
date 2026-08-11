"""Confidence Validation & Calibration layer -- Parts 2 & 3.

Background service that resolves outcomes for MT5CandidateEvaluationORM rows written by
backend/brokers/mt5/candidate_evaluation.py, strictly after the fact:

  - Shadow tracking (Part 2): for candidates that were never submitted as a broker order
    (outcome_type == "SHADOW"), replay the SAME live market data source the trading engine
    itself uses (MT5Adapter.candles()) forward from the candidate's decision timestamp to
    determine whether the proposed take-profit or stop-loss would have been touched, and
    compute MFE/MAE/hypothetical R. This resolver never mutates the broker in any way -- it is
    a pure read of historical/live candle data (MT5Adapter.candles() only).

  - Executed-trade linking (Part 3): for candidates that WERE submitted and accepted
    (outcome_type == "EXECUTED", broker_ticket set), once adaptive trade management has
    detected the position closed (AdaptivePositionStateORM.closed_detected_at) and the trade
    ledger has the realized figures (MT5TradeRecordORM), copy the already-computed MFE/MAE/R/
    exit data across. Nothing here is recomputed independently of adaptive management --
    signal quality (this table) stays analytically separate from execution/management quality
    (AdaptivePositionStateORM/MT5TradeRecordORM), per the calibration-layer brief.

Runs on its own periodic loop (default 5 minutes), following the same start()/stop()/_task
convention as MT5AutonomousTradingService. Entirely read-only against the broker.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.adaptive_management.orm import AdaptivePositionStateORM
from backend.brokers.mt5.adapter import MT5Adapter, mt5_adapter
from backend.brokers.mt5.candidate_evaluation import pending_executed_candidates, pending_shadow_candidates, record_executed_outcome, record_shadow_outcome
from backend.brokers.mt5.orm import MT5TradeRecordORM
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

RESOLUTION_TIMEFRAME = "M5"
POLL_INTERVAL_SECONDS = 300


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CandidateOutcomeResolver:
    def __init__(self, adapter: MT5Adapter | None = None) -> None:
        self.adapter = adapter or mt5_adapter
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> bool:
        if self._task and not self._task.done():
            return True
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="mt5-candidate-outcome-resolver")
        logger.warning("MT5 candidate outcome resolver started")
        return True

    async def stop(self) -> None:
        if not self._task:
            return
        assert self._stop_event is not None
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.warning("MT5 candidate outcome resolver stopped")

    async def _loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                logger.exception("MT5 candidate outcome resolution cycle failed: %s", exc.__class__.__name__)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> dict[str, int]:
        shadow_resolved = await self._resolve_shadow_candidates()
        executed_linked = self._link_executed_outcomes()
        return {"shadow_resolved": shadow_resolved, "executed_linked": executed_linked}

    async def _resolve_shadow_candidates(self) -> int:
        resolved = 0
        for candidate in pending_shadow_candidates():
            try:
                outcome = await self._evaluate_shadow_candidate(candidate)
            except Exception as exc:
                logger.warning("MT5 shadow outcome evaluation failed for %s: %s", candidate.get("candidate_id"), exc.__class__.__name__)
                continue
            if outcome is None:
                continue
            record_shadow_outcome(candidate["candidate_id"], **outcome)
            resolved += 1
        return resolved

    async def _evaluate_shadow_candidate(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        entry = candidate.get("proposed_entry")
        stop_loss = candidate.get("proposed_stop_loss")
        take_profit = candidate.get("proposed_take_profit")
        created_at = candidate.get("created_at")
        expiry_at = candidate.get("expiry_at")
        direction = candidate.get("direction")
        if entry is None or stop_loss is None or take_profit is None or created_at is None:
            return None
        risk_per_unit = abs(float(entry) - float(stop_loss))
        if risk_per_unit <= 0:
            return None
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        elapsed = utcnow() - created_at
        bars_needed = min(2000, max(20, int(elapsed.total_seconds() // 300) + 10))
        candles = await self.adapter.candles(candidate["broker_symbol"], RESOLUTION_TIMEFRAME, count=bars_needed, completed_only=True)
        relevant = sorted((c for c in candles if c.time >= created_at), key=lambda c: c.time)
        if not relevant:
            return None

        long = direction == "LONG"
        max_favorable = 0.0
        max_adverse = 0.0
        mfe_time: datetime | None = None
        resolution: dict[str, Any] | None = None
        for candle in relevant:
            high, low, close = float(candle.high), float(candle.low), float(candle.close)
            favorable = (high - float(entry)) if long else (float(entry) - low)
            adverse = (float(entry) - low) if long else (high - float(entry))
            if favorable > max_favorable:
                max_favorable = favorable
                mfe_time = candle.time
            max_adverse = max(max_adverse, adverse)

            sl_touched = (low <= float(stop_loss)) if long else (high >= float(stop_loss))
            tp_touched = (high >= float(take_profit)) if long else (low <= float(take_profit))
            if sl_touched or tp_touched:
                # Intrabar order is unknown from OHLC alone -- when both levels fall inside the
                # same candle, conservatively assume the stop was hit first (never assume the
                # more favorable outcome when the data can't actually distinguish it).
                if sl_touched:
                    resolution = {
                        "outcome_status": "SL_HIT",
                        "tp_hit": False,
                        "sl_hit": True,
                        "hypothetical_r": -1.0,
                        "time_to_sl_seconds": (candle.time - created_at).total_seconds(),
                    }
                else:
                    reward_risk = float(candidate.get("initial_reward_risk") or ((float(take_profit) - float(entry)) / risk_per_unit if long else (float(entry) - float(take_profit)) / risk_per_unit))
                    resolution = {
                        "outcome_status": "TP_HIT",
                        "tp_hit": True,
                        "sl_hit": False,
                        "hypothetical_r": float(reward_risk),
                        "time_to_tp_seconds": (candle.time - created_at).total_seconds(),
                    }
                break

        base = {
            "mfe_r": round(max_favorable / risk_per_unit, 4),
            "mae_r": round(max_adverse / risk_per_unit, 4),
            "time_to_mfe_seconds": (mfe_time - created_at).total_seconds() if mfe_time else None,
        }
        if resolution is not None:
            base.update(resolution)
            base["outcome_resolved_at"] = utcnow()
            return base

        if expiry_at is not None:
            if expiry_at.tzinfo is None:
                expiry_at = expiry_at.replace(tzinfo=timezone.utc)
            if utcnow() >= expiry_at:
                last_close = float(relevant[-1].close)
                mark_to_market_r = ((last_close - float(entry)) / risk_per_unit) * (1.0 if long else -1.0)
                base.update({"outcome_status": "EXPIRED_NO_TOUCH", "tp_hit": False, "sl_hit": False, "hypothetical_r": round(mark_to_market_r, 4), "outcome_resolved_at": utcnow()})
                return base
        return None  # Still pending -- neither level touched, not yet expired.

    def _link_executed_outcomes(self) -> int:
        linked = 0
        for candidate in pending_executed_candidates():
            broker_ticket = candidate.get("broker_ticket")
            if not broker_ticket:
                continue
            outcome = self._executed_outcome_from_management_records(broker_ticket)
            if outcome is None:
                continue
            record_executed_outcome(candidate["candidate_id"], **outcome)
            linked += 1
        return linked

    def _executed_outcome_from_management_records(self, broker_ticket: str) -> dict[str, Any] | None:
        with SessionLocal() as db:
            position = db.query(AdaptivePositionStateORM).filter(AdaptivePositionStateORM.broker_ticket == broker_ticket).first()
            if position is None or position.closed_detected_at is None:
                return None  # Still open, or adaptive management hasn't adopted/detected it yet.
            trade_record = db.query(MT5TradeRecordORM).filter(MT5TradeRecordORM.order_ticket == broker_ticket).first()

            realized_pnl = float(trade_record.realized_pnl) if trade_record and trade_record.realized_pnl is not None else None
            original_risk = float(position.original_risk_money) if position.original_risk_money else None
            realized_r = (realized_pnl / abs(original_risk)) if (realized_pnl is not None and original_risk) else None
            holding_seconds = None
            if trade_record and trade_record.open_timestamp and trade_record.close_timestamp:
                holding_seconds = (trade_record.close_timestamp - trade_record.open_timestamp).total_seconds()
            elif position.opened_at and position.closed_detected_at:
                holding_seconds = (position.closed_detected_at - position.opened_at).total_seconds()

            return {
                "outcome_status": "CLOSED",
                "mfe_r": float(position.max_achieved_r) if position.max_achieved_r is not None else None,
                "mae_r": float(position.min_achieved_r) if position.min_achieved_r is not None else None,
                "realized_r": round(realized_r, 4) if realized_r is not None else None,
                "realized_pnl": realized_pnl,
                "holding_duration_seconds": holding_seconds,
                "exit_reason": (trade_record.exit_reason if trade_record else None) or position.winner_classification,
                "actual_entry": float(position.entry_price) if position.entry_price else None,
                "slippage": None,
                "final_exit_price": None,
                "outcome_resolved_at": utcnow(),
                "outcome_payload": {"winner_classification": position.winner_classification, "r_source": position.r_source},
            }


candidate_outcome_resolver = CandidateOutcomeResolver()
