"""Phase 4 regression tests: revision-aware outcome-quality labeling, fp-v2 peer-group
granularity (reduced fragmentation without over-generalizing), deterministic weighted similarity
+ effective sample size, replay-trust promotion from fresh evidence, and adaptive statistics
excluding non-RESOLVED post-exit data."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.adaptive_management.orm import AdaptiveManagementEventORM, AdaptiveManagerCounterfactualORM, AdaptivePositionBaselineORM
from backend.brokers.mt5.orm import MT5CanonicalCandleORM
from backend.historical_intelligence import adaptive_fingerprint, adaptive_statistics, fingerprint as fingerprint_mod, outcomes, similarity, trust_gating
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalReplayParityCheckORM, HistoricalSetupOutcomeORM, MT5CandleRevisionORM
from backend.mt5_strategies.context import StrategyContext, build_strategy_context
from backend.shared.db import Base

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _mk_rows(n: int, *, base: float = 1.1000, wick: float = 0.0015) -> list[dict]:
    rows = []
    t = NOW - timedelta(minutes=15 * n)
    price = base
    for i in range(n):
        c = base
        o = price
        h = max(o, c) + wick
        low = min(o, c) - wick
        rows.append({"time": (t + timedelta(minutes=15 * i)).isoformat(), "open": o, "high": h, "low": low, "close": c, "tick_volume": 100, "spread": 1})
        price = c
    return rows


def _ctx() -> StrategyContext:
    rows = _mk_rows(100)
    ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal("1.1010"), ask=Decimal("1.1012"), spread=Decimal("0.0002"), now=NOW)
    assert ctx is not None
    return ctx


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    for module in (outcomes, similarity, trust_gating, adaptive_statistics):
        monkeypatch.setattr(module, "SessionLocal", SessionLocal)
    return SessionLocal


# --- 1. revision-aware outcome labeling ----------------------------------------------------------


def test_outcome_labeling_prefers_revision_over_legacy_canonical(monkeypatch):
    """A future bar with BOTH a revision entry and a (differing, stale) legacy canonical row
    must resolve using the revision value (quality HIGH), never the legacy one."""
    SessionLocal = _session_factory(monkeypatch)
    entry_time = NOW
    bar_time = entry_time + timedelta(minutes=15)
    with SessionLocal() as db:
        # Legacy canonical row: if this were used, SL (1.0980) would be touched (low=1.0975).
        db.add(MT5CanonicalCandleORM(
            candle_id="MT5:EURUSD:M15:legacy", provider="MT5", canonical_symbol="EURUSD", broker_symbol="EURUSD",
            timeframe="M15", timestamp=bar_time, timestamp_utc=bar_time, open=1.1000, high=1.1010, low=1.0975, close=1.1000,
            quality="VALID", finalized=True, updated_at=datetime(2026, 8, 12, 22, 0, tzinfo=timezone.utc),
        ))
        # Revision row (authoritative): price never drops below 1.0995 -- SL never touched.
        db.add(MT5CandleRevisionORM(
            revision_id="MT5:EURUSD:M15:rev1", provider="MT5", canonical_symbol="EURUSD", broker_symbol="EURUSD", timeframe="M15",
            bar_timestamp=bar_time, bar_timestamp_utc=bar_time, broker_utc_offset_minutes=0, observed_at=bar_time + timedelta(minutes=1),
            revision_number=1, open=1.1000, high=1.1300, low=1.0995, close=1.1250, tick_volume=10, spread=1, real_volume=0,
            finalized=True, post_finalization_anomaly=False, created_at=NOW,
        ))
        db.commit()

    result = outcomes.label_outcome(
        fingerprint_id="FP_REV_TEST", canonical_symbol="EURUSD", broker_symbol="EURUSD", direction="LONG",
        entry=1.1000, stop_loss=1.0980, take_profit=1.1200, entry_time=entry_time,
    )
    assert result["sl_hit"] is False
    assert result["tp_hit"] is True
    assert result["data_quality"] == "HIGH"


def test_outcome_labeling_downgrades_quality_for_unfinalized_bars(monkeypatch):
    """A future bar that is NOT YET finalized (no revision, too recent) must be marked
    UNTRUSTED, even though it resolves the outcome (a TP/SL touch was technically found)."""
    SessionLocal = _session_factory(monkeypatch)
    entry_time = datetime.now(timezone.utc) - timedelta(minutes=20)
    bar_time = entry_time + timedelta(minutes=15)
    with SessionLocal() as db:
        db.add(MT5CanonicalCandleORM(
            candle_id="MT5:EURUSD:M15:fresh", provider="MT5", canonical_symbol="EURUSD", broker_symbol="EURUSD",
            timeframe="M15", timestamp=bar_time, timestamp_utc=bar_time, open=1.1000, high=1.1300, low=1.0995, close=1.1250,
            quality="VALID", finalized=False, updated_at=datetime.now(timezone.utc),
        ))
        db.commit()

    result = outcomes.label_outcome(
        fingerprint_id="FP_UNFINALIZED", canonical_symbol="EURUSD", broker_symbol="EURUSD", direction="LONG",
        entry=1.1000, stop_loss=1.0980, take_profit=1.1200, entry_time=entry_time,
    )
    assert result["data_quality"] == "UNTRUSTED"


# --- 2. fp-v2 peer grouping: reduced fragmentation without over-generalizing ---------------------


def test_similarity_only_dimensions_no_longer_fragment_peer_group():
    """Two setups differing ONLY on a similarity-only dimension (session/HTF trend/SMC presence)
    must land in the SAME peer group under fp-v2 -- proving the redesign actually reduces
    fragmentation, not just claims to."""
    ctx = _ctx()
    base_kwargs = dict(ctx=ctx, strategy_id="mtfai1", contributing_strategies=["mtfai1"], strategy_family="trend_multi_timeframe",
                        strategy_version="replay-v1", source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False,
                        entry=1.1010, stop_loss=1.0990, take_profit=1.1050)
    fp_morning = fingerprint_mod.build_fingerprint(entry_time=NOW.replace(hour=3), **base_kwargs)
    fp_evening = fingerprint_mod.build_fingerprint(entry_time=NOW.replace(hour=18), **base_kwargs)
    assert fp_morning["session"] != fp_evening["session"]  # confirms the dimension really differs
    assert fp_morning["peer_group_hash"] == fp_evening["peer_group_hash"]  # yet still one peer group


def test_hard_match_dimensions_still_separate_peer_groups():
    """Symbol, direction, strategy, and regime_broad must NEVER be collapsed together --
    over-generalization would be just as wrong as over-fragmentation."""
    ctx = _ctx()
    base_kwargs = dict(ctx=ctx, contributing_strategies=["mtfai1"], strategy_family="trend_multi_timeframe",
                        strategy_version="replay-v1", source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False,
                        entry=1.1010, stop_loss=1.0990, take_profit=1.1050, entry_time=NOW)
    fp_mtfai1 = fingerprint_mod.build_fingerprint(strategy_id="mtfai1", **base_kwargs)
    fp_momentum = fingerprint_mod.build_fingerprint(strategy_id="momentum", **base_kwargs)
    assert fp_mtfai1["peer_group_hash"] != fp_momentum["peer_group_hash"]

    fp_short = fingerprint_mod.build_fingerprint(strategy_id="mtfai1", entry=1.1010, stop_loss=1.1030, take_profit=1.0970,
                                                  ctx=ctx, contributing_strategies=["mtfai1"], strategy_family="trend_multi_timeframe",
                                                  strategy_version="replay-v1", source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, entry_time=NOW)
    assert fp_mtfai1["peer_group_hash"] != fp_short["peer_group_hash"]  # LONG vs SHORT


# --- 3. similarity determinism / weighting / effective sample size --------------------------------


def test_similarity_score_is_deterministic():
    query = {"regime": "trending_up", "atr_regime": "NORMAL", "session": "LONDON"}
    candidate = {"regime": "trending_up", "atr_regime": "NORMAL", "session": "NY"}
    a = similarity.similarity_score(query, candidate)
    b = similarity.similarity_score(query, candidate)
    assert a == b
    assert 0.0 <= a <= 1.0


def test_similarity_score_weighting_reflects_dimension_importance():
    query = {"regime": "trending_up", "atr_regime": "NORMAL"}
    matches_high_weight_dim = {"regime": "trending_up", "atr_regime": "HIGH"}  # matches the heavier-weighted dim
    matches_low_weight_dim = {"regime": "ranging", "atr_regime": "NORMAL"}  # matches the lighter-weighted dim
    score_high = similarity.similarity_score(query, matches_high_weight_dim)
    score_low = similarity.similarity_score(query, matches_low_weight_dim)
    # regime's weight (3.0) exceeds atr_regime's weight (2.0) in DIMENSION_WEIGHTS -- matching
    # the heavier dimension must score higher.
    assert score_high > score_low


def test_similarity_ignores_query_fields_the_query_does_not_have():
    query = {"regime": "trending_up"}  # only one dimension known
    candidate_full_match = {"regime": "trending_up", "atr_regime": "EXTREME", "session": "OTHER"}
    assert similarity.similarity_score(query, candidate_full_match) == 1.0  # perfect on the ONE dimension queried


def test_effective_sample_size_never_exceeds_raw_neighbor_count(monkeypatch):
    """Effective sample size is a SUM OF SIMILARITY WEIGHTS (each <= 1.0) -- it can never exceed
    the raw neighbor count, proving it is not a way to inflate the apparent sample size."""
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(5):
            # Spaced a day apart -- similarity.py's temporal-cluster dedup (added for the
            # multi-neighbor analog directive) correctly collapses same-move, near-identical
            # timestamps into one representative; this test is about weight<=1 per neighbor, so
            # the fixture must not itself trigger that (separate, dedicated) dedup behavior.
            entry_time = NOW + timedelta(days=i)
            db.add(HistoricalPatternFingerprintORM(
                fingerprint_id=f"FP_SIM_{i}", historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v2",
                source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol="EURUSD", direction="LONG",
                anchor_strategy="mtfai1", contributing_strategies=["mtfai1"], regime="trending_up", session="LONDON",
                liquidity_location="none", fvg_state="none", order_block_state="none", spread_regime="unknown",
                entry=1.1, stop_loss=1.09, take_profit=1.12, entry_time=entry_time, peer_group_hash=f"H{i}", created_at=entry_time,
            ))
            db.add(HistoricalSetupOutcomeORM(outcome_id=f"OUT_SIM_{i}", fingerprint_id=f"FP_SIM_{i}", resolution_status="RESOLVED", outcome_r=1.0, data_quality="HIGH", bars_scanned=5, created_at=entry_time))
        db.commit()

    stats = similarity.similarity_statistics(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims={"regime": "trending_up", "session": "LONDON"})
    assert stats["effective_sample_size"] <= stats["raw_neighbor_count"]
    assert stats["raw_neighbor_count"] == 5


# --- 4. replay trust promotion from fresh evidence -------------------------------------------------


def test_trust_promotion_moves_strategy_to_active_with_strong_new_evidence(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(25):
            db.add(HistoricalReplayParityCheckORM(
                check_id=f"CHK_{i}", live_evaluation_id=f"EVAL_{i}", canonical_symbol="EURUSD", strategy_id="test_strategy",
                verdict="EXACT_MATCH", diff_detail={}, created_at=NOW + timedelta(seconds=i),
            ))
        db.commit()

    before = trust_gating.get_trust_state("test_strategy")
    assert before["trust_state"] == trust_gating.HIST_INTEL_UNTRUSTED_REPLAY  # no evidence yet -> untrusted by default

    results = trust_gating.recompute_trust()
    assert results["test_strategy"]["trust_state"] == trust_gating.HIST_INTEL_ACTIVE

    after = trust_gating.get_trust_state("test_strategy")
    assert after["trust_state"] == trust_gating.HIST_INTEL_ACTIVE
    assert after["sample_count"] == 25


def test_trust_recompute_uses_only_latest_check_per_evaluation(monkeypatch):
    """Re-running parity against the SAME evaluation_id after a code fix must not let the STALE
    verdict continue to count alongside the fresh one."""
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        db.add(HistoricalReplayParityCheckORM(check_id="OLD", live_evaluation_id="EVAL_DUP", canonical_symbol="EURUSD", strategy_id="dup_strategy", verdict="REPLAY_MISSING", diff_detail={}, created_at=NOW))
        db.add(HistoricalReplayParityCheckORM(check_id="NEW", live_evaluation_id="EVAL_DUP", canonical_symbol="EURUSD", strategy_id="dup_strategy", verdict="EXACT_MATCH", diff_detail={}, created_at=NOW + timedelta(minutes=5)))
        db.commit()

    results = trust_gating.recompute_trust()
    assert results["dup_strategy"]["sample_count"] == 1  # deduped to one evaluation
    assert results["dup_strategy"]["exact_match_pct"] == 100.0  # only the LATEST verdict counted


# --- 5. adaptive statistics use only resolved, non-pending post-exit data --------------------------


def test_adaptive_state_statistics_excludes_pending_post_exit(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        db.add(AdaptivePositionBaselineORM(position_id="POS1", account_id="demo_10k", symbol="EURUSD", direction="LONG", original_strategy="mtfai1", created_at=NOW))
        db.add(AdaptiveManagementEventORM(
            event_id="EVT1", account_id="demo_10k", cycle_run_id="C1", created_at=NOW, position_id="POS1", symbol="EURUSD", direction="LONG",
            current_r=0.5, max_achieved_r=0.6, min_achieved_r=0.0, action_type="HOLD", action_category="NO_ACTION", action_status="OK",
            market_regime="trending_up", is_at_or_beyond_breakeven=False, is_trailing_action=False,
        ))
        db.add(AdaptiveManagerCounterfactualORM(position_id="POS1", account_id="demo_10k", symbol="EURUSD", post_exit_status="PENDING"))
        db.commit()

    fields = adaptive_fingerprint.build_state_fingerprint(
        strategy="mtfai1", symbol="EURUSD", direction="LONG", original_regime=None, current_regime="trending_up",
        current_r=0.5, max_achieved_r=0.6, min_achieved_r=0.0, elapsed_seconds=0, is_at_or_beyond_breakeven=False, is_trailing_action=False,
        now=NOW,  # must match event.created_at used internally by adaptive_statistics._collect_rows
    )
    stats = adaptive_statistics.state_statistics(fields["peer_group_hash"])
    assert stats["total_states_observed"] == 1
    assert stats["resolved_sample_size"] == 0  # PENDING post-exit must never count as resolved
