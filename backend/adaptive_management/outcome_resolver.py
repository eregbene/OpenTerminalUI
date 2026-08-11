"""Adaptive Trade Manager Validation & Performance Analytics layer -- Parts 8 & 13.

Background service that resolves three analytics-only counterfactuals per position, strictly
after the fact, using the SAME live/historical MT5 candle source
(backend.brokers.mt5.adapter.mt5_adapter.candles()) the trading/management engine itself uses.
Entirely read-only against the broker -- never mutates a position in any way, and nothing
computed here is ever read back into AdaptiveManagementService._monitor_cycle.

  - Original SL/TP baseline (Part 13): from entry forward, what would have happened if the
    ORIGINAL stop/target had simply been left in place, ignoring every management action.
  - No-BE baseline (Part 13): for positions where break-even was actually confirmed activated
    (detected from the AdaptiveManagementEventORM sl_before/sl_after time series -- not merely
    "attempted", but observed to have taken effect on the broker-synced state), what would have
    happened afterward using the pre-BE stop instead.
  - Post-exit shadow tracking (Part 8): from the manager's actual exit price/time forward, did
    price reach the original TP, move +1R beyond the exit, reverse strongly, or reach the
    original SL -- an evidence signal for possible early exits, never a verdict.

Ambiguous same-candle TP/SL ordering always resolves to the more conservative outcome (the
stop, never the target), matching backend/brokers/mt5/outcome_resolver.py's rule.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.adaptive_management.orm import AdaptiveManagementEventORM, AdaptiveManagerCounterfactualORM, AdaptivePositionBaselineORM, AdaptivePositionStateORM, AdaptiveTradeEventORM
from backend.brokers.mt5.adapter import MT5Adapter, mt5_adapter
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

RESOLUTION_TIMEFRAME = "M5"
POLL_INTERVAL_SECONDS = 300
ORIGINAL_SLTP_WINDOW = timedelta(days=10)
POST_EXIT_WINDOW = timedelta(days=3)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class AdaptiveManagerOutcomeResolver:
    def __init__(self, adapter: MT5Adapter | None = None) -> None:
        self.adapter = adapter or mt5_adapter
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> bool:
        if self._task and not self._task.done():
            return True
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="adaptive-manager-outcome-resolver")
        logger.warning("Adaptive manager outcome resolver started")
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
        logger.warning("Adaptive manager outcome resolver stopped")

    async def _loop(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                logger.exception("Adaptive manager counterfactual resolution cycle failed: %s", exc.__class__.__name__)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> dict[str, int]:
        original = await self._resolve_original_sltp_baselines()
        no_be = await self._resolve_no_be_baselines()
        post_exit = await self._resolve_post_exit_shadows()
        return {"original_sltp_resolved": original, "no_be_resolved": no_be, "post_exit_resolved": post_exit}

    # ------------------------------------------------------------------ shared candle scan ---
    async def _scan_candles(self, *, symbol: str, since: datetime, window: timedelta) -> list[Any]:
        elapsed = utcnow() - since
        bars_needed = min(3000, max(20, int(min(elapsed, window).total_seconds() // 300) + 10))
        candles = await self.adapter.candles(symbol, RESOLUTION_TIMEFRAME, count=bars_needed, completed_only=True)
        return sorted((c for c in candles if _aware(c.time) and _aware(c.time) >= since), key=lambda c: c.time)

    @staticmethod
    def _first_touch(candles: list[Any], *, long: bool, sl: float, tp: float) -> tuple[str | None, Any]:
        """Returns (outcome, candle) where outcome is 'SL' | 'TP' | None. Same-candle ambiguity
        always resolves to 'SL' -- the conservative rule, never the favorable one."""
        for candle in candles:
            high, low = float(candle.high), float(candle.low)
            sl_touched = (low <= sl) if long else (high >= sl)
            tp_touched = (high >= tp) if long else (low <= tp)
            if sl_touched:
                return "SL", candle
            if tp_touched:
                return "TP", candle
        return None, None

    # --------------------------------------------------------------- Part 13: original SL/TP ---
    async def _resolve_original_sltp_baselines(self) -> int:
        resolved = 0
        with SessionLocal() as db:
            candidates = (
                db.query(AdaptivePositionBaselineORM, AdaptivePositionStateORM)
                .join(AdaptivePositionStateORM, AdaptivePositionStateORM.position_id == AdaptivePositionBaselineORM.position_id)
                .all()
            )
            pending = []
            for baseline, state in candidates:
                cf = db.get(AdaptiveManagerCounterfactualORM, baseline.position_id)
                if cf is None:
                    cf = AdaptiveManagerCounterfactualORM(position_id=baseline.position_id, symbol=baseline.symbol)
                    db.add(cf)
                    db.commit()
                if cf.original_sltp_outcome == "PENDING":
                    pending.append((baseline, state, cf))
            targets = [(b.position_id, b.symbol, b.direction, b.original_entry, b.original_sl, b.original_tp, b.initial_stop_distance, b.initial_reward_risk, state.opened_at) for b, state, _cf in pending]

        for position_id, symbol, direction, entry, sl, tp, stop_distance, reward_risk, opened_at in targets:
            opened_at = _aware(opened_at)
            if entry is None or sl is None or tp is None or opened_at is None or not stop_distance:
                continue
            try:
                candles = await self._scan_candles(symbol=symbol, since=opened_at, window=ORIGINAL_SLTP_WINDOW)
            except Exception as exc:
                logger.warning("Adaptive counterfactual: candle fetch failed for %s: %s", symbol, exc.__class__.__name__)
                continue
            if not candles:
                continue
            long = direction == "LONG"
            outcome, _candle = self._first_touch(candles, long=long, sl=float(sl), tp=float(tp))
            if outcome == "SL":
                result, r = "ORIGINAL_SL_FIRST", -1.0
            elif outcome == "TP":
                result, r = "ORIGINAL_TP_FIRST", float(reward_risk) if reward_risk else round(abs(float(tp) - float(entry)) / stop_distance, 4)
            elif utcnow() - opened_at >= ORIGINAL_SLTP_WINDOW:
                last_close = float(candles[-1].close)
                mark_to_market = ((last_close - float(entry)) / stop_distance) * (1.0 if long else -1.0)
                result, r = "NEITHER_WITHIN_WINDOW", round(mark_to_market, 4)
            else:
                continue  # still pending, not yet expired
            with SessionLocal() as db:
                cf = db.get(AdaptiveManagerCounterfactualORM, position_id)
                if cf is None:
                    continue
                cf.original_sltp_outcome = result
                cf.original_sltp_r = r
                cf.original_sltp_resolved_at = utcnow()
                cf.updated_at = utcnow()
                db.commit()
            resolved += 1
        return resolved

    # -------------------------------------------------------------------- Part 13: no-BE -------
    async def _resolve_no_be_baselines(self) -> int:
        resolved = 0
        with SessionLocal() as db:
            baselines = {row.position_id: row for row in db.query(AdaptivePositionBaselineORM).all()}
            cfs = {row.position_id: row for row in db.query(AdaptiveManagerCounterfactualORM).filter(AdaptiveManagerCounterfactualORM.no_be_outcome == "PENDING").all()}
            pending_ids = [pid for pid in cfs if pid in baselines]
            events_by_position: dict[str, list[AdaptiveManagementEventORM]] = {}
            if pending_ids:
                for row in db.query(AdaptiveManagementEventORM).filter(AdaptiveManagementEventORM.position_id.in_(pending_ids)).order_by(AdaptiveManagementEventORM.created_at.asc()).all():
                    events_by_position.setdefault(row.position_id, []).append(row)
            work = []
            for position_id in pending_ids:
                events = events_by_position.get(position_id) or []
                activation = self._find_be_activation(events)
                if activation is None:
                    cf = cfs[position_id]
                    cf.no_be_applicable = False
                    cf.no_be_outcome = "NOT_APPLICABLE"
                    cf.updated_at = utcnow()
                    continue
                work.append((position_id, baselines[position_id], activation))
            db.commit()

        for position_id, baseline, (pre_be_sl, activation_time) in work:
            if baseline.original_tp is None or not baseline.initial_stop_distance:
                continue
            activation_time = _aware(activation_time)
            try:
                candles = await self._scan_candles(symbol=baseline.symbol, since=activation_time, window=ORIGINAL_SLTP_WINDOW)
            except Exception as exc:
                logger.warning("Adaptive counterfactual (no-BE): candle fetch failed for %s: %s", baseline.symbol, exc.__class__.__name__)
                continue
            if not candles:
                continue
            long = baseline.direction == "LONG"
            outcome, _candle = self._first_touch(candles, long=long, sl=float(pre_be_sl), tp=float(baseline.original_tp))
            if outcome == "SL":
                result, r = "ORIGINAL_SL_FIRST", -1.0
            elif outcome == "TP":
                result, r = "ORIGINAL_TP_FIRST", round(abs(float(baseline.original_tp) - float(pre_be_sl)) / baseline.initial_stop_distance, 4) if baseline.initial_stop_distance else None
            elif utcnow() - activation_time >= ORIGINAL_SLTP_WINDOW:
                last_close = float(candles[-1].close)
                mark_to_market = ((last_close - float(baseline.original_entry or pre_be_sl)) / baseline.initial_stop_distance) * (1.0 if long else -1.0)
                result, r = "NEITHER_WITHIN_WINDOW", round(mark_to_market, 4)
            else:
                continue
            with SessionLocal() as db:
                cf = db.get(AdaptiveManagerCounterfactualORM, position_id)
                if cf is None:
                    continue
                cf.no_be_applicable = True
                cf.no_be_outcome = result
                cf.no_be_r = r
                cf.no_be_resolved_at = utcnow()
                cf.updated_at = utcnow()
                db.commit()
            resolved += 1
        return resolved

    @staticmethod
    def _find_be_activation(events: list[AdaptiveManagementEventORM]) -> tuple[float, datetime] | None:
        """Confirmed (not merely attempted) break-even activation: a cycle whose decision moved
        SL to/beyond breakeven, where the FOLLOWING cycle's broker-synced sl_before confirms it
        actually took effect. Returns (pre_be_stop, confirmation_timestamp) or None."""
        for i in range(len(events) - 1):
            this_row, next_row = events[i], events[i + 1]
            if this_row.is_at_or_beyond_breakeven and this_row.sl_before is not None and not AdaptiveManagerOutcomeResolver._at_or_beyond(this_row.direction, this_row.entry_price, this_row.sl_before):
                if next_row.sl_before is not None and AdaptiveManagerOutcomeResolver._at_or_beyond(next_row.direction, next_row.entry_price, next_row.sl_before):
                    return this_row.sl_before, next_row.created_at
        return None

    @staticmethod
    def _at_or_beyond(direction: str, entry_price: float | None, sl: float | None) -> bool:
        if entry_price is None or sl is None:
            return False
        return sl >= entry_price if direction == "LONG" else sl <= entry_price

    # --------------------------------------------------------------- Part 8: post-exit shadow ---
    async def _resolve_post_exit_shadows(self) -> int:
        resolved = 0
        with SessionLocal() as db:
            closed = db.query(AdaptivePositionStateORM).filter(AdaptivePositionStateORM.closed_detected_at.isnot(None)).all()
            baselines = {row.position_id: row for row in db.query(AdaptivePositionBaselineORM).all()}
            cfs = {row.position_id: row for row in db.query(AdaptiveManagerCounterfactualORM).filter(AdaptiveManagerCounterfactualORM.post_exit_status == "PENDING").all()}
            targets = []
            for state in closed:
                if state.position_id not in cfs or state.position_id not in baselines:
                    continue
                deals = db.query(AdaptiveTradeEventORM).filter(AdaptiveTradeEventORM.position_id == state.position_id, AdaptiveTradeEventORM.event_type == "DEAL").all()
                exit_deal = max(deals, key=lambda d: d.utc_time or datetime.min.replace(tzinfo=timezone.utc), default=None)
                if exit_deal is None or exit_deal.price is None or exit_deal.utc_time is None:
                    continue
                targets.append((state.position_id, baselines[state.position_id], float(exit_deal.price), _aware(exit_deal.utc_time)))

        for position_id, baseline, exit_price, exit_time in targets:
            if exit_time is None or not baseline.initial_stop_distance:
                continue
            window_elapsed = utcnow() - exit_time >= POST_EXIT_WINDOW
            try:
                candles = await self._scan_candles(symbol=baseline.symbol, since=exit_time, window=POST_EXIT_WINDOW)
            except Exception as exc:
                logger.warning("Adaptive counterfactual (post-exit): candle fetch failed for %s: %s", baseline.symbol, exc.__class__.__name__)
                continue
            if not window_elapsed:
                continue  # wait for the full evidence window before finalizing
            long = baseline.direction == "LONG"
            reached_original_tp = False
            reached_plus_1r = False
            reversed_strongly = False
            would_have_hit_original_sl = False
            plus_1r_level = exit_price + baseline.initial_stop_distance if long else exit_price - baseline.initial_stop_distance
            reversal_level = exit_price - baseline.initial_stop_distance if long else exit_price + baseline.initial_stop_distance
            for candle in candles:
                high, low = float(candle.high), float(candle.low)
                if baseline.original_tp is not None:
                    if (high >= float(baseline.original_tp)) if long else (low <= float(baseline.original_tp)):
                        reached_original_tp = True
                if (high >= plus_1r_level) if long else (low <= plus_1r_level):
                    reached_plus_1r = True
                if (low <= reversal_level) if long else (high >= reversal_level):
                    reversed_strongly = True
                if baseline.original_sl is not None:
                    if (low <= float(baseline.original_sl)) if long else (high >= float(baseline.original_sl)):
                        would_have_hit_original_sl = True
            with SessionLocal() as db:
                cf = db.get(AdaptiveManagerCounterfactualORM, position_id)
                if cf is None:
                    continue
                cf.post_exit_status = "RESOLVED"
                cf.post_exit_reached_original_tp = reached_original_tp
                cf.post_exit_reached_plus_1r = reached_plus_1r
                cf.post_exit_reversed_strongly = reversed_strongly
                cf.post_exit_would_have_hit_original_sl = would_have_hit_original_sl
                cf.post_exit_resolved_at = utcnow()
                cf.updated_at = utcnow()
                db.commit()
            resolved += 1
        return resolved


adaptive_manager_outcome_resolver = AdaptiveManagerOutcomeResolver()
