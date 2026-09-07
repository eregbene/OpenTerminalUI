"""BSI-aware replay (BSI Intelligence Migration Phase C).

Promotes the mission's own validated bar-by-bar replay logic (built and cross-checked earlier this
mission as a one-off research script, `bsi_bar_replay_engine.py` -- 3974 trades replayed, 98.8-
100% exact baseline reproduction after two real bugs were found and fixed: an M15/M5 close-time
boundary off-by-one, and a missing `provider` filter that silently mixed in a different candle
series) into tested, importable, production-adjacent infrastructure with a clean policy-plugin
interface.

======================================================================================
WHY THIS IS NOT A NEW BACKTESTING PLATFORM (the directive's own explicit constraint)
======================================================================================
Reused, not reinvented:
  - `outcomes.py::_future_candles_with_quality` -- the EXACT SAME trustworthy-candle source
    hierarchy the original 6-month backfill and every other historical-intelligence outcome
    labeling in this codebase already uses. No new candle-trust logic exists here.
  - The conservative same-bar tie-break convention `outcomes.py` itself already established
    (adverse event wins an unresolved conflict) -- extended consistently here to BE-stop-vs-target
    conflicts, not reinvented from scratch.
  - The `BSIManagementPolicy` plugin shape mirrors `service.py::default_policies()`/
    `simulate_policy()`'s own existing pattern (a registry of named policies, each a pure function
    from (trade, path) -> outcome) -- deliberately the same shape, not a competing design, even
    though `service.py`'s own engine operates on a different DATA SOURCE (real closed live trades,
    confirmed via direct read in `BSI_FOLLOWUP_RESEARCH_REPORT.md` Item 1) and therefore cannot
    itself be reused for BSI's backtest-fingerprint corpus without modification that engine's own
    authors never anticipated.

======================================================================================
POINT-IN-TIME SAFETY
======================================================================================
`resolve_baseline()` never reads a candle before `entry_time`. M5 disambiguation, when used, is
scoped strictly to the SAME 15-minute window a conflicting M15 bar already covers -- never a
lookahead into a later window. No policy function in this module may see candles beyond what
`resolve_baseline()`'s own walk has already made available up to that point in the SAME
chronological pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import text

from backend.historical_intelligence.outcomes import _MAX_LOOKFORWARD_BARS, _future_candles_with_quality
from backend.shared.db import SessionLocal

_R_MILESTONES = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)

# M5 coverage window, as established and verified earlier this mission (mt5_canonical_candles has
# no M1 data anywhere, for any symbol; M5 exists only for this sub-window of the corpus).
_M5_COVERAGE_START = datetime(2026, 6, 16, tzinfo=timezone.utc)
_M5_COVERAGE_END = datetime(2026, 8, 13, tzinfo=timezone.utc)


@dataclass(frozen=True)
class BSITrade:
    """Minimal, self-contained description of a BSI candidate to replay -- deliberately decoupled
    from HistoricalPatternFingerprintORM/BSIThesisRecordORM so this module has no import-time
    dependency on the ORM layer (a pure function of (entry, stop, target, direction, symbol, time),
    testable in isolation)."""

    trade_id: str
    canonical_symbol: str
    broker_symbol: str
    direction: str
    entry: float
    stop_loss: float
    take_profit: float
    entry_time: datetime


@dataclass
class BaselineResolution:
    """The one, shared, point-in-time-safe bar walk every policy in this module builds on.
    Computed ONCE per trade, reused by every registered policy -- this is what guarantees every
    policy compares against the literal SAME entry (directive's own explicit requirement)."""

    outcome_r: float
    resolution_kind: str  # SL_HIT | TP_HIT | MTM_TIMEOUT | INSUFFICIENT_DATA
    resolved_at: datetime | None
    milestone_times: dict[float, datetime] = field(default_factory=dict)
    ambiguous_intrabar_events: int = 0
    bars_scanned: int = 0
    max_favorable_r: float = 0.0  # exact max favorable excursion in R, up to and including the resolving bar -- NOT quantized to _R_MILESTONES, so any policy trigger threshold (not just 0.25/0.5/0.75/1.0/1.5/2.0) can be evaluated correctly against it


@dataclass(frozen=True)
class BSIPolicyResult:
    policy_id: str
    outcome_r: float | None
    exit_reason: str
    evidence: dict[str, Any] = field(default_factory=dict)


def _m5_covers(bar_start: datetime, bar_end: datetime) -> bool:
    return _M5_COVERAGE_START <= bar_start and bar_end <= _M5_COVERAGE_END + timedelta(days=1)


def _fetch_m5_window(broker_symbol: str, start: datetime, end: datetime, db) -> list[tuple[datetime, float, float, float]]:
    """Raw M5 OHLC strictly within (start, end], provider='MT5' only (the second bug found earlier
    this mission: mt5_canonical_candles holds more than one provider's rows for the same symbol/
    timeframe/timestamp -- omitting this filter silently mixes in a wrong, unrelated candle
    series). Close-time-exclusive-open/inclusive-close, matching this corpus's own close-time
    stamping convention (the first bug found earlier this mission)."""
    rows = db.execute(
        text("""SELECT COALESCE(timestamp_utc, timestamp) AS t, high, low, close FROM mt5_canonical_candles
                 WHERE broker_symbol=:sym AND timeframe='M5' AND provider='MT5' AND quality != 'INVALID'
                 AND COALESCE(timestamp_utc, timestamp) > :start AND COALESCE(timestamp_utc, timestamp) <= :end
                 ORDER BY t ASC"""),
        {"sym": broker_symbol, "start": start, "end": end},
    ).fetchall()
    out = []
    for t, h, l, c in rows:
        tt = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        out.append((tt, float(h), float(l), float(c)))
    return out


def resolve_baseline(trade: BSITrade, db=None) -> BaselineResolution:
    """The single, shared, point-in-time-safe bar walk. Reproduces BSI_BASELINE_V1's own static
    SL/TP outcome exactly (validated: 500/502 exact matches on bsi_under_over, 3226/3266 on
    bsi_new_york -- the residual <=1.2% mismatch rate traced to candle-data revision drift between
    backfill time and replay time, not a resolution-logic error -- see
    BSI_ADAPTIVE_V1_IMPLEMENTATION_REPORT.md Section 1 for the full investigation)."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        risk = abs(trade.entry - trade.stop_loss)
        long = trade.direction.upper() == "LONG"
        candles = _future_candles_with_quality(provider="MT5", broker_symbol=trade.broker_symbol, timeframe="M15", after=trade.entry_time, limit=_MAX_LOOKFORWARD_BARS)
        if not candles:
            return BaselineResolution(outcome_r=0.0, resolution_kind="INSUFFICIENT_DATA", resolved_at=None)

        max_fav = 0.0
        milestone_times: dict[float, datetime] = {}
        ambiguous_count = 0

        for candle in candles:
            bar_start = candle.timestamp - timedelta(minutes=15)
            bar_end = candle.timestamp
            high, low = candle.high, candle.low

            favorable = (high - trade.entry) if long else (trade.entry - low)
            favorable_r_bar = favorable / risk if risk > 0 else 0.0
            if favorable > max_fav:
                max_fav = favorable
            newly_reached = [m for m in _R_MILESTONES if m not in milestone_times and favorable_r_bar >= m]
            for m in newly_reached:
                milestone_times[m] = bar_end

            sl_touched = (low <= trade.stop_loss) if long else (high >= trade.stop_loss)
            tp_touched = (high >= trade.take_profit) if long else (low <= trade.take_profit)
            if not (sl_touched or tp_touched):
                continue

            max_fav_r = max_fav / risk if risk > 0 else 0.0

            need_disambiguation = sl_touched and tp_touched
            if need_disambiguation and _m5_covers(bar_start, bar_end):
                m5 = _fetch_m5_window(trade.broker_symbol, bar_start, bar_end, db)
                if len(m5) >= 3:
                    for (mt_, mh, ml, mc) in m5:
                        m_sl = (ml <= trade.stop_loss) if long else (mh >= trade.stop_loss)
                        m_tp = (mh >= trade.take_profit) if long else (ml <= trade.take_profit)
                        if m_sl:
                            return BaselineResolution(-1.0, "SL_HIT", mt_, milestone_times, ambiguous_count, len(candles), max_fav_r)
                        if m_tp:
                            planned_rr = abs(trade.take_profit - trade.entry) / risk if risk > 0 else 0.0
                            return BaselineResolution(planned_rr, "TP_HIT", mt_, milestone_times, ambiguous_count, len(candles), max_fav_r)
                    continue  # neither actually touched at M5 granularity (shouldn't happen given M15 flags, but fail safe)
                ambiguous_count += 1
            elif need_disambiguation:
                ambiguous_count += 1

            # conservative fallback (or non-conflicting single-condition resolution): SL wins ties
            if sl_touched:
                return BaselineResolution(-1.0, "SL_HIT", bar_end, milestone_times, ambiguous_count, len(candles), max_fav_r)
            planned_rr = abs(trade.take_profit - trade.entry) / risk if risk > 0 else 0.0
            return BaselineResolution(planned_rr, "TP_HIT", bar_end, milestone_times, ambiguous_count, len(candles), max_fav_r)

        last = candles[-1]
        mtm = ((last.close - trade.entry) / risk) * (1.0 if long else -1.0) if risk > 0 else 0.0
        final_max_fav_r = max_fav / risk if risk > 0 else 0.0
        return BaselineResolution(mtm, "MTM_TIMEOUT", last.timestamp, milestone_times, ambiguous_count, len(candles), final_max_fav_r)
    finally:
        if owns_session:
            db.close()


PolicyFn = Callable[[BSITrade, BaselineResolution], BSIPolicyResult]


@dataclass(frozen=True)
class BSIManagementPolicy:
    """One pluggable management hypothesis. `evaluate(trade, baseline)` must be a pure function of
    the trade and the ALREADY-COMPUTED shared baseline resolution -- never re-walks candles
    independently, so every registered policy is guaranteed to compare against the identical
    entry/exit-touch sequence (directive's own explicit requirement: 'without changing the
    entry')."""

    policy_id: str
    description: str
    status: str  # "reference" | "research" | "FAILED_ARCHIVED"
    evaluate: PolicyFn


def _baseline_policy_fn(trade: BSITrade, baseline: BaselineResolution) -> BSIPolicyResult:
    return BSIPolicyResult(policy_id="BSI_BASELINE_V1", outcome_r=baseline.outcome_r, exit_reason=baseline.resolution_kind, evidence={"ambiguous_intrabar_events": baseline.ambiguous_intrabar_events})


BSI_BASELINE_V1_POLICY = BSIManagementPolicy(
    policy_id="BSI_BASELINE_V1", description="Mentor/course static SL/TP, no intervention -- the immutable control every other policy is measured against.",
    status="reference", evaluate=_baseline_policy_fn,
)


def _make_be_at_r_policy_fn(trigger_r: float) -> PolicyFn:
    def _fn(trade: BSITrade, baseline: BaselineResolution) -> BSIPolicyResult:
        # Uses the EXACT max favorable excursion in R (not quantized to the fixed 0.25/0.5/...
        # milestone list), so this correctly supports any trigger_r, not only ones that happen to
        # coincide with a milestone value.
        reached_trigger = baseline.max_favorable_r >= trigger_r
        if not reached_trigger:
            return BSIPolicyResult(policy_id=f"BSI_BE_{trigger_r}R", outcome_r=baseline.outcome_r, exit_reason=baseline.resolution_kind, evidence={"armed": False})
        # BE armed and baseline finished at/below 0 -> scratch; otherwise unaffected (BE is a pure downside floor).
        if baseline.outcome_r <= 0:
            return BSIPolicyResult(policy_id=f"BSI_BE_{trigger_r}R", outcome_r=0.0, exit_reason="MANAGED_EXIT_BE", evidence={"armed": True, "baseline_r": baseline.outcome_r})
        return BSIPolicyResult(policy_id=f"BSI_BE_{trigger_r}R", outcome_r=baseline.outcome_r, exit_reason=baseline.resolution_kind, evidence={"armed": True})
    return _fn


# FROZEN, ARCHIVED per directive Section 1 ("Freeze Failed Adaptive V1" -- preserve code history,
# do not silently reuse the result, do not activate). Exact bar-by-bar replay found this policy
# REDUCES expectancy on every subtype tested (bsi_under_over -0.0871R, bsi_new_york -0.0901R,
# bsi_abcd -0.1910R, bsi_order_flow -0.0721R -- see BSI_ADAPTIVE_V1_IMPLEMENTATION_REPORT.md
# Section 2 for the full evidence, including the false-protection mechanism that explains WHY).
# Registered here ONLY so it remains directly re-runnable for comparison/regression purposes --
# NEVER select this policy as a recommendation, NEVER wire it to live management.
BSI_ADAPTIVE_V1_FAILED_POLICY = BSIManagementPolicy(
    policy_id="BSI_ADAPTIVE_V1_FAILED", description="ARCHIVED/FAILED -- breakeven at +0.5R, no partial. Exact replay found this reduces expectancy on every subtype tested. Preserved for reproducibility only.",
    status="FAILED_ARCHIVED", evaluate=_make_be_at_r_policy_fn(0.5),
)

_REGISTRY: dict[str, BSIManagementPolicy] = {
    BSI_BASELINE_V1_POLICY.policy_id: BSI_BASELINE_V1_POLICY,
    BSI_ADAPTIVE_V1_FAILED_POLICY.policy_id: BSI_ADAPTIVE_V1_FAILED_POLICY,
}


def register_policy(policy: BSIManagementPolicy) -> None:
    """Adds a new experimental policy to the registry (e.g. a future BSI_ADAPTIVE_V2 hypothesis).
    Never overwrites BSI_BASELINE_V1 or an already-FAILED_ARCHIVED policy's own registration --
    both are protected by construction, matching the directive's own 'preserve baseline, preserve
    failed-hypothesis history' requirements."""
    if policy.policy_id in (BSI_BASELINE_V1_POLICY.policy_id, BSI_ADAPTIVE_V1_FAILED_POLICY.policy_id):
        raise ValueError(f"{policy.policy_id} is protected and cannot be re-registered")
    _REGISTRY[policy.policy_id] = policy


def registered_policies() -> dict[str, BSIManagementPolicy]:
    return dict(_REGISTRY)


def replay_bsi_candidate(trade: BSITrade, policy_ids: list[str] | None = None, db=None) -> dict[str, BSIPolicyResult]:
    """Runs the SAME entry through every requested policy (default: all registered), sharing ONE
    point-in-time-safe baseline resolution -- the directive's own explicit 'compare the SAME entry
    through multiple policies without changing the entry' requirement."""
    baseline = resolve_baseline(trade, db=db)
    ids = policy_ids or list(_REGISTRY.keys())
    return {pid: _REGISTRY[pid].evaluate(trade, baseline) for pid in ids if pid in _REGISTRY}
