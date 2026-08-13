"""Bulk, chronological driver over replay.py::replay_at (Part 8/9 -- corpus-expansion directive).

The point-in-time replay engine (replay.py), fingerprint builder (fingerprint.py), and outcome
labeler (outcomes.py) already existed from an earlier directive and are reused here completely
unmodified -- this module ONLY adds the missing piece: walking forward through REAL historical
M15 instants and, for every strategy that would have fired, persisting a fingerprint + outcome.

Never invents which instants to replay: callers supply an explicit [start, end) window bounded by
REAL, VERIFIED trustworthy coverage (see the Phase 1 data inventory) -- this module has no
"assume N years" default. Idempotent: fingerprint ids are deterministic on (canonical_symbol, at,
strategy_id), so re-running over a previously-replayed window is a no-op for those instants
(matches pattern_builder.py's own idempotency convention). Runs strictly offline/background --
never imported by the live M5 cycle path (backend/brokers/mt5/autonomous.py never imports this
module).
"""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from backend.historical_intelligence import outcomes
from backend.historical_intelligence.fingerprint import build_fingerprint
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM
from backend.historical_intelligence.replay import (
    STRATEGY_REPLAY_VERSION, _MIN_BARS, _ReplayQuote, _replay_mtfai1, bars_as_of,
    build_strategy_context, evaluate_all, replay_at, required_lookback,
)
from backend.market_structure.engine import analyze_bars
from backend.mt5_strategies.fusion import build_candidates
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

_STRIDE_DEFAULT = timedelta(minutes=15)
_DEFAULT_COMMIT_BATCH_SIZE = 25


def _fingerprint_id(canonical_symbol: str, at: datetime, strategy_id: str) -> str:
    return "HPF_" + hashlib.sha256(f"BULK:{canonical_symbol}:{at.isoformat()}:{strategy_id}".encode()).hexdigest()[:40]


def _extract_geometry(replay_result: dict[str, Any], strategy_id: str) -> dict[str, Any] | None:
    """Mirrors pattern_builder.py::_extract_candidate_geometry exactly (same source data shape,
    replay_at's return dict is deliberately shaped like replay_for_evaluation's)."""
    if strategy_id == "mtfai1":
        mtfai1 = replay_result.get("mtfai1") or {}
        if mtfai1.get("direction") in (None, "NO_TRADE"):
            return None
        return {
            "entry": mtfai1.get("entry") or mtfai1.get("proposed_entry"),
            "stop_loss": mtfai1.get("stop_loss") or mtfai1.get("proposed_stop_loss"),
            "take_profit": mtfai1.get("take_profit") or mtfai1.get("proposed_take_profit"),
            "direction": mtfai1.get("direction"),
        }
    for candidate in replay_result.get("families_candidates") or []:
        candidate_strategy = candidate.get("context", {}).get("strategy_id") or candidate.get("strategy_id")
        if candidate_strategy != strategy_id:
            continue
        return {
            "entry": candidate.get("entry") or candidate.get("proposed_entry"),
            "stop_loss": candidate.get("stop_loss") or candidate.get("proposed_stop_loss"),
            "take_profit": candidate.get("take_profit") or candidate.get("proposed_take_profit"),
            "direction": candidate.get("direction"),
        }
    return None


def _candidate_strategy_ids(replay_result: dict[str, Any]) -> list[str]:
    ids = []
    mtfai1 = replay_result.get("mtfai1") or {}
    if mtfai1.get("direction") not in (None, "NO_TRADE"):
        ids.append("mtfai1")
    for candidate in replay_result.get("families_candidates") or []:
        sid = candidate.get("context", {}).get("strategy_id") or candidate.get("strategy_id")
        if sid:
            ids.append(sid)
    return ids


