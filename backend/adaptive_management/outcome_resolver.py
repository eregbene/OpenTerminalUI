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
    original SL -- an evidence signal for possible early exits, never a verdict. Also tracks
    maximum favorable/adverse excursion, time-to-continuation/reversal, and a deterministic A/B/
    C/D classification (see _classify_post_exit) so "the manager exited and price immediately
    reversed" is distinguishable from "left substantial R on the table" instead of collapsing
    into one ambiguous flag.

Every account this resolver touches (demo_10k, ftmo_demo_25k/50k/100k, ...) is resolved using
that account's OWN MT5 bridge (see _adapter_for_account/multi_account.adapter_for_account) and
its own account-scoped rows -- a position from one account can never be resolved using another
account's candles or deal ledger, even if two accounts' broker tickets happen to collide.

Ambiguous same-candle TP/SL ordering always resolves to the more conservative outcome (the
stop, never the target), matching backend/brokers/mt5/outcome_resolver.py's rule. The same
conservative principle extends to post-exit continuation-vs-reversal ambiguity: see
_classify_post_exit and _resolve_post_exit_shadows for the exact tie-break, and the module never
fabricates intrabar tick order to break a genuine tie.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from backend.adaptive_management.orm import AdaptiveManagementEventORM, AdaptiveManagerCounterfactualORM, AdaptivePositionBaselineORM, AdaptivePositionStateORM, AdaptiveTradeEventORM
from backend.brokers.mt5.adapter import MT5Adapter, mt5_adapter
from backend.brokers.mt5.multi_account import adapter_for_account
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

RESOLUTION_TIMEFRAME = "M5"
POLL_INTERVAL_SECONDS = 300
ORIGINAL_SLTP_WINDOW = timedelta(days=10)
POST_EXIT_WINDOW = timedelta(days=3)
DEFAULT_ACCOUNT_ID = "demo_10k"

# "Substantial additional R left on the table" (post-exit classification bucket D) -- a
# post-exit MFE at or beyond this many multiples of the original 1R stop distance, even when
# the original TP itself was never reached. Documented here rather than inlined so the
# threshold choice is visible and easy to revisit; purely an analytics/reporting cutoff, never
# fed back into any trading decision.
SUBSTANTIAL_R_LEFT_THRESHOLD = 2.0

