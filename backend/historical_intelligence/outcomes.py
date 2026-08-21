"""Historical outcome labeling (Part 3) -- walks FORWARD through candles strictly after a
setup's entry_time to determine what actually happened. Never reads or influences signal
generation; this module is called only AFTER a fingerprint already exists.

Reuses the exact R-multiple formula and the same SL-touched-first tie-break convention as
backend/brokers/mt5/outcome_resolver.py::MT5OutcomeResolver._evaluate_shadow_candidate (the live
shadow-tracking resolver) -- deliberately NOT reimplemented differently, so "what counts as a
win" means the same thing whether the setup was a live shadow candidate or a purely historical
replay. Divergences from that resolver, both intentional: (1) this module reads candles from the
same trustworthy source hierarchy replay.py uses for point-in-time replay (see
_future_candles_with_quality below), never from the live broker adapter -- historical labeling
must never make a network call; (2) this module also tracks the finer-grained partial-R
milestones (+0.25R/+0.5R/+0.75R/+1R/+1.5R/+2R) and an immediate-failure flag the live resolver
doesn't need.

Source hierarchy for OUTCOME labeling (deliberately similar to, but not identical to, replay.py's
point-in-time hierarchy -- outcome labeling runs long after the fact, so there is no point-in-
time ambiguity to resolve; the question here is purely "how MUCH do we trust this value", not
"what was knowable when"):
  1. revision-tracked bars (MT5CandleRevisionORM, latest revision, no post-finalization anomaly)
     -> quality HIGH.
  2. finalized canonical bars with no revision history, written AFTER this session's point-in-
     time integrity fix went live (2026-08-12 ~21:20 UTC) -> quality ACCEPTABLE. Written after
     the fix means no legacy pre-fix silent-overwrite risk applies to that specific write, even
     though it isn't itself revision-protected.
  3. finalized canonical bars with no revision history, SUSPECT-flagged, OR written BEFORE the
     fix (cannot rule out the confirmed pre-fix bar-overwrite bug having silently altered this
     exact value before it was ever recorded here) -> quality APPROXIMATE.
  4. bars that are NOT YET finalized (still theoretically live-mutable, no revision confirms
     them) or carry a proven post_finalization_anomaly -> quality UNTRUSTED. INVALID-flagged
     bars are excluded entirely (never even considered "untrusted data used" -- simply skipped,
     same as before).
An outcome's overall `data_quality` is the WORST tier among every bar actually used in computing
it (MFE/MAE tracking scans every bar; the resolving touch is one of them). UNTRUSTED outcomes are
still persisted (for audit/observability) but statistics.py excludes them from every active
aggregate -- see that module's `pattern_statistics`.

Decision snapshots (MT5DecisionSnapshotORM) are NEVER used here: they capture only the bars
BEFORE/AT decision time for point-in-time replay, never the future price path a setup's outcome
depends on -- using them for outcome labeling would silently read data they were never built to
contain.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.brokers.mt5.orm import MT5CanonicalCandleORM
from backend.historical_intelligence import execution_costs
from backend.historical_intelligence.orm import HistoricalSetupOutcomeORM, MT5CandleRevisionORM
from backend.historical_intelligence.quality import is_finalized
from backend.shared.db import SessionLocal

# Reached-adverse-excursion threshold (as a fraction of planned risk) that, if hit BEFORE the
# setup ever reaches +0.25R, marks it an immediate failure -- distinct from a plain SL_HIT
# (which requires touching the exact stop level): a setup that gets to -0.9R and then crawls
# back to breakeven without ever showing real favorable movement first behaved like a failure
# even if the stop itself was never technically touched.
_IMMEDIATE_FAILURE_ADVERSE_FRACTION = 0.8
_R_MILESTONES = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)

# How far forward (in bars) this module will scan before giving up and marking a setup PENDING
# rather than scanning unboundedly -- generous relative to MT5_MAX_HOLDING_MINUTES (240 min = 16
# M15 bars) used by the live engine's own management logic, since historical setups can take
# longer to resolve when nothing is actively managing them.
_MAX_LOOKFORWARD_BARS = 800  # ~8.3 days of M15 bars

# When this session's point-in-time integrity fix (bar revisions + finalized-bar protection)
# went live in production -- confirmed via deployment logs. A canonical bar with no revision
# history that was last written BEFORE this instant cannot be ruled out as a victim of the
# confirmed pre-fix bar-overwrite bug; one written after it carries no such risk even though it
# isn't itself revision-protected (nothing has overwritten it since, by construction).
_INTEGRITY_FIX_DEPLOYED_AT = datetime(2026, 8, 12, 21, 20, tzinfo=timezone.utc)

QUALITY_HIGH = "HIGH"
QUALITY_ACCEPTABLE = "ACCEPTABLE"
QUALITY_APPROXIMATE = "APPROXIMATE"
QUALITY_UNTRUSTED = "UNTRUSTED"
_QUALITY_ORDER = {QUALITY_HIGH: 0, QUALITY_ACCEPTABLE: 1, QUALITY_APPROXIMATE: 2, QUALITY_UNTRUSTED: 3}


def _worse(a: str, b: str) -> str:
    return a if _QUALITY_ORDER[a] >= _QUALITY_ORDER[b] else b


@dataclass(frozen=True)
class _QualityCandle:
    timestamp: datetime
    high: float
    low: float
    close: float
    quality_tier: str


def _ensure_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _outcome_id(fingerprint_id: str) -> str:
    return "HSO_" + hashlib.sha256(fingerprint_id.encode()).hexdigest()[:40]


def _future_candles_with_quality(*, provider: str, broker_symbol: str, timeframe: str, after: datetime, limit: int) -> list[_QualityCandle]:
    after = _ensure_utc(after)
    tf = timeframe.upper()
    now = utcnow()
    resolved: dict[datetime, _QualityCandle] = {}

    with SessionLocal() as db:
        revision_rows = (
            db.query(MT5CandleRevisionORM)
            .filter(
                MT5CandleRevisionORM.provider == provider.upper(),
                MT5CandleRevisionORM.broker_symbol == broker_symbol.upper(),
                MT5CandleRevisionORM.timeframe == tf,
                MT5CandleRevisionORM.bar_timestamp_utc > after,
            )
            .order_by(MT5CandleRevisionORM.bar_timestamp_utc.asc(), MT5CandleRevisionORM.revision_number.asc())
            .limit(max(limit * 6, 600))
            .all()
        )
        latest_by_bar: dict[datetime, MT5CandleRevisionORM] = {}
        for rev in revision_rows:
            latest_by_bar[_ensure_utc(rev.bar_timestamp_utc)] = rev  # ascending order -> last write wins -> latest revision

        for bar_time, rev in latest_by_bar.items():
            tier = QUALITY_UNTRUSTED if rev.post_finalization_anomaly else QUALITY_HIGH
            resolved[bar_time] = _QualityCandle(timestamp=bar_time, high=rev.high, low=rev.low, close=rev.close, quality_tier=tier)

        if len(resolved) < limit:
            canonical_rows = (
                db.query(MT5CanonicalCandleORM)
                .filter(
                    MT5CanonicalCandleORM.provider == provider.upper(),
                    MT5CanonicalCandleORM.broker_symbol == broker_symbol.upper(),
                    MT5CanonicalCandleORM.timeframe == tf,
                    MT5CanonicalCandleORM.timestamp > after,
                    MT5CanonicalCandleORM.quality != "INVALID",
                )
                .order_by(MT5CanonicalCandleORM.timestamp.asc())
                .limit(max(limit * 2, 200))
                .all()
            )
            for row in canonical_rows:
                raw_utc = row.timestamp_utc or row.timestamp
                bar_time = _ensure_utc(raw_utc)
                if bar_time in resolved:
                    continue
                if not is_finalized(bar_time, tf, now=now):
                    tier = QUALITY_UNTRUSTED  # not yet settled, no revision confirms it -- never trusted as authoritative
                elif row.quality == "SUSPECT":
                    tier = QUALITY_APPROXIMATE
                else:
                    last_write = row.updated_at or row.fetched_at
                    last_write = _ensure_utc(last_write) if last_write else None
                    tier = QUALITY_ACCEPTABLE if (last_write and last_write >= _INTEGRITY_FIX_DEPLOYED_AT) else QUALITY_APPROXIMATE
                resolved[bar_time] = _QualityCandle(timestamp=bar_time, high=row.high, low=row.low, close=row.close, quality_tier=tier)

    ordered = sorted(resolved.values(), key=lambda c: c.timestamp)
    return ordered[:limit]


def label_outcome(
    *,
    fingerprint_id: str,
    canonical_symbol: str,
    broker_symbol: str,
    direction: str,
    entry: float,
    stop_loss: float,
    take_profit: float,
    entry_time: datetime,
    provider: str = "MT5",
    real_spread: float | None = None,
    db: Any = None,
) -> dict[str, Any]:
    """Computes and PERSISTS (upserts by fingerprint_id) a HistoricalSetupOutcomeORM row. Pure
    read of future candles + deterministic walk-forward -- no broker calls, no mutation of any
    other table. `real_spread`: when known (e.g. from a decision snapshot), used as the OBSERVED-
    tier spread cost. When None, execution_costs.resolve_spread_cost() falls through its own
    point-in-time-safe HISTORICAL_ESTIMATE / CONFIG_FALLBACK / UNKNOWN tiers (see that module's
    docstring) rather than fabricating a value -- net_outcome_r is only ever computed when the
    spread component resolves to something other than UNKNOWN; gross outcome_r is always
    preserved regardless. spread_cost_provenance/commission_cost_provenance on the persisted row
    record exactly which tier was used.

    `db` (optional, default None): when omitted, behavior is EXACTLY as before -- opens and
    commits its own short-lived session per call. When a caller supplies an existing session
    (bulk_replay.py's batched fast path -- Phase 8 of the corpus-expansion-throughput directive),
    the row is added/updated on that session WITHOUT committing -- the caller owns the commit
    boundary, so many labels can share one round-trip. Every other existing caller is completely
    unaffected since this parameter is additive and defaults to the old behavior."""
    risk = abs(entry - stop_loss)
    if risk <= 0:
        return _persist(fingerprint_id, {"resolution_status": "INSUFFICIENT_FUTURE_DATA", "bars_scanned": 0, "data_quality": QUALITY_UNTRUSTED}, db=db)

    long = direction.upper() == "LONG"
    candles = _future_candles_with_quality(provider=provider, broker_symbol=broker_symbol, timeframe="M15", after=entry_time, limit=_MAX_LOOKFORWARD_BARS)
    if not candles:
        return _persist(fingerprint_id, {"resolution_status": "PENDING", "bars_scanned": 0, "data_quality": QUALITY_UNTRUSTED}, db=db)

    max_favorable = 0.0
    max_adverse = 0.0
    mfe_time: datetime | None = None
    milestone_times: dict[float, datetime] = {}
    reached_before_adverse_failure = False
    resolution: dict[str, Any] | None = None
    data_quality = QUALITY_HIGH

    for candle in candles:
        data_quality = _worse(data_quality, candle.quality_tier)
        high, low, close = candle.high, candle.low, candle.close
        favorable = (high - entry) if long else (entry - low)
        adverse = (entry - low) if long else (high - entry)
        if favorable > max_favorable:
            max_favorable = favorable
            mfe_time = candle.timestamp
        max_adverse = max(max_adverse, adverse)

        favorable_r_now = max_favorable / risk
        for milestone in _R_MILESTONES:
            if milestone not in milestone_times and favorable_r_now >= milestone:
                milestone_times[milestone] = candle.timestamp

        if not reached_before_adverse_failure and favorable_r_now >= 0.25:
            reached_before_adverse_failure = True

        sl_touched = (low <= stop_loss) if long else (high >= stop_loss)
        tp_touched = (high >= take_profit) if long else (low <= take_profit)
        if sl_touched or tp_touched:
            if sl_touched:
                resolution = {"resolution_status": "RESOLVED", "tp_hit": False, "sl_hit": True, "outcome_r": -1.0, "resolved_at_candle": candle.timestamp}
            else:
                planned_rr = abs(take_profit - entry) / risk
                resolution = {"resolution_status": "RESOLVED", "tp_hit": True, "sl_hit": False, "outcome_r": planned_rr, "resolved_at_candle": candle.timestamp}
            break

    immediate_failure = (max_adverse / risk >= _IMMEDIATE_FAILURE_ADVERSE_FRACTION) and not reached_before_adverse_failure

    base: dict[str, Any] = {
        "mfe_r": round(max_favorable / risk, 4),
        "mae_r": round(max_adverse / risk, 4),
        "time_to_mfe_seconds": (mfe_time - entry_time).total_seconds() if mfe_time else None,
        "time_to_0_5r_seconds": (milestone_times[0.5] - entry_time).total_seconds() if 0.5 in milestone_times else None,
        "time_to_1r_seconds": (milestone_times[1.0] - entry_time).total_seconds() if 1.0 in milestone_times else None,
        "immediate_failure": immediate_failure,
        "reached_0_25r": 0.25 in milestone_times,
        "reached_0_5r": 0.5 in milestone_times,
        "reached_0_75r": 0.75 in milestone_times,
        "reached_1r": 1.0 in milestone_times,
        "reached_1_5r": 1.5 in milestone_times,
        "reached_2r": 2.0 in milestone_times,
        "bars_scanned": len(candles),
        "data_quality": data_quality,
    }

    if resolution is not None:
        outcome_r = resolution["outcome_r"]
        base["outcome_r"] = round(outcome_r, 4)
        base["tp_hit"] = resolution["tp_hit"]
        base["sl_hit"] = resolution["sl_hit"]
        base["holding_duration_seconds"] = (resolution["resolved_at_candle"] - entry_time).total_seconds()
        base.update(_cost_fields(outcome_r, canonical_symbol=canonical_symbol, entry_time=entry_time, risk=risk, real_spread=real_spread, db=db))
        base["resolution_status"] = "RESOLVED"
        base["resolved_at"] = utcnow()
    elif len(candles) >= _MAX_LOOKFORWARD_BARS:
        # Scanned the full lookforward window with neither level touched -- mark-to-market at
        # the last available candle rather than leaving this open forever.
        last_close = candles[-1].close
        mtm_r = ((last_close - entry) / risk) * (1.0 if long else -1.0)
        base.update({
            "outcome_r": round(mtm_r, 4), "tp_hit": False, "sl_hit": False,
            "holding_duration_seconds": (candles[-1].timestamp - entry_time).total_seconds(),
            "resolution_status": "RESOLVED", "resolved_at": utcnow(),
        })
        base.update(_cost_fields(mtm_r, canonical_symbol=canonical_symbol, entry_time=entry_time, risk=risk, real_spread=real_spread, db=db))
    else:
        # Neither level touched and history doesn't yet extend far enough to know -- genuinely
        # pending, not a failure to resolve.
        base["resolution_status"] = "PENDING"

    return _persist(fingerprint_id, base, db=db)


def _cost_fields(gross_r: float, *, canonical_symbol: str, entry_time: datetime, risk: float, real_spread: float | None, db: Any) -> dict[str, Any]:
    """Computes and labels the full execution-cost picture for one resolved outcome (QuantConnect/
    LEAN gap-analysis roadmap Phase 1, item 1) -- see execution_costs.py's module docstring for
    the exact provenance-tier definitions. net_outcome_r requires the spread component to be at
    least HISTORICAL_ESTIMATE-or-better known (never computed from commission alone); commission
    is deducted on top when it is known (currently always a real, verified $0 for this account --
    see execution_costs.resolve_commission_cost_r), and simply omitted (not guessed) otherwise."""
    spread = execution_costs.resolve_spread_cost(canonical_symbol=canonical_symbol, entry_time=entry_time, risk=risk, real_spread=real_spread, db=db)
    commission = execution_costs.resolve_commission_cost_r(db=db)

    net_r = None
    if spread.spread_cost_r is not None:
        net_r = gross_r - spread.spread_cost_r - (commission.commission_cost_r or 0.0)

    return {
        "net_outcome_r": round(net_r, 4) if net_r is not None else None,
        "real_spread_price": spread.real_spread_price,
        "spread_cost_r": spread.spread_cost_r,
        "spread_cost_provenance": spread.provenance,
        "commission_cost_r": commission.commission_cost_r,
        "commission_cost_provenance": commission.provenance,
    }


def _persist(fingerprint_id: str, fields: dict[str, Any], *, db: Any = None) -> dict[str, Any]:
    outcome_id = _outcome_id(fingerprint_id)
    fields = {k: v for k, v in fields.items() if k != "resolved_at_candle"}

    def _write(session: Any) -> dict[str, Any]:
        row = session.get(HistoricalSetupOutcomeORM, outcome_id)
        if row is None:
            row = HistoricalSetupOutcomeORM(outcome_id=outcome_id, fingerprint_id=fingerprint_id, created_at=utcnow())
            session.add(row)
        for key, value in fields.items():
            setattr(row, key, value)
        return {column.name: getattr(row, column.name) for column in HistoricalSetupOutcomeORM.__table__.columns}

    if db is not None:
        # Caller-owned session: no commit, no close -- caller (bulk_replay.py's batched fast
        # path) controls the transaction boundary across many labels for one round-trip.
        return _write(db)

    with SessionLocal() as session:
        result = _write(session)
        session.commit()
    return result
