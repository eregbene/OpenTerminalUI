"""Historical Adaptive Intelligence backfill (Adaptive-Historical-Intelligence-Backfill
directive, Phases 2-6, 20-21).

Reconstructs intermediate trade-STATE checkpoints from the already-replayed entry-side historical
corpus (historical_pattern_fingerprints + historical_setup_outcomes, built by the earlier
corpus-expansion directive) and resolves what happened AFTER each checkpoint -- giving Adaptive
Historical Intelligence its own large evidence base instead of waiting on real DEMO-managed
positions (currently ~30 resolved counterfactuals).

Never touches AdaptiveManagementEventORM/AdaptivePositionBaselineORM/AdaptiveManagerCounterfactualORM
(the REAL per-position tables) -- writes only to HistoricalAdaptiveStateORM/
HistoricalAdaptiveOutcomeORM, kept deliberately separate so no synthetic position can ever be
mistaken for a real broker position by reconciliation/freshness/circuit-breaker logic (Phase 23).

No future candle influences a state's OWN fields: for each historical trade, ONE forward candle
series is fetched (entry_time -> +800 M15 bars, reusing outcomes.py's already-quality-tiered
_future_candles_with_quality), walked forward chronologically once. A checkpoint's STATE uses only
the PREFIX of that walk up to and including its own candle; its OUTCOME uses only the SUFFIX
strictly after it. This is the same walk-forward/no-lookahead discipline replay.py and outcomes.py
already established for the entry side, applied to intermediate points instead of only entry.

Structure/regime AT a checkpoint reuses replay.py's already no-lookahead-proven bars_as_of/
build_strategy_context/analyze_bars -- never a second parallel structure computation.
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from backend.adaptive_management.outcome_resolver import AdaptiveManagerOutcomeResolver, SUBSTANTIAL_R_LEFT_THRESHOLD
from backend.historical_intelligence import outcomes
from backend.historical_intelligence.fingerprint import time_of_day_bucket
from backend.historical_intelligence.orm import (
    ADAPTIVE_STATE_MODEL_VERSION, HistoricalAdaptiveOutcomeORM, HistoricalAdaptiveStateORM,
    HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM,
)
from backend.historical_intelligence.replay import bars_as_of, build_strategy_context, required_lookback
from backend.market_structure.bar_utils import average_true_range, normalize_bars
from backend.market_structure.models import Direction
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

_R_MILESTONES = outcomes._R_MILESTONES
_MILESTONE_LABELS = {0.25: "R_0_25", 0.5: "R_0_50", 0.75: "R_0_75", 1.0: "R_1_00", 1.5: "R_1_50", 2.0: "R_2_00"}
_ADVERSE_FRACTION = outcomes._IMMEDIATE_FAILURE_ADVERSE_FRACTION
_GIVEBACK_THRESHOLD_R = 0.25  # minimum giveback (MFE - current) to trigger a GIVEBACK checkpoint
_GIVEBACK_MIN_MFE_R = 0.5  # only meaningful once real profit existed to give back
_MAX_LOOKFORWARD_BARS = outcomes._MAX_LOOKFORWARD_BARS
# Postgres caps a single query at 65,535 bound parameters -- the EURUSD candidate corpus alone
# has already crossed that with resume=False (no checkpoint filter, full-history scan). Chunk the
# same way ingestion.py's _persist_bars() does for its IN(...) prefetches.
_IN_CLAUSE_CHUNK_SIZE = 10_000


def _state_id(source_fingerprint_id: str, milestone_label: str) -> str:
    return "HAS_" + hashlib.sha256(f"{source_fingerprint_id}:{milestone_label}:{ADAPTIVE_STATE_MODEL_VERSION}".encode()).hexdigest()[:40]


def _outcome_id(state_id: str) -> str:
    return "HAO_" + hashlib.sha256(state_id.encode()).hexdigest()[:40]


def _ensure_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _structure_snapshot_at(*, canonical_symbol: str, broker_symbol: str, at: datetime, direction: str, provider: str = "MT5") -> dict[str, Any]:
    """Real, no-lookahead structure/regime snapshot AT `at` -- reuses replay.py's already-proven
    bars_as_of/build_strategy_context (byte-identical to the entry-side replay path), never a
    second parallel computation. Returns status=INSUFFICIENT_HISTORY honestly rather than
    guessing when there isn't enough trailing data at `at`.

    `provider` MUST match the fingerprint's own source provider (see backfill_trade's caller) --
    a bug found auditing this module for the ForexSB corpus-expansion directive: this previously
    hardcoded provider="MT5" unconditionally, so every checkpoint reconstructed for a
    non-MT5-sourced (e.g. FOREXSB) historical trade would silently fail this lookup for any `at`
    before MT5's own corpus starts (~2022-08) -- not incorrect data, but a silent
    INSUFFICIENT_HISTORY that would have discarded structure/regime context for the entire deep
    ForexSB-sourced corpus instead of actually reconstructing it from the same provider the trade
    itself came from."""
    m15 = await bars_as_of(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, timeframe="M15", at=at, count=required_lookback(timeframe="M15"), provider=provider)
    h1 = await bars_as_of(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, timeframe="H1", at=at, count=required_lookback(timeframe="H1"), provider=provider)
    h4 = await bars_as_of(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, timeframe="H4", at=at, count=required_lookback(timeframe="H4"), provider=provider)
    if len(m15) < 60 or len(h1) < 50 or len(h4) < 30:
        return {"status": "INSUFFICIENT_HISTORY"}

    m15_rows = [c.model_dump(mode="json") for c in m15]
    h1_rows = [c.model_dump(mode="json") for c in h1]
    h4_rows = [c.model_dump(mode="json") for c in h4]
    last_close = m15[-1].close
    ctx = build_strategy_context(
        symbol=canonical_symbol, broker_symbol=broker_symbol, m15_rows=m15_rows, h1_rows=h1_rows, h4_rows=h4_rows,
        bid=last_close, ask=last_close, spread=Decimal("0"), now=at,
    )
    if ctx is None:
        return {"status": "INSUFFICIENT_HISTORY"}

    long = direction.upper() == "LONG"
    against_direction = Direction.BEARISH if long else Direction.BULLISH
    bos_against = choch_against = mss_against = False
    for b in ctx.m15_snapshot.breaks:
        if str(b.continuation_direction) != str(against_direction):
            continue
        kind = str(b.break_kind)
        if kind == "bos":
            bos_against = True
        elif kind == "choch":
            choch_against = True
        elif kind == "mss":
            mss_against = True

    atr_regime = None
    try:
        normalized = normalize_bars(m15_rows, symbol=broker_symbol, timeframe="M15")
        atrs = [a for a in average_true_range(normalized, 14) if a is not None]
        if ctx.atr_m15 is not None and atrs:
            below = sum(1 for a in atrs if a <= float(ctx.atr_m15))
            pct = 100.0 * below / len(atrs)
            atr_regime = "LOW" if pct < 25 else "NORMAL" if pct < 75 else "HIGH" if pct < 90 else "EXTREME"
    except Exception:
        atr_regime = None

    structure_intact = not (bos_against or choch_against or mss_against)
    return {
        "status": "OK", "current_regime": ctx.regime, "session": time_of_day_bucket(at),
        "bos_against_trade": bos_against, "choch_against_trade": choch_against, "mss_against_trade": mss_against,
        "structure_intact": structure_intact, "atr_regime": atr_regime,
    }


def _detect_checkpoints(*, entry: float, stop_loss: float, take_profit: float | None, direction: str, candles: list[Any]) -> list[dict[str, Any]]:
    """Pure function: walks `candles` (ascending, already fetched) chronologically ONCE, emitting
    at most one checkpoint per milestone (Phase 3 -- never one row per bar). Each checkpoint dict
    carries `candle_index` (position in `candles`) so the caller can split the SAME series into
    prefix (state) / suffix (outcome) without a second fetch.

    Stops generating checkpoints once the ORIGINAL take_profit or stop_loss is touched -- a
    trade managed with its static original levels closes there; a "state" claiming the position
    is still open with e.g. current_r=1.9 when the original TP sat at 1.5R would describe a
    position that, realistically, no longer exists. The closing bar itself may still contribute
    a milestone checkpoint (its crossing can coincide with the close), but no LATER bar may."""
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return []
    long = direction.upper() == "LONG"

    checkpoints: list[dict[str, Any]] = []
    seen_milestones: set[float] = set()
    seen_adverse = False
    seen_giveback = False
    max_favorable = 0.0
    max_adverse = 0.0

    for idx, candle in enumerate(candles):
        high, low, close = candle.high, candle.low, candle.close
        favorable = (high - entry) if long else (entry - low)
        adverse = (entry - low) if long else (high - entry)
        max_favorable = max(max_favorable, favorable)
        max_adverse = max(max_adverse, adverse)
        current_r = ((close - entry) / risk) if long else ((entry - close) / risk)
        mfe_r_to_t = max_favorable / risk
        mae_r_to_t = max_adverse / risk

        for milestone in _R_MILESTONES:
            if milestone in seen_milestones:
                continue
            if mfe_r_to_t >= milestone:
                seen_milestones.add(milestone)
                checkpoints.append({
                    "candle_index": idx, "milestone_label": _MILESTONE_LABELS[milestone],
                    "current_r": round(current_r, 4), "max_achieved_r": round(mfe_r_to_t, 4), "min_achieved_r": round(-mae_r_to_t, 4),
                    "candle_time": candle.timestamp,
                })

        if not seen_adverse and mae_r_to_t >= _ADVERSE_FRACTION and mfe_r_to_t < 0.25:
            seen_adverse = True
            checkpoints.append({
                "candle_index": idx, "milestone_label": "ADVERSE_MOVE",
                "current_r": round(current_r, 4), "max_achieved_r": round(mfe_r_to_t, 4), "min_achieved_r": round(-mae_r_to_t, 4),
                "candle_time": candle.timestamp,
            })

        if not seen_giveback and mfe_r_to_t >= _GIVEBACK_MIN_MFE_R and (mfe_r_to_t - current_r) >= _GIVEBACK_THRESHOLD_R:
            seen_giveback = True
            checkpoints.append({
                "candle_index": idx, "milestone_label": "GIVEBACK",
                "current_r": round(current_r, 4), "max_achieved_r": round(mfe_r_to_t, 4), "min_achieved_r": round(-mae_r_to_t, 4),
                "candle_time": candle.timestamp,
            })

        tp_touched = (take_profit is not None) and ((high >= take_profit) if long else (low <= take_profit))
        sl_touched = (low <= stop_loss) if long else (high >= stop_loss)
        if tp_touched or sl_touched:
            break

    return checkpoints


def _resolve_checkpoint_outcome(*, entry: float, stop_loss: float, take_profit: float, direction: str, checkpoint: dict[str, Any], future_candles: list[Any]) -> dict[str, Any]:
    """Walk-forward from the checkpoint's OWN candle onward (future_candles is already the
    suffix strictly after it -- see the caller). Reuses the SAME "additional R from here"
    semantics and same-candle SL-wins tie-break convention as
    outcome_resolver.py::_resolve_post_exit_shadows (a real, already-shipped, tested algorithm --
    not reimplemented differently here, only re-based on the checkpoint's price instead of a
    real exit price)."""
    risk = abs(entry - stop_loss)
    long = direction.upper() == "LONG"
    checkpoint_price = entry + checkpoint["current_r"] * risk * (1 if long else -1)

    plus_1r_level = checkpoint_price + risk if long else checkpoint_price - risk
    reversal_level = checkpoint_price - risk if long else checkpoint_price + risk
    entry_level = entry  # for round-trip-to-breakeven/loss

    reached_original_tp = False
    would_have_hit_original_sl = False
    reached_plus_1r = False
    reversed_strongly = False
    round_trip_to_be = False
    round_trip_to_loss = False
    max_favorable = 0.0
    max_adverse = 0.0
    time_to_continuation: datetime | None = None
    time_to_reversal: datetime | None = None
    milestone_hits: dict[float, bool] = {m: False for m in (0.25, 0.5, 0.75, 1.5, 2.0)}
    data_quality = "HIGH"

    if not future_candles:
        return {"status": "PENDING", "bars_scanned": 0, "data_quality": "UNTRUSTED"}

    bars_scanned = 0
    for candle in future_candles:
        bars_scanned += 1
        high, low = candle.high, candle.low
        data_quality = outcomes._worse(data_quality, candle.quality_tier)
        favorable = max((high - checkpoint_price) if long else (checkpoint_price - low), 0.0)
        adverse = max((checkpoint_price - low) if long else (high - checkpoint_price), 0.0)
        max_favorable = max(max_favorable, favorable)
        max_adverse = max(max_adverse, adverse)
        favorable_r_now = max_favorable / risk

        for m in milestone_hits:
            if not milestone_hits[m] and favorable_r_now >= m:
                milestone_hits[m] = True

        if take_profit is not None:
            if (high >= take_profit) if long else (low <= take_profit):
                reached_original_tp = True
        if stop_loss is not None:
            if (low <= stop_loss) if long else (high >= stop_loss):
                would_have_hit_original_sl = True

        adverse_touched_now = (low <= reversal_level) if long else (high >= reversal_level)
        favorable_touched_now = (high >= plus_1r_level) if long else (low <= plus_1r_level)
        if adverse_touched_now:
            reversed_strongly = True
            if time_to_reversal is None:
                time_to_reversal = candle.timestamp
        if favorable_touched_now and not adverse_touched_now:
            reached_plus_1r = True
            if time_to_continuation is None:
                time_to_continuation = candle.timestamp

        crossed_entry_down = (low <= entry_level) if long else (high >= entry_level)
        if crossed_entry_down:
            round_trip_to_be = True
            beyond_entry_into_loss = (low <= entry_level - risk * 0.05) if long else (high >= entry_level + risk * 0.05)
            if beyond_entry_into_loss:
                round_trip_to_loss = True

        # A real trade closes the instant its ORIGINAL SL/TP is touched -- continuing to scan
        # past that point would attribute unrelated LATER price action (potentially days of it,
        # since future_candles can span the full _MAX_LOOKFORWARD_BARS window) to "additional R
        # available from this state", which is not a real, ever-realizable outcome under a
        # static-original-levels policy. Bug found via real-data smoke test: an ADVERSE_MOVE
        # checkpoint on a trade whose final outcome_r was +1.5R showed post_exit_additional_r_
        # available=7.99 before this fix, because the scan kept accumulating MFE for days after
        # the original TP had already been reached.
        if reached_original_tp or would_have_hit_original_sl:
            break

    mfe_r = round(max_favorable / risk, 4)
    mae_r = round(max_adverse / risk, 4)
    classification = AdaptiveManagerOutcomeResolver._classify_post_exit(
        reached_original_tp=reached_original_tp, mfe_r=mfe_r, reached_plus_1r=reached_plus_1r, reversed_strongly=reversed_strongly,
    )
    checkpoint_time = _ensure_utc(checkpoint["candle_time"])
    time_to_continuation_seconds = int((_ensure_utc(time_to_continuation) - checkpoint_time).total_seconds()) if time_to_continuation else None
    time_to_reversal_seconds = int((_ensure_utc(time_to_reversal) - checkpoint_time).total_seconds()) if time_to_reversal else None

    return {
        "status": "RESOLVED", "post_exit_reached_original_tp": reached_original_tp,
        "post_exit_reached_plus_1r": reached_plus_1r, "post_exit_reversed_strongly": reversed_strongly,
        "post_exit_would_have_hit_original_sl": would_have_hit_original_sl,
        "post_exit_mfe_r": mfe_r, "post_exit_mae_r": mae_r, "post_exit_additional_r_available": mfe_r,
        "post_exit_time_to_continuation_seconds": time_to_continuation_seconds, "post_exit_time_to_reversal_seconds": time_to_reversal_seconds,
        "post_exit_classification": classification,
        "reached_plus_0_25r_additional": milestone_hits[0.25], "reached_plus_0_5r_additional": milestone_hits[0.5],
        "reached_plus_0_75r_additional": milestone_hits[0.75], "reached_plus_1_5r_additional": milestone_hits[1.5],
        "reached_plus_2r_additional": milestone_hits[2.0],
        "round_trip_to_breakeven": round_trip_to_be, "round_trip_to_loss": round_trip_to_loss,
        "data_quality": data_quality, "bars_scanned": bars_scanned,
    }


async def backfill_trade(fingerprint: HistoricalPatternFingerprintORM, outcome: HistoricalSetupOutcomeORM | None, *, db: Any, compute_structure: bool = True) -> int:
    """Reconstructs and persists every checkpoint state + resolved outcome for ONE historical
    trade occurrence, onto the CALLER-OWNED session `db` (no commit -- batched by the driver,
    matching bulk_replay.py's established Phase-8-throughput pattern). Returns the number of
    NEW states written (idempotent: existing state_ids are updated in place, not duplicated)."""
    entry, stop_loss, take_profit = fingerprint.entry, fingerprint.stop_loss, fingerprint.take_profit
    direction = fingerprint.direction
    entry_time = _ensure_utc(fingerprint.entry_time)

    # `after=entry_time` (strictly greater-than, matching outcomes.label_outcome's own forward-
    # walk convention exactly) -- NOT entry_time minus any offset. Using an earlier boundary
    # would silently include the entry candle itself in the walk, a bar this trade's OWN
    # already-computed HistoricalSetupOutcomeORM.outcome_r never counted, and could register a
    # spurious ADVERSE_MOVE/SL-touch that never actually happened in the trusted resolution.
    candles = outcomes._future_candles_with_quality(
        provider=fingerprint.provider, broker_symbol=fingerprint.canonical_symbol, timeframe="M15",
        after=entry_time, limit=_MAX_LOOKFORWARD_BARS,
    )
    if not candles:
        return 0

    checkpoints = _detect_checkpoints(entry=entry, stop_loss=stop_loss, take_profit=take_profit, direction=direction, candles=candles)
    written = 0
    for cp in checkpoints:
        state_id = _state_id(fingerprint.fingerprint_id, cp["milestone_label"])
        already_existed = db.get(HistoricalAdaptiveStateORM, state_id) is not None

        structure = {"status": "SKIPPED"}
        if compute_structure:
            try:
                structure = await _structure_snapshot_at(
                    canonical_symbol=fingerprint.canonical_symbol, broker_symbol=fingerprint.canonical_symbol,
                    at=_ensure_utc(cp["candle_time"]), direction=direction, provider=fingerprint.provider,
                )
            except Exception as exc:
                logger.debug("adaptive_backfill: structure snapshot failed for %s/%s: %s", fingerprint.fingerprint_id, cp["milestone_label"], exc.__class__.__name__)

        elapsed_seconds = (_ensure_utc(cp["candle_time"]) - entry_time).total_seconds()
        giveback = round(cp["max_achieved_r"] - cp["current_r"], 4)

        row = db.get(HistoricalAdaptiveStateORM, state_id)
        if row is None:
            row = HistoricalAdaptiveStateORM(state_id=state_id, created_at=datetime.now(timezone.utc))
            db.add(row)
        row.source_fingerprint_id = fingerprint.fingerprint_id
        row.strategy_version = fingerprint.strategy_version
        row.canonical_symbol = fingerprint.canonical_symbol
        row.broker_symbol = fingerprint.canonical_symbol
        row.direction = direction
        row.strategy = fingerprint.anchor_strategy
        row.original_regime = fingerprint.regime_broad
        row.current_regime = structure.get("current_regime") if structure.get("status") == "OK" else fingerprint.regime_broad
        row.state_time = _ensure_utc(cp["candle_time"])
        row.current_r = cp["current_r"]
        row.max_achieved_r = cp["max_achieved_r"]
        row.min_achieved_r = cp["min_achieved_r"]
        row.elapsed_seconds = elapsed_seconds
        row.is_at_or_beyond_breakeven = cp["current_r"] >= 0
        row.is_trailing_action = False
        row.milestone_label = cp["milestone_label"]
        row.giveback_from_mfe_r = giveback
        row.structure_intact = structure.get("structure_intact")
        row.bos_against_trade = bool(structure.get("bos_against_trade", False))
        row.choch_against_trade = bool(structure.get("choch_against_trade", False))
        row.mss_against_trade = bool(structure.get("mss_against_trade", False))
        row.atr_regime = structure.get("atr_regime")
        row.session = structure.get("session") or time_of_day_bucket(_ensure_utc(cp["candle_time"]))

        from backend.historical_intelligence.adaptive_fingerprint import build_state_fingerprint
        peer_fields = build_state_fingerprint(
            strategy=fingerprint.anchor_strategy, symbol=fingerprint.canonical_symbol, direction=direction,
            original_regime=fingerprint.regime_broad, current_regime=row.current_regime, current_r=cp["current_r"],
            max_achieved_r=cp["max_achieved_r"], min_achieved_r=cp["min_achieved_r"], elapsed_seconds=elapsed_seconds,
            is_at_or_beyond_breakeven=row.is_at_or_beyond_breakeven, is_trailing_action=False, now=row.state_time,
        )
        row.peer_group_hash = peer_fields["peer_group_hash"]

        if not already_existed:
            written += 1

        future_suffix = candles[cp["candle_index"] + 1:]
        resolution = _resolve_checkpoint_outcome(entry=entry, stop_loss=stop_loss, take_profit=take_profit, direction=direction, checkpoint=cp, future_candles=future_suffix)
        outcome_id = _outcome_id(state_id)
        orow = db.get(HistoricalAdaptiveOutcomeORM, outcome_id)
        if orow is None:
            orow = HistoricalAdaptiveOutcomeORM(outcome_id=outcome_id, state_id=state_id, created_at=datetime.now(timezone.utc))
            db.add(orow)
        orow.post_exit_status = resolution["status"]
        if resolution["status"] == "RESOLVED":
            for key in (
                "post_exit_reached_original_tp", "post_exit_reached_plus_1r", "post_exit_reversed_strongly",
                "post_exit_would_have_hit_original_sl", "post_exit_mfe_r", "post_exit_mae_r", "post_exit_additional_r_available",
                "post_exit_time_to_continuation_seconds", "post_exit_time_to_reversal_seconds", "post_exit_classification",
                "reached_plus_0_25r_additional", "reached_plus_0_5r_additional", "reached_plus_0_75r_additional",
                "reached_plus_1_5r_additional", "reached_plus_2r_additional", "round_trip_to_breakeven", "round_trip_to_loss",
            ):
                setattr(orow, key, resolution[key])
            orow.resolved_at = datetime.now(timezone.utc)
        orow.data_quality = resolution.get("data_quality")
        orow.bars_scanned = resolution.get("bars_scanned", 0)
        orow.final_r = outcome.outcome_r if outcome is not None else None

    return written


def _checkpoint_progress(anchor_strategy: str | None, canonical_symbol: str | None = None, provider: str | None = None) -> datetime | None:
    """Restart-safety (Phase 20): resume from the newest source fingerprint's entry_time already
    covered by this backfill, per anchor_strategy AND canonical_symbol, so re-running is a cheap
    no-op for already-processed trades.

    Real bug found/fixed during the ForexSB integration directive: this previously scoped the
    checkpoint by anchor_strategy ONLY, never by canonical_symbol, even though
    run_adaptive_backfill's own fingerprint query filters by both when both are given. Calling
    this per-symbol (exactly what pipelining the ForexSB corpus-expansion backfill requires --
    running adaptive backfill for one newly-completed symbol at a time rather than waiting for
    all ten) would compute a checkpoint from the GLOBAL max state_time across every symbol, then
    apply it as an entry_time floor to a query already filtered to ONE symbol -- silently
    excluding every fingerprint for a symbol whose real history is older than whatever the
    globally-newest adaptive state happens to be. Confirmed this would have fired immediately:
    157,174 existing adaptive states already reach state_time 2026-08-13, which would have
    floored out every 2018-2022 ForexSB-sourced fingerprint with zero error, zero warning --
    exactly the kind of silent no-op this whole directive explicitly warns against.

    Second real bug, found later the same directive: per-symbol scoping alone is still not
    enough. EURUSD (like every symbol) has BOTH live MT5-sourced adaptive states (state_time
    reaching today) AND historical FOREXSB-sourced ones (state_time in 2018-2022) under the SAME
    canonical_symbol. Without a provider filter, a FOREXSB-scoped backfill run's checkpoint would
    still resolve to EURUSD's newest state regardless of which provider sourced it -- inheriting
    today's live MT5 watermark and floaring out the entire ForexSB corpus, same failure mode as
    the cross-symbol bug, just one dimension narrower. `provider` filters via a join to the
    SOURCING fingerprint's own provider (HistoricalAdaptiveStateORM has no provider column of its
    own -- it is a derived/reconstructed record, not raw evidence)."""
    with SessionLocal() as db:
        q = db.query(HistoricalAdaptiveStateORM.state_time).order_by(HistoricalAdaptiveStateORM.state_time.desc())
        if anchor_strategy:
            q = q.filter(HistoricalAdaptiveStateORM.strategy == anchor_strategy)
        if canonical_symbol:
            q = q.filter(HistoricalAdaptiveStateORM.canonical_symbol == canonical_symbol.upper())
        if provider:
            q = q.join(
                HistoricalPatternFingerprintORM,
                HistoricalPatternFingerprintORM.fingerprint_id == HistoricalAdaptiveStateORM.source_fingerprint_id,
            ).filter(HistoricalPatternFingerprintORM.provider == provider.upper())
        latest = q.first()
    return latest[0] if latest else None


async def run_adaptive_backfill(
    *, anchor_strategy: str | None = None, canonical_symbol: str | None = None, provider: str | None = None,
    limit: int | None = None,
    commit_batch_size: int = 20, compute_structure: bool = True, resume: bool = True, progress_every: int = 200,
) -> dict[str, Any]:
    """Bulk driver (Phase 20) -- restart-safe, idempotent, batch-committed, mirrors
    bulk_replay.py's replay_symbol_history_fast pattern exactly (one session held open per
    batch, committed once per `commit_batch_size` trades, never per-row).

    `provider`, when given, scopes BOTH the fingerprint scan (so a FOREXSB-only run doesn't also
    walk every MT5 fingerprint for the symbol) AND the resume checkpoint (so it can never inherit
    a different provider's watermark for the same symbol -- see _checkpoint_progress's docstring
    for the bug this fixes). Omitting it preserves existing behavior exactly: every provider is
    scanned together, checkpointed together, same as before this parameter existed."""
    resumed_from = None
    with SessionLocal() as scan_db:
        q = scan_db.query(HistoricalPatternFingerprintORM).order_by(HistoricalPatternFingerprintORM.entry_time.asc())
        if anchor_strategy:
            q = q.filter(HistoricalPatternFingerprintORM.anchor_strategy == anchor_strategy)
        if canonical_symbol:
            q = q.filter(HistoricalPatternFingerprintORM.canonical_symbol == canonical_symbol)
        if provider:
            q = q.filter(HistoricalPatternFingerprintORM.provider == provider.upper())
        if resume:
            checkpoint = _checkpoint_progress(anchor_strategy, canonical_symbol, provider)
            if checkpoint is not None:
                q = q.filter(HistoricalPatternFingerprintORM.entry_time > checkpoint)
                resumed_from = checkpoint.isoformat()
        if limit:
            q = q.limit(limit)
        fingerprints = q.all()
        outcome_map = {}
        if fingerprints:
            fp_ids = [f.fingerprint_id for f in fingerprints]
            for chunk_start in range(0, len(fp_ids), _IN_CLAUSE_CHUNK_SIZE):
                chunk = fp_ids[chunk_start:chunk_start + _IN_CLAUSE_CHUNK_SIZE]
                for o in scan_db.query(HistoricalSetupOutcomeORM).filter(HistoricalSetupOutcomeORM.fingerprint_id.in_(chunk)).all():
                    outcome_map[o.fingerprint_id] = o

    trades_processed = 0
    states_written = 0
    errors = 0
    t0 = time.perf_counter()
    db = SessionLocal()
    pending = 0
    try:
        for fp in fingerprints:
            try:
                outcome = outcome_map.get(fp.fingerprint_id)
                if outcome is None or outcome.resolution_status != "RESOLVED":
                    trades_processed += 1
                    continue
                written = await backfill_trade(fp, outcome, db=db, compute_structure=compute_structure)
                states_written += written
                pending += written
                trades_processed += 1
            except Exception as exc:
                errors += 1
                logger.warning("adaptive_backfill: trade %s failed: %s", fp.fingerprint_id, exc.__class__.__name__)
                db.rollback()

            if pending >= commit_batch_size:
                db.commit()
                pending = 0

            if progress_every and trades_processed % progress_every == 0:
                elapsed = time.perf_counter() - t0
                logger.info("adaptive_backfill progress: trades=%d states=%d errors=%d elapsed=%.1fs", trades_processed, states_written, errors, elapsed)

        db.commit()
    finally:
        db.close()

    elapsed = time.perf_counter() - t0
    return {
        "anchor_strategy": anchor_strategy, "canonical_symbol": canonical_symbol, "provider": provider, "resumed_from": resumed_from,
        "trades_processed": trades_processed, "states_written": states_written, "errors": errors,
        "elapsed_seconds": round(elapsed, 2),
        "avg_ms_per_trade": round((elapsed / trades_processed) * 1000, 2) if trades_processed else None,
    }