async def _persist_occurrence(
    *, canonical_symbol: str, broker_symbol: str, at: datetime, strategy_id: str,
    replay_result: dict[str, Any], provider: str, label_outcome: bool,
) -> bool:
    ctx = replay_result.get("_ctx")
    if ctx is None:
        return False
    geometry = _extract_geometry(replay_result, strategy_id)
    if geometry is None or geometry["entry"] is None or geometry["stop_loss"] is None or geometry["take_profit"] is None:
        return False

    quote = replay_result.get("_quote")
    real_spread = float(quote.spread) if (replay_result.get("source") == "SNAPSHOT" and quote is not None) else None

    fields = build_fingerprint(
        ctx=ctx, strategy_id=strategy_id, contributing_strategies=[strategy_id], strategy_family=None,
        strategy_version=STRATEGY_REPLAY_VERSION, source_quality_tier=replay_result["source"], provider=provider, proxy=False,
        entry=float(geometry["entry"]), stop_loss=float(geometry["stop_loss"]), take_profit=float(geometry["take_profit"]),
        entry_time=at, confidence_band=None, real_spread=real_spread,
    )

    fingerprint_id = _fingerprint_id(canonical_symbol, at, strategy_id)
    with SessionLocal() as db:
        row = db.get(HistoricalPatternFingerprintORM, fingerprint_id)
        already_existed = row is not None
        if row is None:
            row = HistoricalPatternFingerprintORM(fingerprint_id=fingerprint_id, source_evaluation_id=None, created_at=datetime.now(timezone.utc))
            db.add(row)
        for key, value in fields.items():
            setattr(row, key, value)
        db.commit()

    if label_outcome and not already_existed:
        outcomes.label_outcome(
            fingerprint_id=fingerprint_id, canonical_symbol=canonical_symbol, broker_symbol=broker_symbol,
            direction=fields["direction"], entry=fields["entry"], stop_loss=fields["stop_loss"], take_profit=fields["take_profit"],
            entry_time=fields["entry_time"], provider=provider, real_spread=real_spread,
        )
    return not already_existed


def _persist_occurrence_batched(
    db: Any, *, canonical_symbol: str, broker_symbol: str, at: datetime, strategy_id: str,
    replay_result: dict[str, Any], provider: str,
) -> bool:
    """Same logic as _persist_occurrence, but writes onto a CALLER-OWNED session with no commit
    -- Phase 8 (corpus-expansion-throughput directive): profiling proved the per-row commit
    round-trip (not the actual computation) was ~71% of total replay time. Returns True if this
    was a genuinely new occurrence (mirrors _persist_occurrence's return contract exactly)."""
    ctx = replay_result.get("_ctx")
    if ctx is None:
        return False
    geometry = _extract_geometry(replay_result, strategy_id)
    if geometry is None or geometry["entry"] is None or geometry["stop_loss"] is None or geometry["take_profit"] is None:
        return False

    quote = replay_result.get("_quote")
    real_spread = float(quote.spread) if (replay_result.get("source") == "SNAPSHOT" and quote is not None) else None

    fields = build_fingerprint(
        ctx=ctx, strategy_id=strategy_id, contributing_strategies=[strategy_id], strategy_family=None,
        strategy_version=STRATEGY_REPLAY_VERSION, source_quality_tier=replay_result["source"], provider=provider, proxy=False,
        entry=float(geometry["entry"]), stop_loss=float(geometry["stop_loss"]), take_profit=float(geometry["take_profit"]),
        entry_time=at, confidence_band=None, real_spread=real_spread,
    )

    fingerprint_id = _fingerprint_id(canonical_symbol, at, strategy_id)
    row = db.get(HistoricalPatternFingerprintORM, fingerprint_id)
    already_existed = row is not None
    if row is None:
        row = HistoricalPatternFingerprintORM(fingerprint_id=fingerprint_id, source_evaluation_id=None, created_at=datetime.now(timezone.utc))
        db.add(row)
    for key, value in fields.items():
        setattr(row, key, value)

    if not already_existed:
        outcomes.label_outcome(
            fingerprint_id=fingerprint_id, canonical_symbol=canonical_symbol, broker_symbol=broker_symbol,
            direction=fields["direction"], entry=fields["entry"], stop_loss=fields["stop_loss"], take_profit=fields["take_profit"],
            entry_time=fields["entry_time"], provider=provider, real_spread=real_spread, db=db,
        )
    return not already_existed


