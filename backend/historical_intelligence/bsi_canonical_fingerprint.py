"""BSI canonical thesis/fingerprint record (Phase B of the BSI Intelligence Migration).

ONE canonical BSI-specific persistence layer, designed to serve ALL downstream consumers the
migration directive names: candidate persistence, Historical Intelligence, confidence, self-
learning, management/counterfactual replay, analytics, DEMO outcome evaluation. Deliberately NOT
a parallel, incompatible fingerprint representation -- this is a one-to-one COMPANION to the
existing, already-working `HistoricalPatternFingerprintORM` (joined by `fingerprint_id`), adding
exactly the fields that table does not and cannot cleanly carry (it is shared across every
strategy family, not BSI-specific), while reusing everything that table already does well
(identity, peer-group isolation via `anchor_strategy="bsi__<subtype>"`, provenance/quality tiers,
regime/session/structure-flag/liquidity-flag/FVG-flag/OB-flag/PD-position/ATR-regime/RR-bucket
columns).

Why this table exists at all (the concrete gap this closes): `bsi_engine.py::_bsi_thesis_metadata()`
ALREADY COMPUTES a rich thesis object at signal time (protected_high/low, liquidity source/side/
level/swept state, mss_level, structure_break_level, fvg_id/bounds, order_block_id/bounds, session
window, entry trigger, target type/level, expected RR, management style, source rule citations --
see that function's own return value for the exact current shape) -- but it is NEVER PERSISTED
anywhere retrievable. `run_bsi_backfill.py::_persist()` calls `build_fingerprint(...)` without ever
passing `metadata=signal.metadata`. This was independently found and documented THREE separate
times across this mission's prior reports (the original `BSI_OVERNIGHT_REPORT.md` Section 13,
`BSI_ARCHITECTURE_MIGRATION_PLAN.md` Section 2 item 3, and `BSI_FOLLOWUP_RESEARCH_REPORT.md` Item
2b, which named it as the exact reason `thesis_still_intact()`-based structural-invalidation replay
could not be tested). This table is the fix: a first-class, queryable, versioned place for that
already-computed thesis data to live.

======================================================================================
LOOKAHEAD SAFETY -- explicit KNOWN_AT_ENTRY / POST_ENTRY / OUTCOME separation
======================================================================================
Three column groups below, each with a clear docstring banner. The builder function
(`build_bsi_thesis_record`) in this module populates ONLY the KNOWN_AT_ENTRY group, from data that
already existed at signal-generation time (the same `StrategySignal`/`StrategyContext`/`evidence`/
`metadata["bsi_thesis"]` objects `bsi_engine.py` already produces per candidate -- no new
computation, no refetching of "future" bars). POST_ENTRY and OUTCOME columns start NULL and are
populated by two SEPARATE, later passes that this module does NOT itself perform:
  - POST_ENTRY: a management/replay pass strictly after entry_time (Phase C's BSI-aware replay is
    the natural owner of this).
  - OUTCOME: the EXISTING, already point-in-time-safe `outcomes.py::label_outcome()` walk-forward
    labeler (reused verbatim -- this table's OUTCOME columns are a convenience DENORMALIZATION of
    that function's own result, joined in by a later sync step, never a second, competing outcome
    computation).
This structural separation is what makes it impossible for a future confidence/HI/self-learning
consumer of this table to accidentally read a POST_ENTRY or OUTCOME field while believing it has
only KNOWN_AT_ENTRY information -- the column GROUP itself is the safety boundary, not a
convention a future caller has to remember.

======================================================================================
DIRECTIVE SECTION 2 FIELD-BY-FIELD DISPOSITION (traceability, not aspirational)
======================================================================================
IDENTITY: bsi_version, subtype, symbol, direction, candidate_time, entry_time, execution_timeframe
  -- all direct columns, sourced from the existing StrategySignal/BSI_VERSION/evidence.
STRUCTURE: internal/external structure, protected_high/low, structure_break_level, mss_level,
  break_time -- direct columns, sourced 1:1 from `bsi_thesis`'s own existing field names
  (`external_structure`, `internal_structure`, `protected_high`, `protected_low`,
  `structure_break_level`, `mss_level`); "relevant swing IDs" and "displacement where relevant" are
  NOT separately captured by `bsi_thesis` today -- explicitly NOT fabricated here, left for a
  future `bsi_thesis` extension if evidence justifies it (noted, not silently dropped).
LIQUIDITY: liquidity_source/side/level/swept/sweep_time, target liquidity -- direct columns, 1:1
  from `bsi_thesis`'s existing `liquidity_source`/`liquidity_side`/`liquidity_level`/
  `liquidity_swept`/`sweep_time` fields. "distance from entry" is DERIVED (computed here, not
  stored redundantly) since it's a pure function of already-stored entry/liquidity_level.
LOCATION: dealing_range_low/high, equilibrium, premium_discount_location -- direct columns, 1:1
  from `bsi_thesis`. "entry location within range" = the already-stored `premium_discount_location`
  plus the derivable (entry - equilibrium)/(dealing_range_high - dealing_range_low) depth ratio
  (computed on read, not stored redundantly).
FVG: fvg_id, fvg_bounds, direction (implied by subtype direction) -- direct columns, 1:1 from
  `bsi_thesis`. Mitigation state and distance are NOT re-derivable point-in-time-safely from a
  single stored snapshot (mitigation is itself a FUTURE event relative to candidate time for a
  still-active zone) -- correctly left for the POST_ENTRY pass, not fabricated here.
ORDER BLOCK: order_block_id, order_block_bounds -- direct columns, 1:1 from `bsi_thesis`. Same
  mitigation/reaction-state caveat as FVG above.
SESSION: session, session_window, entry_trigger -- direct columns, 1:1 from `bsi_thesis`. Timezone/
  DST-safe resolution is already handled upstream by `bsi_engine.py`'s own `_NY_TZ`/session-window
  logic before this module ever sees the value -- not re-derived here, reused as computed.
SETUP: subtype-specific fields -- `subtype_extension` JSON column (see module docstring above for
  why this is one flexible column, not dozens of always-NULL ones), populated from the SAME
  `evidence` dict `bsi_engine.py` already builds per subtype (e.g. Under/Over's
  `level_touch_count`/`penetration_atr`, ABC's `p0_price`/`p1_price`/`p2_price`,
  New York's `sweep_side`/`penetration_atr`, etc. -- exact keys already exist, just weren't
  persisted before).
GEOMETRY: entry, initial_stop, stop_distance, target_type, target_level, initial_rr, atr, spread --
  direct columns; stop_distance/initial_rr are DERIVED on read from entry/stop/target (never
  stored redundantly, so they can never silently drift from the source values). Execution cost
  estimate is explicitly NOT captured here -- that's `execution_costs.py`'s own, already-existing,
  separately-versioned domain (reused via `HistoricalSetupOutcomeORM`'s own
  `spread_cost_r`/`commission_cost_r` columns at outcome-labeling time, not duplicated).
INTELLIGENCE: hi_result, hi_effective_sample_size, hi_historical_expectancy, confidence_version,
  confidence_score, confidence_components, eligibility_decision, rejection_reason -- direct
  columns, ALL NULLABLE and explicitly KNOWN_AT_ENTRY (populated only when HI/confidence actually
  ran at decision time -- Phase D/G/H populate these for NEW candidates going forward; the ONE rich
  backfill in Phase F is what would populate them retroactively for the historical corpus, not this
  module by itself).
MANAGEMENT: original_thesis (this whole row IS that), thesis_invalidation_conditions (derivable
  from protected_high/low + structure_direction, already stored), management_policy_version --
  POST_ENTRY group, nullable, populated by Phase C's replay pass.
OUTCOME: mfe_r, mae_r, realized_r, baseline_r, exit_reason, post_exit_continuation -- OUTCOME
  group, nullable, denormalized copy synced from `HistoricalSetupOutcomeORM` (see module docstring
  above) -- not recomputed here.

RESEARCH-ONLY infrastructure: this module defines a table and a pure builder function. It does NOT
write to STRATEGY_FAMILIES, does not change BSI activation, does not run automatically. No caller
in the live signal-generation or execution path imports this module as of this commit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.historical_intelligence.orm import utcnow
from backend.shared.db import Base

BSI_THESIS_SCHEMA_VERSION = "BSI_THESIS_V1"


class BSIThesisRecordORM(Base):
    """One row per BSI candidate (backtest OR live), one-to-one with
    `HistoricalPatternFingerprintORM.fingerprint_id`. See module docstring for the full field
    disposition and the KNOWN_AT_ENTRY / POST_ENTRY / OUTCOME safety separation."""

    __tablename__ = "bsi_thesis_records"

    record_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    fingerprint_id: Mapped[str] = mapped_column(String(160), ForeignKey("historical_pattern_fingerprints.fingerprint_id"), nullable=False, unique=True, index=True)
    schema_version: Mapped[str] = mapped_column(String(24), nullable=False, default=BSI_THESIS_SCHEMA_VERSION, index=True)

    # ================================================================================
    # KNOWN_AT_ENTRY -- populated once, at signal-generation time, never rewritten.
    # ================================================================================
    bsi_version: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # e.g. "BSI_BASELINE_V1" -- the entry-rule version, immutable per directive Section 13
    subtype: Mapped[str] = mapped_column(String(32), nullable=False, index=True)  # bsi_order_flow | bsi_abc | bsi_abcd | bsi_asian | bsi_new_york | bsi_0930 | bsi_under_over | bsi_reactionary | bsi_ob_liquidity
    canonical_symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(8), nullable=False, index=True)  # LONG | SHORT
    candidate_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)  # instant the candidate was evaluated (== entry_time for this signal-then-market-order engine, kept distinct in case a future limit-order variant decouples them)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    execution_timeframe: Mapped[str] = mapped_column(String(16), nullable=False, default="M15")

    # STRUCTURE
    structure_direction: Mapped[str | None] = mapped_column(String(16), nullable=True)  # bullish | bearish -- mentor's own vocabulary, distinct from trade direction (see bsi_engine.py's own documented bugfix of this exact field)
    external_structure: Mapped[str | None] = mapped_column(String(16), nullable=True)  # HTF bias, where the subtype gates on one
    internal_structure: Mapped[str | None] = mapped_column(String(32), nullable=True)  # e.g. break_kind for the LTF confirming structure
    protected_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    protected_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    structure_break_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    mss_level: Mapped[float | None] = mapped_column(Float, nullable=True)

    # LIQUIDITY
    liquidity_source: Mapped[str | None] = mapped_column(String(48), nullable=True)
    liquidity_side: Mapped[str | None] = mapped_column(String(16), nullable=True)  # buy_side | sell_side
    liquidity_level: Mapped[float | None] = mapped_column(Float, nullable=True)  # the target liquidity price
    liquidity_swept: Mapped[bool | None] = mapped_column(Integer, nullable=True)  # tri-state via nullable Integer(0/1) rather than Boolean so "unknown" != "false" is representable; see _to_bool_or_none
    sweep_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # LOCATION
    dealing_range_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    dealing_range_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    equilibrium: Mapped[float | None] = mapped_column(Float, nullable=True)
    premium_discount_location: Mapped[str | None] = mapped_column(String(16), nullable=True)  # premium | discount

    # FVG
    fvg_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    fvg_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    fvg_high: Mapped[float | None] = mapped_column(Float, nullable=True)

    # ORDER BLOCK
    order_block_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    order_block_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    order_block_high: Mapped[float | None] = mapped_column(Float, nullable=True)

    # SESSION
    session: Mapped[str | None] = mapped_column(String(32), nullable=True)
    session_window: Mapped[str | None] = mapped_column(String(64), nullable=True)
    entry_trigger: Mapped[str | None] = mapped_column(String(96), nullable=True)

    # SETUP -- subtype-specific fields (see module docstring: one flexible column by design)
    subtype_extension: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # GEOMETRY
    entry: Mapped[float] = mapped_column(Float, nullable=False)
    initial_stop: Mapped[float] = mapped_column(Float, nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # natural_opposing_liquidity | fixed_1_2_rr | bounded_3r_5r | b_leg_extreme | ...
    target_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    atr_at_entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread_at_entry: Mapped[float | None] = mapped_column(Float, nullable=True)

    # INTELLIGENCE -- nullable; populated only once BSI confidence/HI actually run for this candidate (Phase D/G/H)
    hi_result: Mapped[str | None] = mapped_column(String(32), nullable=True)  # SUPPORT | OPPOSE | NEUTRAL | INSUFFICIENT
    hi_effective_sample_size: Mapped[float | None] = mapped_column(Float, nullable=True)
    hi_historical_expectancy: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_version: Mapped[str | None] = mapped_column(String(32), nullable=True)  # e.g. "BSI_CONFIDENCE_V1" once Phase D/G lands
    confidence_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_components: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    eligibility_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)  # SELECTED | REJECTED | DEFERRED
    rejection_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    source_rule_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # mentor transcript citation IDs, straight from bsi_thesis -- provenance, never fabricated

    # ================================================================================
    # POST_ENTRY -- NULL until a management/replay pass runs strictly after entry_time.
    # Never populated by build_bsi_thesis_record(); a separate function's responsibility.
    # ================================================================================
    management_policy_version: Mapped[str | None] = mapped_column(String(32), nullable=True)  # e.g. "BSI_BASELINE_V1" (no intervention) | future "BSI_ADAPTIVE_V2"
    thesis_invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    thesis_invalidation_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    management_actions_applied: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # ================================================================================
    # OUTCOME -- NULL until synced from HistoricalSetupOutcomeORM (denormalized copy, not
    # a competing computation -- see module docstring).
    # ================================================================================
    mfe_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    mae_r: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_r: Mapped[float | None] = mapped_column(Float, nullable=True)  # == HistoricalSetupOutcomeORM.outcome_r under BSI_BASELINE_V1 (no management intervention)
    realized_r: Mapped[float | None] = mapped_column(Float, nullable=True)  # under whatever management_policy_version actually governed this candidate (== baseline_r when management_policy_version == "BSI_BASELINE_V1")
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)  # SL_HIT | TP_HIT | MTM_TIMEOUT | MANAGED_EXIT
    post_exit_continuation_r: Mapped[float | None] = mapped_column(Float, nullable=True)  # diagnostic only, per the mission's own "never a production target" rule -- how far price continued past the actual exit, for profit-capture-ratio style analysis

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    __table_args__ = (
        Index("ix_bsi_thesis_subtype_symbol_direction", "subtype", "canonical_symbol", "direction"),
        Index("ix_bsi_thesis_subtype_session", "subtype", "session"),
    )


# ---------------------------------------------------------------------------------------
# Derived-field helpers -- computed on read, never stored redundantly (module docstring's
# own "never store what's a pure function of already-stored values" rule).
# ---------------------------------------------------------------------------------------
def stop_distance(record: BSIThesisRecordORM) -> float:
    return abs(record.entry - record.initial_stop)


def initial_rr(record: BSIThesisRecordORM) -> float | None:
    risk = stop_distance(record)
    if risk <= 0 or record.target_level is None:
        return None
    return abs(record.target_level - record.entry) / risk


def liquidity_distance_from_entry(record: BSIThesisRecordORM) -> float | None:
    if record.liquidity_level is None:
        return None
    return abs(record.liquidity_level - record.entry)


def location_depth_ratio(record: BSIThesisRecordORM) -> float | None:
    """0 at equilibrium, 1 at the leg's own extreme -- same measure bsi_confidence.py's own
    location_quality dimension already computes, exposed here for reuse rather than
    reimplementation once Phase D/G wires this table into confidence scoring."""
    if record.dealing_range_low is None or record.dealing_range_high is None or record.equilibrium is None:
        return None
    half_range = (record.dealing_range_high - record.dealing_range_low) / 2.0
    if half_range <= 0:
        return None
    return abs(record.entry - record.equilibrium) / half_range


def _to_bool_or_none(value: Any) -> bool | None:
    return None if value is None else bool(value)


@dataclass(frozen=True)
class BSISignalSource:
    """Everything build_bsi_thesis_record() needs, decoupled from the live StrategySignal/
    StrategyContext types so this module has no import-time dependency on the signal-generation
    path (keeps this a pure, testable data-transformation module)."""

    fingerprint_id: str
    bsi_version: str
    subtype: str
    canonical_symbol: str
    direction: str
    candidate_time: datetime
    entry_time: datetime
    execution_timeframe: str
    thesis: dict[str, Any]  # the metadata["bsi_thesis"] dict bsi_engine.py already produces
    evidence: dict[str, Any]  # the subtype's own evidence dict (subtype-specific fields live here)
    entry: float
    initial_stop: float


def build_bsi_thesis_record(source: BSISignalSource) -> BSIThesisRecordORM:
    """Pure transformation: BSISignalSource -> BSIThesisRecordORM, populating ONLY the
    KNOWN_AT_ENTRY column group (see module docstring). Never touches POST_ENTRY/OUTCOME columns
    -- they are left at their ORM defaults (NULL), by construction, not by omission the caller
    could accidentally violate."""
    t = source.thesis
    e = source.evidence
    record_id = "BSITR_" + source.fingerprint_id[4:] if source.fingerprint_id.startswith("HPF_") else "BSITR_" + source.fingerprint_id

    return BSIThesisRecordORM(
        record_id=record_id,
        fingerprint_id=source.fingerprint_id,
        schema_version=BSI_THESIS_SCHEMA_VERSION,
        bsi_version=source.bsi_version,
        subtype=source.subtype,
        canonical_symbol=source.canonical_symbol,
        direction=source.direction,
        candidate_time=source.candidate_time,
        entry_time=source.entry_time,
        execution_timeframe=source.execution_timeframe,
        structure_direction=t.get("structure_direction"),
        external_structure=t.get("external_structure"),
        internal_structure=t.get("internal_structure"),
        protected_high=t.get("protected_high"),
        protected_low=t.get("protected_low"),
        structure_break_level=t.get("structure_break_level"),
        mss_level=t.get("mss_level"),
        liquidity_source=t.get("liquidity_source"),
        liquidity_side=t.get("liquidity_side"),
        liquidity_level=t.get("liquidity_level"),
        liquidity_swept=_to_bool_or_none(t.get("liquidity_swept")),
        sweep_time=_parse_dt(t.get("sweep_time")),
        dealing_range_low=t.get("dealing_range_low"),
        dealing_range_high=t.get("dealing_range_high"),
        equilibrium=t.get("equilibrium"),
        premium_discount_location=t.get("premium_discount_location"),
        fvg_id=t.get("fvg_id"),
        fvg_low=(t.get("fvg_bounds") or [None, None])[0],
        fvg_high=(t.get("fvg_bounds") or [None, None])[1],
        order_block_id=t.get("order_block_id"),
        order_block_low=(t.get("order_block_bounds") or [None, None])[0],
        order_block_high=(t.get("order_block_bounds") or [None, None])[1],
        session=t.get("session"),
        session_window=t.get("session_window"),
        entry_trigger=t.get("entry_trigger"),
        subtype_extension=dict(e or {}),
        entry=source.entry,
        initial_stop=source.initial_stop,
        target_type=t.get("target_type"),
        target_level=t.get("target_level"),
        atr_at_entry=None,  # not currently threaded through evidence/thesis -- explicitly left NULL rather than guessed; a real gap for a future bsi_engine.py addition, not fabricated here
        spread_at_entry=None,  # same -- MT5_BSI_* spread-safety gate runs at signal time but its own observed spread value isn't captured in evidence/thesis today
        hi_result=None, hi_effective_sample_size=None, hi_historical_expectancy=None,
        confidence_version=None, confidence_score=None, confidence_components=None,
        eligibility_decision=None, rejection_reason=None,
        source_rule_ids=list(t.get("source_rule_ids") or []),
    )


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
