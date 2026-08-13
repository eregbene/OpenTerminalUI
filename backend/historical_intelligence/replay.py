"""Point-in-time replay of Bensim's REAL production MT5 strategy logic (Part 6).

This module NEVER reimplements a strategy. Every signal-generating call below is a direct,
unmodified import of the exact function the live autonomous entry engine calls in
backend/brokers/mt5/autonomous.py::_build_multi_strategy_analysis / MT5AutonomousTradingService.
_score_candidate:

    backend.mt5_strategies.context.build_strategy_context   (pure feature context, no I/O)
    backend.mt5_strategies.context.summarize_smc_evidence
    backend.mt5_strategies.families.evaluate_all             (all 11 strategy evaluators)
    backend.mt5_strategies.fusion.build_candidates           (same-direction fusion / conflict)
    backend.brokers.mt5.autonomous._score_candidate          (MTFAI1's own scoring)

SOURCE HIERARCHY (Part 7 of the point-in-time integrity fix) -- exact parity verification must
use, in order:
  1. MT5DecisionSnapshotORM  -- the EXACT m15/h1/h4 rows + bid/ask/regime the live engine used
     for one specific candidate, captured at generation time (see snapshot_capture.py). No
     timestamp reconstruction, immune to both bugs below. `replay_from_snapshot()`.
  2. MT5CandleRevisionORM as observed at decision time -- for replay against an instant that has
     no snapshot, the latest revision of each bar that was ALREADY KNOWN (observed_at <= the
     replay instant), or, if the bar is old enough to be finalized by that instant, the latest
     known revision regardless of when it was captured (a settled bar's value does not depend on
     when we happened to fetch it). `bars_as_of()`, tier 2.
  3. Finalized MT5CanonicalCandleORM rows with no revision history at all -- legacy data ingested
     before revision-tracking existed. `bars_as_of()`, tier 3 fallback.
These are never mixed silently: every returned replay dict carries an explicit "source" field
(SNAPSHOT | RECONSTRUCTED) and bars_as_of's own per-bar resolution is documented above.

Two confirmed, now-fixed bugs this hierarchy exists to correct (see quality.py / ingestion.py /
orm.py docstrings for full detail):
  - Bar-overwrite: mt5_canonical_candles was upserted via db.merge() on every re-fetch with no
    history retained, silently discarding the OHLC the live engine actually saw. Fixed by
    MT5CandleRevisionORM (append-only) + finalized-bar protection in ingestion.py.
  - Broker-server-time vs UTC: MT5 bar timestamps are broker-server time (confirmed empirically,
    2026-08-12: UTC+3 on this deployment's demo server), but the OLD version of this module
    compared them directly against true-UTC MT5CandidateEvaluationORM.created_at timestamps,
    silently shifting the point-in-time bar-selection window by 3 hours. Fixed by
    bar_timestamp_utc (offset-corrected at ingestion time) being the ONLY column used for
    point-in-time comparisons below -- the raw provider-labeled timestamp is never compared
    against a UTC instant anywhere in this module.

Strict no-lookahead is enforced in ONE place -- `bars_as_of()` -- which only ever returns bars
whose full interval has already closed as of the requested instant (`bar_timestamp_utc +
timeframe <= at`, never merely `<= at`, which would leak the still-forming/most-recent bar's own
close), AND whose value was either genuinely knowable at `at` (observed_at <= at) or safely
presumed settled (finalized as of `at`) -- never a revision captured after `at` for a bar that
was still ambiguous at `at`. See test_historical_intelligence_replay.py::
test_bars_as_of_never_returns_future_data and ::test_bars_as_of_never_uses_not_yet_known_revision.

Known, explicitly-documented approximations (this is real strategy logic on REAL historical
prices, but two live-only inputs cannot be reconstructed from OHLC candle history alone):
  - bid/ask: approximated as the most recently closed M15 bar's close price (spread=0) unless
    the caller supplies a real historical tick or a decision snapshot (which carries the real
    bid/ask/spread captured at decision time). This affects entry price PRECISION only -- never
    which strategies fire or their structural (BOS/CHoCH/SMC) evidence, and is never faked as a
    real historical spread (Part 6 of the integrity spec: mark it approximate, keep it separate
    from structural/signal parity).
  - symbol_info (broker stops-level/point): reused from CURRENT broker metadata when the caller
    doesn't supply a historical value, since this field changes rarely.

This module makes no broker calls and writes nothing to any live table -- it only reads
mt5_canonical_candles / mt5_candle_revisions / mt5_decision_snapshots and returns data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from backend.brokers.mt5.models import MT5Candle
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM, MT5CanonicalCandleORM
from backend.historical_intelligence.orm import MT5CandleRevisionORM, MT5DecisionSnapshotORM
from backend.historical_intelligence.quality import is_finalized
from backend.mt5_strategies.context import build_strategy_context, summarize_smc_evidence
from backend.mt5_strategies.families import evaluate_all
from backend.mt5_strategies.fusion import build_candidates
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

# Bumped whenever this module's approximations or call sequence change in a way that could alter
# replay output -- downstream statistics/cache keys (later phases) must key on this, per the
# explicit "changing strategy logic must not consume statistics generated by an older
# implementation" requirement. NOT bumped for changes to mt5_strategies itself (that module's
# own logic has no separate version here -- replay always calls whatever is currently deployed,
# by design, since the whole point is parity with live).
STRATEGY_REPLAY_VERSION = "replay-v2-point-in-time-integrity"

_TIMEFRAME_DELTAS = {"M5": timedelta(minutes=5), "M15": timedelta(minutes=15), "H1": timedelta(hours=1), "H4": timedelta(hours=4)}
_MIN_BARS = {"M15": 60, "H1": 50, "H4": 30}  # mirrors autonomous.py::_screen's own thresholds

# Part 8: strategy-declared minimum history, rather than one hardcoded window fetched
# unconditionally for every replay. Verified against the LIVE path's own fetch counts
# (backend/brokers/mt5/autonomous.py::_screen, `redis_layer.cached_candles(..., count=100)` for
# all of M15/H1/H4 -- confirmed by reading that call site directly, not assumed) -- every current
# strategy family already gets exactly 100/100/100 live, so this registry does not change what
# gets fetched today; it exists so a FUTURE strategy that genuinely needs a different window
# (e.g. a slower-moving EMA, or a strategy needing deeper H4 history) can declare it explicitly
# here rather than requiring a silent, easy-to-miss edit to the replay fetch call itself. Do not
# blindly raise these to "be safe" -- a window wider than what live actually used reintroduces
# exactly the kind of live/replay mismatch this module exists to eliminate.
STRATEGY_LOOKBACK: dict[str, dict[str, int]] = {
    "mtfai1": {"M15": 100, "H1": 100, "H4": 100},
    "ema_trend": {"M15": 100, "H1": 100, "H4": 100},  # needs the full 100 M15 closes for EMA100
    "trend_pullback": {"M15": 100, "H1": 100, "H4": 100},
    "breakout": {"M15": 100, "H1": 100, "H4": 100},
    "mean_reversion": {"M15": 100, "H1": 100, "H4": 100},
    "liquidity_sweep_reversal": {"M15": 100, "H1": 100, "H4": 100},
    "smc_continuation": {"M15": 100, "H1": 100, "H4": 100},
    "support_resistance_bounce": {"M15": 100, "H1": 100, "H4": 100},
    "momentum": {"M15": 100, "H1": 100, "H4": 100},
    "session_breakout": {"M15": 100, "H1": 100, "H4": 100},
    "vwap_reversion": {"M15": 100, "H1": 100, "H4": 100},
}
_DEFAULT_LOOKBACK = {"M15": 100, "H1": 100, "H4": 100}


def required_lookback(*, timeframe: str, strategy_ids: list[str] | None = None) -> int:
    """The number of bars replay must fetch for `timeframe` to satisfy every strategy in
    `strategy_ids` (all registered strategies when omitted) -- the MAX across their individual
    declarations, since a single fetched window is shared by every strategy evaluated against a
    given replay instant (matching live's own single-fetch-per-timeframe-per-cycle design)."""
    tf = timeframe.upper()
    ids = strategy_ids if strategy_ids is not None else list(STRATEGY_LOOKBACK.keys())
    requirements = [STRATEGY_LOOKBACK.get(sid, _DEFAULT_LOOKBACK).get(tf, _DEFAULT_LOOKBACK[tf]) for sid in ids if sid in STRATEGY_LOOKBACK or True]
    return max(requirements, default=_DEFAULT_LOOKBACK[tf])


SOURCE_SNAPSHOT = "SNAPSHOT"
SOURCE_RECONSTRUCTED = "RECONSTRUCTED"


@dataclass(frozen=True)
class _ReplayQuote:
    bid: Decimal
    ask: Decimal
    spread: Decimal


def _ensure_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_offset_cache: dict[str, tuple[int, datetime]] = {}
_OFFSET_CACHE_TTL = timedelta(hours=1)


async def _resolve_broker_offset_minutes(provider: str) -> int:
    """Resolves the broker-server-time-to-UTC offset (minutes) used to correct LEGACY canonical
    rows that have no bar_timestamp_utc at all -- critically, this includes every row written by
    the LIVE trading path's own persist_candles() (backend/brokers/mt5/persistence.py), which
    never sets timestamp_utc/broker_utc_offset_minutes/finalized and is the dominant writer of
    mt5_canonical_candles (confirmed: 100% of this deployment's EURUSD M15 canonical rows have
    timestamp_utc IS NULL, 2026-08-12). Without this correction, tier-3 fallback would silently
    reproduce the exact broker-clock-vs-UTC bug this module exists to fix, for nearly the entire
    live-written bar history.

    Prefers the most recently recorded MT5CandleRevisionORM.broker_utc_offset_minutes (DB-only,
    consistent with this module's "makes no broker calls" design); falls back to ONE live broker
    call via MT5HistoricalProvider.detect_utc_offset_minutes() only when no revision has ever
    been recorded yet for this provider (a bootstrap-only path -- becomes unnecessary once any
    historical_intelligence ingestion run has occurred). Cached in-process for _OFFSET_CACHE_TTL
    since a broker's offset is stable intra-day (it only moves across DST transitions)."""
    now = datetime.now(timezone.utc)
    cached = _offset_cache.get(provider.upper())
    if cached and (now - cached[1]) < _OFFSET_CACHE_TTL:
        return cached[0]

    offset = 0
    with SessionLocal() as db:
        latest = (
            db.query(MT5CandleRevisionORM.broker_utc_offset_minutes)
            .filter(MT5CandleRevisionORM.provider == provider.upper())
            .order_by(MT5CandleRevisionORM.created_at.desc())
            .first()
        )
    if latest is not None:
        offset = int(latest[0])
    elif provider.upper() == "MT5":
        try:
            from backend.brokers.mt5.adapter import mt5_adapter
            from backend.historical_intelligence.providers.mt5_provider import MT5HistoricalProvider

            offset = await MT5HistoricalProvider(mt5_adapter).detect_utc_offset_minutes()
        except Exception as exc:
            logger.warning("Broker UTC offset bootstrap detection failed for provider=%s, defaulting to 0: %s", provider, exc.__class__.__name__)
            offset = 0

    _offset_cache[provider.upper()] = (offset, now)
    return offset


def _row_to_candle(canonical_symbol: str, timeframe: str, bar_time_utc: datetime, row: Any) -> MT5Candle:
    """Builds an MT5Candle from either an MT5CandleRevisionORM or an MT5CanonicalCandleORM row
    -- both expose the same open/high/low/close/tick_volume/spread/real_volume/provider fields,
    so a single conversion covers tier 2 and tier 3 of the source hierarchy."""
    return MT5Candle(
        symbol=canonical_symbol, timeframe=timeframe,
        time=bar_time_utc,
        open=Decimal(str(row.open)), high=Decimal(str(row.high)), low=Decimal(str(row.low)), close=Decimal(str(row.close)),
        tick_volume=row.tick_volume, spread=row.spread, real_volume=row.real_volume,
        complete=True, source=row.provider,
    )


async def bars_as_of(*, canonical_symbol: str, broker_symbol: str, timeframe: str, at: datetime, count: int, provider: str = "MT5") -> list[MT5Candle]:
    """STRICT no-lookahead gate implementing source-hierarchy tiers 2 and 3 (tier 1, decision
    snapshots, is handled separately by replay_from_snapshot -- callers needing exact parity
    should try that first). Returns up to `count` bars, ascending by time, resolved bar-by-bar as
    follows:

      - Only bars whose full interval has closed by `at` are eligible: `bar_timestamp_utc +
        timeframe_delta <= at`.
      - For each eligible bar timestamp, among its revisions: prefer the latest one with
        `observed_at <= at` (what was actually knowable at that instant). If none exists (the
        normal case for history backfilled long after the fact) AND the bar is finalized as of
        `at` (quality.is_finalized), use the latest revision regardless of observed_at -- a
        settled bar's value does not depend on fetch timing. If neither condition holds, the bar
        is EXCLUDED (no look-ahead-safe answer exists yet), never guessed.
      - Bars with no revision history at all (ingested before revision-tracking existed) fall
        back to the canonical table directly, again only once finalized as of `at`.

    Excludes bars flagged INVALID by ingestion quality checks; SUSPECT bars are included
    (flagged, not silently dropped)."""
    at = _ensure_utc(at)
    tf = timeframe.upper()
    delta = _TIMEFRAME_DELTAS.get(tf, timedelta(minutes=15))
    cutoff_utc = at - delta
    legacy_offset_minutes = await _resolve_broker_offset_minutes(provider)
    legacy_offset = timedelta(minutes=legacy_offset_minutes)

    resolved: dict[datetime, Any] = {}
    with SessionLocal() as db:
        revision_rows = (
            db.query(MT5CandleRevisionORM)
            .filter(
                MT5CandleRevisionORM.provider == provider.upper(),
                MT5CandleRevisionORM.broker_symbol == broker_symbol.upper(),
                MT5CandleRevisionORM.timeframe == tf,
                MT5CandleRevisionORM.bar_timestamp_utc <= cutoff_utc,
            )
            .order_by(MT5CandleRevisionORM.bar_timestamp_utc.desc())
            .limit(count * 12)  # generous headroom: several revisions can exist per bar
            .all()
        )
        by_bar: dict[datetime, list[MT5CandleRevisionORM]] = {}
        for r in revision_rows:
            by_bar.setdefault(_ensure_utc(r.bar_timestamp_utc), []).append(r)

        for bar_time_utc, revs in by_bar.items():
            revs_sorted = sorted(revs, key=lambda r: r.revision_number)
            known_at_t = [r for r in revs_sorted if _ensure_utc(r.observed_at) <= at]
            if known_at_t:
                resolved[bar_time_utc] = known_at_t[-1]
            elif is_finalized(bar_time_utc, tf, now=at):
                resolved[bar_time_utc] = revs_sorted[-1]
            # else: genuinely ambiguous at instant `at` and no revision was captured in time --
            # excluded rather than guessed.

        if len(resolved) < count:
            # Tier 3 fallback: legacy canonical rows with no revision history at all -- this is
            # actually the DOMINANT case in practice, since the live trading path's own
            # persist_candles() writes directly to mt5_canonical_candles and never sets
            # timestamp_utc (confirmed: 100% of this deployment's live-written rows have
            # timestamp_utc IS NULL). The SQL bound below is deliberately widened by
            # legacy_offset (rather than filtering precisely in SQL against the raw,
            # broker-labeled `timestamp` column) because that column is NOT reliably UTC for
            # these rows -- precise no-lookahead filtering happens below in Python against the
            # OFFSET-CORRECTED bar_time_utc instead. Only fills bar timestamps NOT already
            # resolved via revisions above.
            canonical_rows = (
                db.query(MT5CanonicalCandleORM)
                .filter(
                    MT5CanonicalCandleORM.provider == provider.upper(),
                    MT5CanonicalCandleORM.broker_symbol == broker_symbol.upper(),
                    MT5CanonicalCandleORM.timeframe == tf,
                    MT5CanonicalCandleORM.timestamp <= cutoff_utc + legacy_offset,
                    MT5CanonicalCandleORM.quality != "INVALID",
                )
                .order_by(MT5CanonicalCandleORM.timestamp.desc())
                .limit(count * 2)
                .all()
            )
            for row in canonical_rows:
                raw_utc = row.timestamp_utc or (_ensure_utc(row.timestamp) - legacy_offset)
                bar_time_utc = _ensure_utc(raw_utc)
                if bar_time_utc > cutoff_utc:
                    continue  # widened SQL bound can over-fetch; enforce the precise cutoff here
                if bar_time_utc in resolved:
                    continue
                if not is_finalized(bar_time_utc, tf, now=at):
                    continue
                resolved[bar_time_utc] = row

    bar_times_sorted = sorted(resolved.keys())[-count:]
    return [_row_to_candle(canonical_symbol, tf, bt, resolved[bt]) for bt in bar_times_sorted]


def _candle_from_snapshot_row(row: dict[str, Any]) -> MT5Candle:
    return MT5Candle(**row)


async def replay_from_snapshot(evaluation_id: str) -> dict[str, Any] | None:
    """Source-hierarchy tier 1: replays using the EXACT m15/h1/h4 rows and bid/ask/spread/regime
    captured by snapshot_capture.py at live decision time -- no timestamp reconstruction, so this
    path is immune to both the bar-overwrite issue and the broker-UTC-offset issue by
    construction. Returns None if no snapshot exists for this evaluation_id (caller should fall
    back to bars_as_of()-based reconstruction, tiers 2/3)."""
    with SessionLocal() as db:
        snap = db.query(MT5DecisionSnapshotORM).filter(MT5DecisionSnapshotORM.evaluation_id == evaluation_id).one_or_none()
        if snap is None:
            return None
        canonical_symbol, broker_symbol = snap.canonical_symbol, snap.broker_symbol
        m15_rows, h1_rows, h4_rows = snap.m15_rows, snap.h1_rows, snap.h4_rows
        bid, ask, spread = snap.bid, snap.ask, snap.spread
        at = _ensure_utc(snap.decision_at)

    if len(m15_rows) < _MIN_BARS["M15"] or len(h1_rows) < _MIN_BARS["H1"] or len(h4_rows) < _MIN_BARS["H4"]:
        return {
            "status": "INSUFFICIENT_HISTORY", "source": SOURCE_SNAPSHOT, "as_of": at.isoformat(),
            "bars": {"m15": len(m15_rows), "h1": len(h1_rows), "h4": len(h4_rows)},
        }

    m15 = [_candle_from_snapshot_row(r) for r in m15_rows]
    h1 = [_candle_from_snapshot_row(r) for r in h1_rows]
    h4 = [_candle_from_snapshot_row(r) for r in h4_rows]
    last_close = m15[-1].close
    resolved_quote = _ReplayQuote(
        bid=Decimal(str(bid)) if bid is not None else last_close,
        ask=Decimal(str(ask)) if ask is not None else last_close,
        spread=Decimal(str(spread)) if spread is not None else Decimal("0"),
    )

    return _run_replay_pipeline(
        canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, at=at,
        m15=m15, h1=h1, h4=h4, quote=resolved_quote, symbol_info=None,
        source=SOURCE_SNAPSHOT, cycle_id=f"REPLAY_SNAPSHOT:{evaluation_id}",
    )


async def replay_at(
    *,
    canonical_symbol: str,
    broker_symbol: str,
    at: datetime,
    provider: str = "MT5",
    symbol_info: Any = None,
    quote: _ReplayQuote | None = None,
) -> dict[str, Any]:
    """Source-hierarchy tiers 2/3: point-in-time replay of the full production pipeline at
    instant `at`, reconstructing bars via bars_as_of() rather than reading a decision snapshot.
    Used for broad historical replay (no specific live candidate to key a snapshot lookup on) and
    as the fallback when replay_from_snapshot() finds nothing. Returns a dict shaped closely
    enough to the live cycle's own candidate rows for direct parity comparison (see parity.py),
    but namespaced separately -- this NEVER writes into mt5_scheduler_candidates /
    mt5_candidate_evaluations."""
    at = _ensure_utc(at)
    m15 = await bars_as_of(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, timeframe="M15", at=at, count=required_lookback(timeframe="M15"), provider=provider)
    h1 = await bars_as_of(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, timeframe="H1", at=at, count=required_lookback(timeframe="H1"), provider=provider)
    h4 = await bars_as_of(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, timeframe="H4", at=at, count=required_lookback(timeframe="H4"), provider=provider)

    if len(m15) < _MIN_BARS["M15"] or len(h1) < _MIN_BARS["H1"] or len(h4) < _MIN_BARS["H4"]:
        return {"status": "INSUFFICIENT_HISTORY", "source": SOURCE_RECONSTRUCTED, "as_of": at.isoformat(), "bars": {"m15": len(m15), "h1": len(h1), "h4": len(h4)}}

    last_close = m15[-1].close
    resolved_quote = quote or _ReplayQuote(bid=last_close, ask=last_close, spread=Decimal("0"))

    return _run_replay_pipeline(
        canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, at=at,
        m15=m15, h1=h1, h4=h4, quote=resolved_quote, symbol_info=symbol_info,
        source=SOURCE_RECONSTRUCTED, cycle_id=f"REPLAY:{canonical_symbol}:{at.isoformat()}",
    )


async def replay_for_evaluation(evaluation_id: str, *, provider: str = "MT5") -> dict[str, Any]:
    """Top-level entry point for parity verification (parity.py) -- implements the full source
    hierarchy: tries the decision snapshot first (tier 1, exact); only reconstructs from
    canonical/revision history (tiers 2/3) when no snapshot was captured for this evaluation
    (e.g. it predates snapshot_capture.py's deployment)."""
    snapshot_result = await replay_from_snapshot(evaluation_id)
    if snapshot_result is not None:
        return snapshot_result

    with SessionLocal() as db:
        row = db.get(MT5CandidateEvaluationORM, evaluation_id)
        if row is None:
            return {"status": "LIVE_EVALUATION_NOT_FOUND", "source": "NONE"}
        symbol, broker_symbol = row.symbol, row.broker_symbol
        # Prefer market_data_as_of (the exact last-M15-bar instant live actually used) over
        # created_at (the cycle's post-decision DB-write time, which lags the real fetch by
        # however long prefilter/scoring/DB-write took) -- see migration 0053's docstring.
        # created_at + a tiny buffer is still a safe upper bound on when the bar was already
        # closed, so it remains a reasonable fallback for rows written before this column existed.
        as_of = row.market_data_as_of or row.created_at

    return await replay_at(canonical_symbol=symbol, broker_symbol=broker_symbol, at=as_of, provider=provider)


def _run_replay_pipeline(
    *,
    canonical_symbol: str, broker_symbol: str, at: datetime,
    m15: list[MT5Candle], h1: list[MT5Candle], h4: list[MT5Candle],
    quote: _ReplayQuote, symbol_info: Any, source: str, cycle_id: str,
) -> dict[str, Any]:
    m15_rows = [c.model_dump(mode="json") for c in m15]
    h1_rows = [c.model_dump(mode="json") for c in h1]
    h4_rows = [c.model_dump(mode="json") for c in h4]

    mtfai1_result = _replay_mtfai1(quote, m15, h1, h4, symbol_info)

    ctx = build_strategy_context(
        symbol=canonical_symbol, broker_symbol=broker_symbol,
        m15_rows=m15_rows, h1_rows=h1_rows, h4_rows=h4_rows,
        bid=quote.bid, ask=quote.ask, spread=quote.spread,
        now=at, symbol_info=symbol_info,
    )
    if ctx is None:
        return {
            "status": "OK", "source": source, "as_of": at.isoformat(), "regime": "insufficient_data", "smc_evidence": {},
            "mtfai1": mtfai1_result, "families_candidates": [], "bars": {"m15": len(m15), "h1": len(h1), "h4": len(h4)},
            "_ctx": None, "_quote": quote,
        }

    smc_evidence = summarize_smc_evidence(ctx)
    signals = evaluate_all(ctx)
    candidates = build_candidates(
        symbol=canonical_symbol, broker_symbol=broker_symbol, asset_class="FOREX",
        cycle_id=cycle_id, signals=signals, htf_trend_h4=ctx.htf_trend_h4, now=at,
    )
    return {
        "status": "OK", "source": source, "as_of": at.isoformat(), "regime": ctx.regime, "smc_evidence": smc_evidence,
        "mtfai1": mtfai1_result, "families_candidates": candidates,
        "bars": {"m15": len(m15), "h1": len(h1), "h4": len(h4)},
        # Internal-only: the built StrategyContext and resolved quote, for callers that need to
        # build a fingerprint (fingerprint.py) from this same replay pass without recomputing it
        # a second time. Not JSON-safe -- never returned from an HTTP route, only consumed
        # in-process by pattern_builder.py.
        "_ctx": ctx, "_quote": quote,
    }


def _replay_mtfai1(quote: _ReplayQuote, m15: list[MT5Candle], h1: list[MT5Candle], h4: list[MT5Candle], symbol_info: Any) -> dict[str, Any]:
    """MTFAI1's own scoring lives inline in autonomous.py (not mt5_strategies/families.py) --
    imported directly here, not reimplemented, matching every other strategy in this module."""
    from backend.brokers.mt5.autonomous import _score_candidate

    score, direction, geometry = _score_candidate(quote, m15, h1, h4, symbol_info)
    return {"strategy_id": "mtfai1", "ranking_score": score, "direction": direction, **geometry}