# MT5 deal `entry` codes (mirrors backend/brokers/mt5/persistence.py's identical convention).
_CLOSING_DEAL_ENTRIES = {"1", "3", "OUT", "OUT_BY", "DEAL_ENTRY_OUT", "DEAL_ENTRY_OUT_BY"}
_OPENING_DEAL_ENTRIES = {"0", "2", "IN", "INOUT", "DEAL_ENTRY_IN", "DEAL_ENTRY_INOUT"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class AdaptiveManagerOutcomeResolver:
    def __init__(self, adapter: MT5Adapter | None = None, *, account_adapter_factory: Callable[[str], MT5Adapter] | None = None) -> None:
        self.adapter = adapter or mt5_adapter
        self._account_adapter_factory = account_adapter_factory or adapter_for_account
        # demo_10k always resolves to self.adapter (preserves the pre-multi-account default and
        # lets tests inject a single fake adapter without needing account_registry.py wired up).
        self._account_adapters: dict[str, MT5Adapter] = {DEFAULT_ACCOUNT_ID: self.adapter}
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    def _adapter_for_account(self, account_id: str) -> MT5Adapter:
        account_id = account_id or DEFAULT_ACCOUNT_ID
        cached = self._account_adapters.get(account_id)
        if cached is not None:
            return cached
        try:
            resolved = self._account_adapter_factory(account_id)
        except Exception as exc:
            logger.warning("Adaptive counterfactual: no MT5 adapter available for account_id=%s (%s); falling back to default account's adapter", account_id, exc.__class__.__name__)
            resolved = self.adapter
        self._account_adapters[account_id] = resolved
        return resolved

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
        backfilled = self._backfill_missing_baselines()
        original = await self._resolve_original_sltp_baselines()
        no_be = await self._resolve_no_be_baselines()
        post_exit = await self._resolve_post_exit_shadows()
        return {"baselines_backfilled": backfilled, "original_sltp_resolved": original, "no_be_resolved": no_be, "post_exit_resolved": post_exit}

    # ------------------------------------------------------- backlog: recover missing baselines ---
    def _backfill_missing_baselines(self) -> int:
        """Best-effort recovery for closed positions whose AdaptivePositionBaselineORM row was
        never written -- e.g. closed before event_capture.py's capture_position_baseline hook
        went live. AdaptivePositionStateORM captures the same original_entry/original_sl/
        original_tp/original_stop_distance/original_risk_money fields independently and
        immutably (see its "Canonical monetary risk" block), so when a closed state has them but
        no baseline row exists, reconstruct the baseline from the state row instead of leaving
        the position permanently and silently unresolvable by every counterfactual (original
        SL/TP, no-BE, and post-exit all require a baseline row to even get a counterfactual row
        created). Idempotent and non-destructive: never touches a position that already has a
        baseline row -- capture_position_baseline in event_capture.py remains the primary,
        immediate write path for every position going forward."""
        backfilled = 0
        with SessionLocal() as db:
            missing = (
                db.query(AdaptivePositionStateORM)
                .outerjoin(AdaptivePositionBaselineORM, AdaptivePositionBaselineORM.position_id == AdaptivePositionStateORM.position_id)
                .filter(AdaptivePositionStateORM.closed_detected_at.isnot(None), AdaptivePositionBaselineORM.position_id.is_(None))
                .all()
            )
            for state in missing:
                original_entry = state.original_entry if state.original_entry is not None else state.entry_price
                original_sl = state.original_sl
                original_tp = state.original_tp
                stop_distance = state.original_stop_distance
                if not stop_distance and original_entry is not None and original_sl is not None:
                    stop_distance = abs(original_entry - original_sl)
                if original_entry is None or original_sl is None or not stop_distance:
                    continue  # genuinely unrecoverable -- no fabricated baseline is ever written
                reward_risk = round(abs(original_tp - original_entry) / stop_distance, 4) if (original_tp is not None and stop_distance) else None
                row = AdaptivePositionBaselineORM(
                    position_id=state.position_id,
                    account_id=state.account_id or DEFAULT_ACCOUNT_ID,
                    broker_ticket=state.broker_ticket,
                    symbol=state.symbol,
                    direction=state.direction,
                    original_entry=original_entry,
                    original_sl=original_sl,
                    original_tp=original_tp,
                    initial_stop_distance=stop_distance,
                    initial_reward_risk=reward_risk,
                    initial_risk_money=state.original_risk_money,
                    original_strategy=state.strategy_id,
                )
                db.add(row)
                backfilled += 1
            if backfilled:
                db.commit()
        return backfilled

    # ------------------------------------------------------------------ shared candle scan ---
    async def _scan_candles(self, *, account_id: str, symbol: str, since: datetime, window: timedelta) -> list[Any]:
        adapter = self._adapter_for_account(account_id)
        elapsed = utcnow() - since
        bars_needed = min(3000, max(20, int(min(elapsed, window).total_seconds() // 300) + 10))
        candles = await adapter.candles(symbol, RESOLUTION_TIMEFRAME, count=bars_needed, completed_only=True)
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
                    cf = AdaptiveManagerCounterfactualORM(position_id=baseline.position_id, symbol=baseline.symbol, account_id=getattr(baseline, "account_id", None) or DEFAULT_ACCOUNT_ID)
                    db.add(cf)
                    db.commit()
                if cf.original_sltp_outcome == "PENDING":
                    pending.append((baseline, state, cf))
            targets = [(b.position_id, b.account_id or DEFAULT_ACCOUNT_ID, b.symbol, b.direction, b.original_entry, b.original_sl, b.original_tp, b.initial_stop_distance, b.initial_reward_risk, state.opened_at) for b, state, _cf in pending]

        for position_id, account_id, symbol, direction, entry, sl, tp, stop_distance, reward_risk, opened_at in targets:
            opened_at = _aware(opened_at)
            if entry is None or sl is None or tp is None or opened_at is None or not stop_distance:
                continue
            try:
                candles = await self._scan_candles(account_id=account_id, symbol=symbol, since=opened_at, window=ORIGINAL_SLTP_WINDOW)
            except Exception as exc:
                logger.warning("Adaptive counterfactual: candle fetch failed for account_id=%s symbol=%s: %s", account_id, symbol, exc.__class__.__name__)
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
                # BUG FIX (found live, this session): `baselines[position_id]` is a SQLAlchemy
                # ORM instance bound to THIS `with SessionLocal() as db:` block's session -- once
                # that block exits below, the session closes and any attribute access on it
                # raises DetachedInstanceError (this crash-looped on every single cycle in
                # production before this fix). Snapshotting the needed fields into a plain tuple
                # HERE, while the session is still open, makes `work`'s contents session-
                # independent for the second loop below.
                b = baselines[position_id]
                snapshot = (b.original_tp, b.initial_stop_distance, b.account_id, b.symbol, b.direction, b.original_entry)
                work.append((position_id, snapshot, activation))
            db.commit()

        for position_id, (original_tp, initial_stop_distance, baseline_account_id, symbol, direction, original_entry), (pre_be_sl, activation_time) in work:
            if original_tp is None or not initial_stop_distance:
                continue
            activation_time = _aware(activation_time)
            account_id = baseline_account_id or DEFAULT_ACCOUNT_ID
            try:
                candles = await self._scan_candles(account_id=account_id, symbol=symbol, since=activation_time, window=ORIGINAL_SLTP_WINDOW)
            except Exception as exc:
                logger.warning("Adaptive counterfactual (no-BE): candle fetch failed for account_id=%s symbol=%s: %s", account_id, symbol, exc.__class__.__name__)
                continue
            if not candles:
                continue
            long = direction == "LONG"
            outcome, _candle = self._first_touch(candles, long=long, sl=float(pre_be_sl), tp=float(original_tp))
            if outcome == "SL":
                result, r = "ORIGINAL_SL_FIRST", -1.0
            elif outcome == "TP":
                result, r = "ORIGINAL_TP_FIRST", round(abs(float(original_tp) - float(pre_be_sl)) / initial_stop_distance, 4) if initial_stop_distance else None
            elif utcnow() - activation_time >= ORIGINAL_SLTP_WINDOW:
                last_close = float(candles[-1].close)
                mark_to_market = ((last_close - float(original_entry or pre_be_sl)) / initial_stop_distance) * (1.0 if long else -1.0)
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
    @staticmethod
    def _is_closing_deal(deal: AdaptiveTradeEventORM) -> bool:
        """A DEAL row represents a position-closing execution when the broker-reported entry
        code is OUT/OUT_BY, or -- when the connector didn't populate `entry` at all (observed in
        this codebase's live MT5 payloads) -- when it carries a nonzero realized P&L, which an
        opening fill never has. Mirrors backend/brokers/mt5/persistence.py's identical
        entry-code/profit fallback so both call sites agree on what a "close" looks like."""
        raw = deal.raw_payload or {}
        entry = str(raw.get("entry")).upper() if raw.get("entry") is not None else ""
        if entry in _CLOSING_DEAL_ENTRIES:
            return True
        if entry in _OPENING_DEAL_ENTRIES:
            return False
        return bool(deal.realized_pnl)

    @staticmethod
    def _classify_post_exit(*, reached_original_tp: bool, mfe_r: float | None, reached_plus_1r: bool, reversed_strongly: bool) -> str:
        """Deterministic A/B/C/D bucket from the Part 8 spec, most-informative outcome first:

          C. TP_LATER_REACHED    -- price later reached the original TP.
          D. SUBSTANTIAL_R_LEFT  -- never reached original TP, but MFE after exit was still >=
                                    SUBSTANTIAL_R_LEFT_THRESHOLD multiples of the original 1R.
          B. MILD_CONTINUATION   -- reached the +1R continuation threshold but nothing bigger.
          A. IMMEDIATE_REVERSAL  -- never reached +1R continuation; price instead reversed past
                                    the -1R threshold (would likely have hit the original SL).
             NO_SIGNIFICANT_MOVE -- neither continuation nor reversal threshold reached in the
                                    evidence window.

        C and D outrank B and A deliberately: a trade that reversed hard at some point but LATER
        also reached the original TP (or a large favorable excursion) is still, on the whole,
        evidence of "left money on the table," not "exiting was vindicated". Note mfe_r (which
        can trigger D on its own) is a raw, unambiguous OHLC statistic (a bar's high/low is a
        fact regardless of intrabar tick order) -- it is NOT run through the same-candle
        conservative tie-break that reached_plus_1r/reversed_strongly are (see
        _resolve_post_exit_shadows: a bar that touches both the reversal AND continuation levels
        only ever confirms the reversal, never the continuation, for that bar)."""
        if reached_original_tp:
            return "TP_LATER_REACHED"
        if mfe_r is not None and mfe_r >= SUBSTANTIAL_R_LEFT_THRESHOLD:
            return "SUBSTANTIAL_R_LEFT"
        if reached_plus_1r:
            return "MILD_CONTINUATION"
        if reversed_strongly:
            return "IMMEDIATE_REVERSAL"
        return "NO_SIGNIFICANT_MOVE"

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
                deals = (
                    db.query(AdaptiveTradeEventORM)
                    .filter(
                        AdaptiveTradeEventORM.position_id == state.position_id,
                        AdaptiveTradeEventORM.account_id == state.account_id,
                        AdaptiveTradeEventORM.event_type == "DEAL",
                    )
                    .all()
                )
                if not deals:
                    continue
                closing_deals = [d for d in deals if self._is_closing_deal(d)]
                # Fall back to every deal (old behavior) only when none can be identified as a
                # close -- never leaves a fully-closed position unresolvable purely because the
                # connector omitted the entry-type field on every one of its deals.
                candidate_deals = closing_deals or deals
                exit_deal = max(candidate_deals, key=lambda d: d.utc_time or datetime.min.replace(tzinfo=timezone.utc), default=None)
                if exit_deal is None or exit_deal.price is None or exit_deal.utc_time is None:
                    continue
                # BUG FIX (same DetachedInstanceError pattern found live in _resolve_no_be_
                # baselines this session, and the one _resolve_original_sltp_baselines already
                # avoided): snapshot the needed baseline fields into a plain tuple HERE, inside
                # the still-open session, rather than carrying the ORM instance itself past the
                # `with` block's close.
                b = baselines[state.position_id]
                targets.append((
                    state.position_id, state.account_id or DEFAULT_ACCOUNT_ID, b.symbol, b.direction,
                    b.initial_stop_distance, b.original_tp, b.original_sl, float(exit_deal.price), _aware(exit_deal.utc_time),
                ))

        for position_id, account_id, symbol, direction, initial_stop_distance, original_tp, original_sl, exit_price, exit_time in targets:
            if exit_time is None or not initial_stop_distance:
                continue
            window_elapsed = utcnow() - exit_time >= POST_EXIT_WINDOW
            try:
                candles = await self._scan_candles(account_id=account_id, symbol=symbol, since=exit_time, window=POST_EXIT_WINDOW)
            except Exception as exc:
                logger.warning("Adaptive counterfactual (post-exit): candle fetch failed for account_id=%s symbol=%s: %s", account_id, symbol, exc.__class__.__name__)
                continue
            if not window_elapsed:
                continue  # wait for the full evidence window before finalizing
            if not candles:
                # The window is over and the broker/bridge STILL never returned a single candle
                # for this symbol/period -- explicit, distinct terminal state (never silently
                # read as "price didn't move", per the spec's "missing data stays explicit" rule).
                with SessionLocal() as db:
                    cf = db.get(AdaptiveManagerCounterfactualORM, position_id)
                    if cf is None:
                        continue
                    cf.post_exit_status = "UNRESOLVABLE_NO_DATA"
                    cf.post_exit_data_note = "NO_CANDLES_RETURNED_AFTER_WINDOW_ELAPSED"
                    cf.post_exit_candles_scanned = 0
                    cf.post_exit_resolved_at = utcnow()
                    cf.updated_at = utcnow()
                    db.commit()
                resolved += 1
                continue

            long = direction == "LONG"
            stop_distance = initial_stop_distance
            plus_1r_level = exit_price + stop_distance if long else exit_price - stop_distance
            reversal_level = exit_price - stop_distance if long else exit_price + stop_distance

            reached_original_tp = False
            reached_plus_1r = False
            reversed_strongly = False
            would_have_hit_original_sl = False
            mfe_price = 0.0
            mae_price = 0.0
            time_to_continuation: datetime | None = None
            time_to_reversal: datetime | None = None

            for candle in candles:
                high, low = float(candle.high), float(candle.low)
                candle_time = _aware(candle.time)
                favorable = max((high - exit_price) if long else (exit_price - low), 0.0)
                adverse = max((exit_price - low) if long else (high - exit_price), 0.0)
                mfe_price = max(mfe_price, favorable)
                mae_price = max(mae_price, adverse)

                if original_tp is not None:
                    if (high >= float(original_tp)) if long else (low <= float(original_tp)):
                        reached_original_tp = True
                if original_sl is not None:
                    if (low <= float(original_sl)) if long else (high >= float(original_sl)):
                        would_have_hit_original_sl = True

                adverse_touched_now = (low <= reversal_level) if long else (high >= reversal_level)
                favorable_touched_now = (high >= plus_1r_level) if long else (low <= plus_1r_level)
                if adverse_touched_now:
                    reversed_strongly = True
                    if time_to_reversal is None:
                        time_to_reversal = candle_time
                # Conservative same-candle tie-break (never fabricates intrabar tick order, per
                # the module docstring): a bar that touches BOTH the reversal and continuation
                # thresholds only ever confirms the reversal for that bar -- `reached_plus_1r`
                # and its timestamp are only set from a bar where the continuation threshold is
                # touched WITHOUT a simultaneous adverse touch, matching _first_touch's existing
                # "the stop, never the target, wins a same-candle tie" convention. A later,
                # unambiguous bar can still confirm continuation even after an earlier tied bar.
                if favorable_touched_now and not adverse_touched_now:
                    reached_plus_1r = True
                    if time_to_continuation is None:
                        time_to_continuation = candle_time

            mfe_r = round(mfe_price / stop_distance, 4)
            mae_r = round(mae_price / stop_distance, 4)
            classification = self._classify_post_exit(reached_original_tp=reached_original_tp, mfe_r=mfe_r, reached_plus_1r=reached_plus_1r, reversed_strongly=reversed_strongly)
            time_to_continuation_seconds = int((time_to_continuation - exit_time).total_seconds()) if time_to_continuation else None
            time_to_reversal_seconds = int((time_to_reversal - exit_time).total_seconds()) if time_to_reversal else None

            with SessionLocal() as db:
                cf = db.get(AdaptiveManagerCounterfactualORM, position_id)
                if cf is None:
                    continue
                cf.post_exit_status = "RESOLVED"
                cf.post_exit_reached_original_tp = reached_original_tp
                cf.post_exit_reached_plus_1r = reached_plus_1r
                cf.post_exit_reversed_strongly = reversed_strongly
                cf.post_exit_would_have_hit_original_sl = would_have_hit_original_sl
                cf.post_exit_mfe_r = mfe_r
                cf.post_exit_mae_r = mae_r
                cf.post_exit_additional_r_available = mfe_r
                cf.post_exit_time_to_continuation_seconds = time_to_continuation_seconds
                cf.post_exit_time_to_reversal_seconds = time_to_reversal_seconds
                cf.post_exit_classification = classification
                cf.post_exit_candles_scanned = len(candles)
                cf.post_exit_data_note = None
                cf.post_exit_resolved_at = utcnow()
                cf.updated_at = utcnow()
                db.commit()
            resolved += 1
        return resolved


adaptive_manager_outcome_resolver = AdaptiveManagerOutcomeResolver()
