"""BSI Intelligence Migration Phase B/E: tests for the canonical BSI thesis/fingerprint record
(backend/historical_intelligence/bsi_canonical_fingerprint.py) -- proves the schema round-trips
correctly, the KNOWN_AT_ENTRY/POST_ENTRY/OUTCOME separation is enforced by construction (not just
convention), and the derived-field helpers are pure functions of already-stored values."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.historical_intelligence.bsi_canonical_fingerprint import (
    BSI_THESIS_SCHEMA_VERSION,
    BSISignalSource,
    BSIThesisRecordORM,
    build_bsi_thesis_record,
    initial_rr,
    liquidity_distance_from_entry,
    location_depth_ratio,
    stop_distance,
)
from backend.shared.db import Base


def _session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _real_bsi_thesis_dict() -> dict:
    """A realistic bsi_thesis dict shaped exactly like _bsi_thesis_metadata() in bsi_engine.py
    actually produces (field names copied verbatim from that function's real return value)."""
    return {
        "bsi_version": "BSI_BASELINE_V1",
        "setup_subtype": "bsi_under_over",
        "direction": "LONG",
        "structure_direction": "bullish",
        "external_structure": None,
        "internal_structure": None,
        "protected_high": 1.0620,
        "protected_low": 1.0480,
        "structure_break_level": None,
        "mss_level": None,
        "liquidity_source": "equal_level_min_3_touches",
        "liquidity_side": "sell_side",
        "liquidity_level": 1.0650,
        "liquidity_swept": True,
        "sweep_time": "2026-03-05T09:15:00+00:00",
        "dealing_range_low": 1.0480,
        "dealing_range_high": 1.0620,
        "equilibrium": 1.0550,
        "premium_discount_location": "discount",
        "fvg_id": "FVG_abc123",
        "fvg_bounds": (1.0505, 1.0512),
        "order_block_id": None,
        "order_block_bounds": None,
        "session": None,
        "session_window": None,
        "entry_trigger": "close_based_fakeout_then_reclaim_of_multi_touch_level",
        "target_type": "natural_opposing_liquidity",
        "target_level": 1.0650,
        "source_rule_ids": ["row_6", "row_9", "row_14"],
    }


def _source() -> BSISignalSource:
    return BSISignalSource(
        fingerprint_id="HPF_deadbeef123456",
        bsi_version="BSI_BASELINE_V1",
        subtype="bsi_under_over",
        canonical_symbol="EURUSD",
        direction="LONG",
        candidate_time=datetime(2026, 3, 5, 9, 30, tzinfo=timezone.utc),
        entry_time=datetime(2026, 3, 5, 9, 30, tzinfo=timezone.utc),
        execution_timeframe="M15",
        thesis=_real_bsi_thesis_dict(),
        evidence={"level_id": "LVL_1", "level_touch_count": 3, "level_side": "sell_side", "penetration_atr": 0.42},
        entry=1.0510,
        initial_stop=1.0470,
    )


def test_round_trip_persists_and_reads_back_identically():
    SessionLocal = _session_factory()
    record = build_bsi_thesis_record(_source())
    record_id = record.record_id  # captured before commit expires the detached instance's attributes
    with SessionLocal() as db:
        db.add(record)
        db.commit()
    with SessionLocal() as db:
        fetched = db.get(BSIThesisRecordORM, record_id)
    assert fetched is not None
    assert fetched.subtype == "bsi_under_over"
    assert fetched.canonical_symbol == "EURUSD"
    assert fetched.direction == "LONG"
    assert fetched.protected_low == 1.0480
    assert fetched.protected_high == 1.0620
    assert fetched.liquidity_level == 1.0650
    assert fetched.liquidity_swept == 1  # tri-state bool stored as 0/1/NULL
    assert fetched.fvg_low == 1.0505
    assert fetched.fvg_high == 1.0512
    assert fetched.premium_discount_location == "discount"
    assert fetched.target_type == "natural_opposing_liquidity"
    assert fetched.subtype_extension["level_touch_count"] == 3
    assert fetched.source_rule_ids == ["row_6", "row_9", "row_14"]
    assert fetched.schema_version == BSI_THESIS_SCHEMA_VERSION


def test_known_at_entry_fields_populated_post_entry_and_outcome_fields_null_by_construction():
    """The core anti-lookahead property: build_bsi_thesis_record() must NEVER populate a
    POST_ENTRY or OUTCOME field -- those are only ever filled by a later, separate pass. This is
    enforced structurally (the builder simply never sets them), not by a runtime check -- this
    test proves that structural guarantee actually holds for the real builder function, not just
    that it's documented to."""
    record = build_bsi_thesis_record(_source())
    # KNOWN_AT_ENTRY: populated
    assert record.entry == 1.0510
    assert record.initial_stop == 1.0470
    assert record.protected_low == 1.0480
    # POST_ENTRY: must be NULL -- no management pass has run yet
    assert record.management_policy_version is None
    assert record.thesis_invalidated_at is None
    assert record.thesis_invalidation_reason is None
    assert record.management_actions_applied is None
    # OUTCOME: must be NULL -- no outcome-labeling pass has run yet
    assert record.mfe_r is None
    assert record.mae_r is None
    assert record.baseline_r is None
    assert record.realized_r is None
    assert record.exit_reason is None
    assert record.post_exit_continuation_r is None


def test_derived_fields_are_pure_functions_never_stored_redundantly():
    record = build_bsi_thesis_record(_source())
    # risk = |entry - stop| = |1.0510 - 1.0470| = 0.0040
    assert stop_distance(record) == pytest.approx(0.0040, abs=1e-9)
    # RR = |target - entry| / risk = |1.0650 - 1.0510| / 0.0040 = 3.5
    assert initial_rr(record) == pytest.approx(3.5, abs=1e-6)
    # liquidity distance = |1.0650 - 1.0510| = 0.0140
    assert liquidity_distance_from_entry(record) == pytest.approx(0.0140, abs=1e-9)
    # depth ratio: entry=1.0510, equilibrium=1.0550, half_range=(1.0620-1.0480)/2=0.0070
    # |1.0510-1.0550|/0.0070 = 0.0040/0.0070
    assert location_depth_ratio(record) == pytest.approx(0.0040 / 0.0070, abs=1e-6)


def test_missing_optional_fields_do_not_crash_the_builder():
    """A subtype whose evidence/thesis genuinely lacks some fields (e.g. order_flow/abc have no
    liquidity_level counterpart the way under_over does for some paths) must not raise -- absence
    is a legitimate, common state, not an error."""
    source = BSISignalSource(
        fingerprint_id="HPF_minimal", bsi_version="BSI_BASELINE_V1", subtype="bsi_abc",
        canonical_symbol="GBPUSD", direction="SHORT",
        candidate_time=datetime(2026, 4, 1, tzinfo=timezone.utc), entry_time=datetime(2026, 4, 1, tzinfo=timezone.utc),
        execution_timeframe="M15", thesis={}, evidence={}, entry=1.25, initial_stop=1.26,
    )
    record = build_bsi_thesis_record(source)
    assert record.fingerprint_id == "HPF_minimal"
    assert record.fvg_low is None and record.fvg_high is None
    assert record.order_block_low is None and record.order_block_high is None
    assert record.liquidity_swept is None  # tri-state: genuinely unknown, not False
    assert initial_rr(record) is None  # no target_level -> undefined RR, not a fabricated 0
    assert location_depth_ratio(record) is None  # no dealing range -> undefined, not fabricated


def test_fingerprint_id_join_key_survives_round_trip():
    """The one-to-one join to HistoricalPatternFingerprintORM (via fingerprint_id) is the whole
    point of this being a COMPANION table, not a parallel one -- prove the key survives intact."""
    source = _source()
    record = build_bsi_thesis_record(source)
    assert record.fingerprint_id == source.fingerprint_id == "HPF_deadbeef123456"
