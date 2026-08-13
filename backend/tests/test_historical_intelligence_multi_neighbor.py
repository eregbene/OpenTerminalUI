"""Multi-neighbor historical analog intelligence regression tests.

Covers: deterministic top-K retrieval, weighted similarity ranking, hard-match enforcement
(symbol/direction/strategy/strategy_version/regime_broad), effective sample size never exceeding
raw neighbor count, weak neighbors never inflating confidence, temporal-cluster dedup, exact vs
very-close-match distinction, time-decay (off by default, opt-in), regime incompatibility
(NO_GOOD_HISTORICAL_ANALOG), and symbol-generalization weighting."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.historical_intelligence import similarity
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.shared.db import Base

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(similarity, "SessionLocal", SessionLocal)
    return SessionLocal


def _seed(db, *, idx: int, symbol: str = "EURUSD", direction: str = "LONG", strategy: str = "mtfai1",
          regime: str = "trending_up", regime_broad: str = "TRENDING", session: str = "LONDON",
          h1_trend: str = "bullish", entry_offset_hours: float | None = None, outcome_r: float = 1.0):
    # 48h apart by default (idx-driven) -> never clusters; entry_offset_hours overrides with an
    # explicit offset from NOW (still requires a unique idx for the fingerprint_id/outcome_id PK).
    entry_time = NOW + timedelta(hours=entry_offset_hours if entry_offset_hours is not None else idx * 48)
    db.add(HistoricalPatternFingerprintORM(
        fingerprint_id=f"FP{idx}", historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v2",
        source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol=symbol, direction=direction,
        anchor_strategy=strategy, contributing_strategies=[strategy], liquidity_location="none", fvg_state="none", order_block_state="none",
        spread_regime="unknown", regime=regime, regime_broad=regime_broad, session=session, h1_trend=h1_trend,
        entry=1.1, stop_loss=1.09, take_profit=1.12, entry_time=entry_time, peer_group_hash="H", created_at=entry_time,
    ))
    db.add(HistoricalSetupOutcomeORM(
        outcome_id=f"OUT{idx}", fingerprint_id=f"FP{idx}", resolution_status="RESOLVED", outcome_r=outcome_r, data_quality="HIGH", bars_scanned=5,
        created_at=entry_time, reached_0_25r=outcome_r > 0, reached_0_5r=outcome_r > 0, reached_1r=outcome_r >= 1, tp_hit=outcome_r > 0, sl_hit=outcome_r < 0,
    ))


QUERY = {"regime": "trending_up", "session": "LONDON", "h1_trend": "bullish"}


# --- deterministic top-K retrieval ------------------------------------------------------------


def test_top_k_retrieval_is_deterministic_and_respects_k(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(30):
            _seed(db, idx=i)
        db.commit()

    a = similarity.find_similar_setups(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY, top_k=10)
    b = similarity.find_similar_setups(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY, top_k=10)
    assert len(a) == 10
    assert [n["fingerprint"].fingerprint_id for n in a] == [n["fingerprint"].fingerprint_id for n in b]


def test_top_k_20_50_100_all_supported(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(120):
            _seed(db, idx=i)
        db.commit()

    for k in (20, 50, 100):
        result = similarity.find_similar_setups(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY, top_k=k)
        assert len(result) == k


# --- hard-match enforcement ---------------------------------------------------------------------


def test_hard_match_enforces_symbol_direction_strategy(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(15):
            _seed(db, idx=i, symbol="EURUSD")
        for i in range(15, 20):
            _seed(db, idx=i, symbol="GBPUSD")  # different symbol -- must not appear
        for i in range(20, 25):
            _seed(db, idx=i, direction="SHORT")  # different direction -- must not appear
        for i in range(25, 30):
            _seed(db, idx=i, strategy="momentum")  # different strategy -- must not appear
        db.commit()

    result = similarity.find_similar_setups(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY, top_k=100)
    assert len(result) == 15
    assert all(n["fingerprint"].canonical_symbol == "EURUSD" and n["fingerprint"].direction == "LONG" and n["fingerprint"].anchor_strategy == "mtfai1" for n in result)


def test_regime_broad_hard_filter_prevents_range_vs_trend_blending(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(15):
            _seed(db, idx=i, regime_broad="TRENDING")
        for i in range(15, 30):
            _seed(db, idx=i, regime_broad="RANGING")
        db.commit()

    result = similarity.find_similar_setups(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY, regime_broad="TRENDING", top_k=100)
    assert len(result) == 15
    assert all(n["fingerprint"].regime_broad == "TRENDING" for n in result)


def test_no_good_historical_analog_when_regime_incompatible(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(15):
            _seed(db, idx=i, regime_broad="RANGING")
        db.commit()

    stats = similarity.similarity_statistics(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY, regime_broad="TRENDING")
    assert stats["status"] == similarity.NO_GOOD_HISTORICAL_ANALOG
    assert stats["effective_sample_size"] == 0.0


# --- effective sample size / weak neighbors never inflate confidence ---------------------------


def test_effective_sample_size_never_exceeds_raw_count(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(20):
            _seed(db, idx=i)
        db.commit()

    stats = similarity.similarity_statistics(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY)
    assert stats["effective_sample_size"] <= stats["raw_neighbor_count"]


def test_weak_neighbors_contribute_less_than_strong_ones(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(10):
            _seed(db, idx=i, session="LONDON")  # matches query exactly on session
        for i in range(10, 20):
            _seed(db, idx=i, session="ASIAN", h1_trend="bearish")  # mismatches session AND h1_trend
        db.commit()

    stats = similarity.similarity_statistics(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY, min_similarity=0.0, top_k=100)
    # weaker neighbors still count (min_similarity=0.0) but contribute less effective weight than a same-count-of-strong-neighbors scenario would
    assert stats["effective_sample_size"] < stats["raw_neighbor_count"]


def test_very_close_matches_distinct_from_raw_neighbor_count(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(10):
            _seed(db, idx=i, session="LONDON", h1_trend="bullish")  # exact match -> very close
        for i in range(10, 20):
            _seed(db, idx=i, session="ASIAN", h1_trend="bearish")  # weaker match
        db.commit()

    stats = similarity.similarity_statistics(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY, min_similarity=0.0, top_k=100)
    assert stats["very_close_matches"] <= stats["raw_neighbor_count"]
    assert stats["very_close_matches"] >= 10  # the 10 exact-matching ones should clear the very-close bar


# --- temporal cluster dedup ---------------------------------------------------------------------


def test_temporal_cluster_dedup_collapses_same_move(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        # 10 fingerprints all within a few hours of each other -- one contiguous market move
        for i in range(10):
            _seed(db, idx=i, entry_offset_hours=i * 0.5)
        db.commit()

    result = similarity.find_similar_setups(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY, top_k=100)
    assert len(result) == 1  # collapsed to the single best representative


def test_temporal_cluster_dedup_keeps_genuinely_separate_moves(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(5):
            _seed(db, idx=i)  # 48h apart -- separate moves
        db.commit()

    result = similarity.find_similar_setups(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY, top_k=100)
    assert len(result) == 5


def test_dedup_can_be_disabled(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i in range(10):
            _seed(db, idx=i, entry_offset_hours=i * 0.5)
        db.commit()

    result = similarity.find_similar_setups(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY, top_k=100, dedup_clusters=False)
    assert len(result) == 10


# --- time decay (off by default) ----------------------------------------------------------------


def test_time_decay_off_by_default_gives_full_weight_regardless_of_age(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, idx=0, entry_offset_hours=0)  # "old" (48h before idx=1 in this spacing scheme is irrelevant here)
        db.commit()

    result = similarity.find_similar_setups(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY, top_k=10)
    assert result[0]["recency_weight"] == 1.0
    assert result[0]["effective_weight"] == result[0]["similarity"]


def test_time_decay_when_enabled_reduces_older_neighbor_weight(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, idx=0)  # far in the past relative to "now" (real wall-clock)
        db.commit()

    result = similarity.find_similar_setups(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY, top_k=10, half_life_days=30)
    assert result[0]["recency_weight"] < 1.0
    assert result[0]["effective_weight"] <= result[0]["similarity"]


# --- symbol generalization (built, opt-in, weighted lower) --------------------------------------


def test_symbol_generalization_off_by_default(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, idx=0, symbol="GBPUSD")
        db.commit()

    result = similarity.find_similar_setups(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY, top_k=10)
    assert result == []  # GBPUSD never appears in an EURUSD search unless explicitly allowed


def test_symbol_generalization_when_enabled_applies_penalty(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        _seed(db, idx=0, symbol="EURUSD")
        _seed(db, idx=1, symbol="GBPUSD")
        db.commit()

    result = similarity.find_similar_setups(canonical_symbol="EURUSD", direction="LONG", anchor_strategy="mtfai1", strategy_version="replay-v1", query_dims=QUERY, top_k=10, allow_related_symbols=True, related_symbol_penalty=0.5, min_similarity=0.0)
    by_symbol = {n["fingerprint"].canonical_symbol: n["similarity"] for n in result}
    assert by_symbol["EURUSD"] > by_symbol["GBPUSD"]  # same underlying match quality, but GBPUSD penalized
    assert by_symbol["GBPUSD"] == round(by_symbol["EURUSD"] * 0.5, 4)


# --- pure similarity_score function --------------------------------------------------------------


def test_similarity_score_ignores_dimensions_query_does_not_have():
    score = similarity.similarity_score({"regime": "trending_up"}, {"regime": "trending_up", "session": "ASIAN"})
    assert score == 1.0  # session mismatch never penalized since the query didn't specify it


def test_similarity_score_is_deterministic():
    q = {"regime": "trending_up", "session": "LONDON", "h1_trend": "bullish"}
    c = {"regime": "trending_up", "session": "ASIAN", "h1_trend": "bullish"}
    assert similarity.similarity_score(q, c) == similarity.similarity_score(q, c)