def _checkpoint(canonical_symbol: str, window_start: datetime, window_end: datetime) -> datetime | None:
    """Restart-safety (Phase 8): the deterministic BULK: fingerprint_id namespace means every
    instant this driver has ever persisted for this symbol is already recorded in
    HistoricalPatternFingerprintORM.entry_time -- resuming from the max entry_time already
    covered inside [window_start, window_end) avoids re-walking (and re-paying the ~7s/instant
    replay cost for) instants a prior, interrupted run already finished, while still allowing a
    DIFFERENT window on the same symbol to run independently."""
    with SessionLocal() as db:
        latest = (
            db.query(HistoricalPatternFingerprintORM.entry_time)
            .filter(
                HistoricalPatternFingerprintORM.canonical_symbol == canonical_symbol,
                HistoricalPatternFingerprintORM.source_evaluation_id.is_(None),
                HistoricalPatternFingerprintORM.entry_time >= window_start,
                HistoricalPatternFingerprintORM.entry_time < window_end,
            )
            .order_by(HistoricalPatternFingerprintORM.entry_time.desc())
            .first()
        )
    return latest[0] if latest else None


async def replay_symbol_history(
    *, canonical_symbol: str, broker_symbol: str, start: datetime, end: datetime,
    provider: str = "MT5", stride: timedelta = _STRIDE_DEFAULT, strategy_ids: list[str] | None = None,
    max_instants: int | None = None, progress_every: int = 200, resume: bool = True,
) -> dict[str, Any]:
    """Walks `at` from `start` to `end` (exclusive) in `stride` steps, calling replay_at at each
    instant and persisting a fingerprint + outcome for every strategy that would genuinely have
    fired. `strategy_ids`, when given, restricts which fired strategies get persisted (still runs
    the full production evaluate_all/_score_candidate pass regardless -- this only filters what
    gets written, never what gets evaluated, so a NO_TRADE-family result is never miscounted).
    `resume` (default True): if a prior run already covered a prefix of [start, end) for this
    symbol, starts from just after the last covered instant instead of re-walking from `start`.
    Returns real counts: instants walked, insufficient-history skips, occurrences newly
    persisted, occurrences already-existing (idempotent no-op), and wall-clock timing."""
    at = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)

    resumed_from = None
    if resume:
        checkpoint = _checkpoint(canonical_symbol, at, end)
        if checkpoint is not None:
            checkpoint = checkpoint if checkpoint.tzinfo else checkpoint.replace(tzinfo=timezone.utc)
            candidate_at = checkpoint + stride
            if candidate_at > at:
                at = candidate_at
                resumed_from = checkpoint.isoformat()

    instants_walked = 0
    insufficient_history = 0
    occurrences_new = 0
    occurrences_existing = 0
    errors = 0
    t0 = time.perf_counter()

    while at < end:
        if max_instants is not None and instants_walked >= max_instants:
            break
        instants_walked += 1
        try:
            result = await replay_at(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, at=at, provider=provider)
        except Exception as exc:
            errors += 1
            logger.warning("bulk_replay: replay_at failed for %s at %s: %s", canonical_symbol, at.isoformat(), exc.__class__.__name__)
            at += stride
            continue

        if result.get("status") != "OK":
            insufficient_history += 1
            at += stride
            continue

        fired = _candidate_strategy_ids(result)
        if strategy_ids is not None:
            fired = [sid for sid in fired if sid in strategy_ids]

        for strategy_id in fired:
            try:
                is_new = await _persist_occurrence(
                    canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, at=at, strategy_id=strategy_id,
                    replay_result=result, provider=provider, label_outcome=True,
                )
                if is_new:
                    occurrences_new += 1
                else:
                    occurrences_existing += 1
            except Exception as exc:
                errors += 1
                logger.warning("bulk_replay: persist failed for %s/%s at %s: %s", canonical_symbol, strategy_id, at.isoformat(), exc.__class__.__name__)

        if progress_every and instants_walked % progress_every == 0:
            elapsed = time.perf_counter() - t0
            logger.info(
                "bulk_replay progress %s: walked=%d new=%d existing=%d insufficient=%d errors=%d elapsed=%.1fs",
                canonical_symbol, instants_walked, occurrences_new, occurrences_existing, insufficient_history, errors, elapsed,
            )

        at += stride

    elapsed = time.perf_counter() - t0
    return {
        "canonical_symbol": canonical_symbol,
        "start": start.isoformat(), "end": end.isoformat(), "stride_seconds": stride.total_seconds(),
        "resumed_from": resumed_from,
        "instants_walked": instants_walked, "insufficient_history": insufficient_history,
        "occurrences_new": occurrences_new, "occurrences_existing": occurrences_existing, "errors": errors,
        "elapsed_seconds": round(elapsed, 2),
        "avg_ms_per_instant": round((elapsed / instants_walked) * 1000, 2) if instants_walked else None,
    }


async def replay_symbol_history_fast(
    *, canonical_symbol: str, broker_symbol: str, start: datetime, end: datetime,
    provider: str = "MT5", stride: timedelta = _STRIDE_DEFAULT, strategy_ids: list[str] | None = None,
    max_instants: int | None = None, progress_every: int = 200, resume: bool = True,
    commit_batch_size: int = _DEFAULT_COMMIT_BATCH_SIZE,
) -> dict[str, Any]:
    """Optimized twin of replay_symbol_history (corpus-expansion-THROUGHPUT directive, Phases
    4-8). Produces IDENTICAL fingerprint/outcome field values for the same instant (proven by
    test_historical_intelligence_bulk_replay_fast.py's parity tests against the slow path on a
    fixed sample) -- only HOW work is done changes, never WHAT is computed:

    1. (Phase 8) Writes go onto ONE session held open for up to `commit_batch_size` new
       occurrences, committed once per batch instead of once per row. Profiling
       (scratchpad/profile_replay.py, 150 real USDJPY instants) measured this as ~71% of total
       replay time -- almost entirely per-row commit/round-trip overhead, not real computation.
    2. (Phase 4/5) H1 and H4 structure snapshots (analyze_bars -- the expensive BOS/CHoCH/MSS/
       swing computation inside build_strategy_context) are cached and reused across consecutive
       M15 instants for AS LONG AS the underlying H1/H4 bar window's last bar hasn't changed --
       H1 genuinely only changes once every 4 M15 instants, H4 once every 16. This is provably
       exact-parity, not an approximation: build_strategy_context's own docstring guarantees a
       caller-supplied snapshot is "byte-identical to what recomputing it here would produce"
       since analyze_bars is a pure function of its input rows. M15's own snapshot is NEVER
       cached -- it is recomputed fresh every single instant, since M15 structure genuinely
       changes every instant and that IS what strategies evaluate against.
    3. Reuses replay.py's bars_as_of/build_strategy_context/evaluate_all/_replay_mtfai1 and
       mt5_strategies.fusion.build_candidates completely unmodified -- this function only changes
       persistence batching and snapshot reuse around those same, real, unmodified calls.

    A crash mid-batch loses at most `commit_batch_size` instants of uncommitted work (never
    corrupts or duplicates anything -- fingerprint_id is deterministic, so a resume simply
    re-walks and re-persists those instants). Restart-safety is otherwise identical to
    replay_symbol_history (same _checkpoint mechanism, same idempotent fingerprint id scheme)."""
    at = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    end = end if end.tzinfo else end.replace(tzinfo=timezone.utc)

    resumed_from = None
    if resume:
        checkpoint = _checkpoint(canonical_symbol, at, end)
        if checkpoint is not None:
            checkpoint = checkpoint if checkpoint.tzinfo else checkpoint.replace(tzinfo=timezone.utc)
            candidate_at = checkpoint + stride
            if candidate_at > at:
                at = candidate_at
                resumed_from = checkpoint.isoformat()

    instants_walked = 0
    insufficient_history = 0
    occurrences_new = 0
    occurrences_existing = 0
    errors = 0
    t0 = time.perf_counter()

    h1_cache: tuple[datetime, Any] | None = None
    h4_cache: tuple[datetime, Any] | None = None

    db = SessionLocal()
    pending = 0
    try:
        while at < end:
            if max_instants is not None and instants_walked >= max_instants:
                break
            instants_walked += 1
            try:
                m15 = await bars_as_of(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, timeframe="M15", at=at, count=required_lookback(timeframe="M15"), provider=provider)
                h1 = await bars_as_of(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, timeframe="H1", at=at, count=required_lookback(timeframe="H1"), provider=provider)
                h4 = await bars_as_of(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, timeframe="H4", at=at, count=required_lookback(timeframe="H4"), provider=provider)
            except Exception as exc:
                errors += 1
                logger.warning("bulk_replay_fast: bars_as_of failed for %s at %s: %s", canonical_symbol, at.isoformat(), exc.__class__.__name__)
                at += stride
                continue

            if len(m15) < _MIN_BARS["M15"] or len(h1) < _MIN_BARS["H1"] or len(h4) < _MIN_BARS["H4"]:
                insufficient_history += 1
                at += stride
                continue

            last_close = m15[-1].close
            quote = _ReplayQuote(bid=last_close, ask=last_close, spread=Decimal("0"))

            try:
                mtfai1_result = _replay_mtfai1(quote, m15, h1, h4, None)

                m15_rows = [c.model_dump(mode="json") for c in m15]
                h1_rows = [c.model_dump(mode="json") for c in h1]
                h4_rows = [c.model_dump(mode="json") for c in h4]

                h1_last_time = h1[-1].time
                if h1_cache is not None and h1_cache[0] == h1_last_time:
                    h1_snapshot = h1_cache[1]
                else:
                    h1_snapshot = analyze_bars(h1_rows, symbol=broker_symbol, timeframe="H1")
                    h1_cache = (h1_last_time, h1_snapshot)

                h4_last_time = h4[-1].time
                if h4_cache is not None and h4_cache[0] == h4_last_time:
                    h4_snapshot = h4_cache[1]
                else:
                    h4_snapshot = analyze_bars(h4_rows, symbol=broker_symbol, timeframe="H4")
                    h4_cache = (h4_last_time, h4_snapshot)

                ctx = build_strategy_context(
                    symbol=canonical_symbol, broker_symbol=broker_symbol,
                    m15_rows=m15_rows, h1_rows=h1_rows, h4_rows=h4_rows,
                    bid=quote.bid, ask=quote.ask, spread=quote.spread,
                    now=at, h1_snapshot=h1_snapshot, h4_snapshot=h4_snapshot,
                )
            except Exception as exc:
                errors += 1
                logger.warning("bulk_replay_fast: context build failed for %s at %s: %s", canonical_symbol, at.isoformat(), exc.__class__.__name__)
                at += stride
                continue

            if ctx is None:
                insufficient_history += 1
                at += stride
                continue

            signals = evaluate_all(ctx)
            candidates = build_candidates(symbol=canonical_symbol, broker_symbol=broker_symbol, asset_class="FOREX", cycle_id=f"REPLAY_FAST:{canonical_symbol}:{at.isoformat()}", signals=signals, htf_trend_h4=ctx.htf_trend_h4, now=at)

            result = {
                "status": "OK", "source": "RECONSTRUCTED", "mtfai1": mtfai1_result, "families_candidates": candidates,
                "_ctx": ctx, "_quote": quote,
            }

            fired = _candidate_strategy_ids(result)
            if strategy_ids is not None:
                fired = [sid for sid in fired if sid in strategy_ids]

            for strategy_id in fired:
                try:
                    is_new = _persist_occurrence_batched(
                        db, canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, at=at, strategy_id=strategy_id,
                        replay_result=result, provider=provider,
                    )
                    if is_new:
                        occurrences_new += 1
                        pending += 1
                    else:
                        occurrences_existing += 1
                except Exception as exc:
                    errors += 1
                    logger.warning("bulk_replay_fast: persist failed for %s/%s at %s: %s", canonical_symbol, strategy_id, at.isoformat(), exc.__class__.__name__)
                    db.rollback()

            if pending >= commit_batch_size:
                db.commit()
                pending = 0

            if progress_every and instants_walked % progress_every == 0:
                elapsed = time.perf_counter() - t0
                logger.info(
                    "bulk_replay_fast progress %s: walked=%d new=%d existing=%d insufficient=%d errors=%d elapsed=%.1fs",
                    canonical_symbol, instants_walked, occurrences_new, occurrences_existing, insufficient_history, errors, elapsed,
                )

            at += stride

        db.commit()
    finally:
        db.close()

    elapsed = time.perf_counter() - t0
    return {
        "canonical_symbol": canonical_symbol,
        "start": start.isoformat(), "end": end.isoformat(), "stride_seconds": stride.total_seconds(),
        "resumed_from": resumed_from,
        "instants_walked": instants_walked, "insufficient_history": insufficient_history,
        "occurrences_new": occurrences_new, "occurrences_existing": occurrences_existing, "errors": errors,
        "elapsed_seconds": round(elapsed, 2),
        "avg_ms_per_instant": round((elapsed / instants_walked) * 1000, 2) if instants_walked else None,
    }
