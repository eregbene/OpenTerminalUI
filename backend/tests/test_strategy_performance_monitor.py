"""Strategy performance monitor (2026-08-18) -- recurring, decision-support-only re-evaluation
of each strategy's recent real/shadow-tracked performance. Covers: real-trade stats computation
(position-level, no duplication), shadow-tracking stats computation, the demote/promote/no-change
decision thresholds (including the insufficient-sample guard and the asymmetric hysteresis
between demote and promote bars), and that a recommendation NEVER touches activation -- only
persists a PENDING_REVIEW row.
"""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone

import pytest

from backend.adaptive_management.orm import AdaptivePositionStateORM
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.mt5_strategies.orm import StrategyPerformanceRecommendationORM
from backend.mt5_strategies import performance_monitor
from backend.mt5_strategies.performance_monitor import (
    DEMOTE_EXPECTANCY_R_THRESHOLD,
    MIN_SAMPLE_FOR_RECOMMENDATION,
    PROMOTE_EXPECTANCY_R_THRESHOLD,
    _decide,
    _persist,
    _real_trade_stats,
    _real_trade_stats_by_symbol,
    _shadow_stats,
    compute_recommendations,
    performance_memory_for_confidence,
)
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite

NOW = datetime.now(timezone.utc)
_id_counter = itertools.count()


def _position(strategy_id: str, *, realized_r: float, risk_money: float = 100.0, closed_days_ago: int = 1, symbol: str = "EURUSD") -> AdaptivePositionStateORM:
    # Matches real production semantics (verified directly against closed positions):
    # max_achieved_r floors at 0 (never goes negative even for a straight-to-loss trade), and
    # current_giveback_r captures the full adverse excursion regardless of whether there was
    # ever a favorable peak to give back from -- so realized_r = max_achieved_r - giveback still
    # recovers the real outcome (e.g. max_achieved_r=0.0, giveback=0.99 => realized_r=-0.99).
    max_achieved_r = max(realized_r, 0.0)
    giveback_r = max_achieved_r - realized_r
    return AdaptivePositionStateORM(
        position_id=f"POS_{strategy_id}_{next(_id_counter)}",
        symbol=symbol, direction="LONG", broker_ticket="1", opened_at=NOW - timedelta(days=closed_days_ago, hours=1),
        original_volume=0.1, current_volume=0.1, entry_price=1.1000, original_sl=1.0950, current_sl=1.0950,
        strategy_id=strategy_id, closed_detected_at=NOW - timedelta(days=closed_days_ago),
        max_achieved_r=max_achieved_r, current_giveback_r=giveback_r,
        original_risk_money=risk_money, account_id="demo_10k",
    )


def _shadow_eval(strategy_id: str, *, realized_r: float, realized_pnl: float, days_ago: int = 1) -> MT5CandidateEvaluationORM:
    seq = next(_id_counter)
    return MT5CandidateEvaluationORM(
        evaluation_id=f"EVAL_{strategy_id}_{seq}", cycle_id="C1", account_id="demo_10k",
        candidate_id=f"CAND_{strategy_id}_{seq}", created_at=NOW - timedelta(days=days_ago),
        symbol="EURUSD", broker_symbol="EURUSD", direction="LONG", timeframe="M15", strategy=strategy_id,
        overall_confidence=75.0, confidence_band="MODERATE", outcome_type="SHADOW", realized_r=realized_r, realized_pnl=realized_pnl,
    )


def test_real_trade_stats_computes_position_level_r_and_usd(monkeypatch: pytest.MonkeyPatch):
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with SessionLocal() as db:
        db.add(_position("ema_trend", realized_r=1.0, risk_money=50.0))
        db.add(_position("ema_trend", realized_r=-1.0, risk_money=50.0))
        db.commit()

    stats = _real_trade_stats("ema_trend", NOW - timedelta(days=14))
    assert stats["sample_size"] == 2
    assert stats["expectancy_r"] == 0.0
    assert stats["realized_usd"] == 0.0


def test_real_trade_stats_excludes_fused_combo_rows(monkeypatch: pytest.MonkeyPatch):
    """A position whose strategy_id is a fused label ("ema_trend+momentum") must never be
    counted toward ema_trend's own solo performance -- see module docstring on attribution."""
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with SessionLocal() as db:
        db.add(_position("ema_trend", realized_r=1.0))
        db.add(_position("ema_trend+momentum", realized_r=-5.0))
        db.commit()

    stats = _real_trade_stats("ema_trend", NOW - timedelta(days=14))
    assert stats["sample_size"] == 1
    assert stats["expectancy_r"] == 1.0


def test_version_cutover_excludes_pre_cutover_trades_from_strategy_performance(monkeypatch: pytest.MonkeyPatch):
    # 2026-08-25 MTFAI1 V2 confidence-calibration audit: a version-cutover strategy must not
    # inherit its predecessor's real-trade history for confidence/recommendation purposes. Real
    # incident this guards: mtfai1 V2 (activated 2026-08-24) was being scored, via
    # strategy_performance, against the 140 real V1 trades (41% win rate) that caused the
    # 2026-08-21 SHADOW_MT5 demotion in the first place -- the exact evidence V2 supersedes.
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    cutover = NOW - timedelta(days=1)
    monkeypatch.setitem(performance_monitor.STRATEGY_VERSION_CUTOVER, "mtfai1", cutover)
    with SessionLocal() as db:
        # Pre-cutover: 20 losing V1 trades -- must be fully excluded.
        for _ in range(20):
            db.add(_position("mtfai1", realized_r=-1.0, closed_days_ago=5))
        # Post-cutover: 2 winning V2 trades -- the only ones that should count.
        db.add(_position("mtfai1", realized_r=1.0, closed_days_ago=0))
        db.add(_position("mtfai1", realized_r=1.0, closed_days_ago=0))
        db.commit()

    stats = _real_trade_stats("mtfai1", NOW - timedelta(days=14))

    assert stats["sample_size"] == 2
    assert stats["expectancy_r"] == 1.0


def test_version_cutover_falls_back_to_neutral_when_no_post_cutover_sample(monkeypatch: pytest.MonkeyPatch):
    # Exactly the live state right now: mtfai1 V2 has real V1 history in the 14d window but zero
    # trades since its own cutover -- performance_memory_for_confidence must return None (the
    # existing "no recorded history, neutral default" path), never a V1-derived score.
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    cutover = NOW - timedelta(hours=1)
    monkeypatch.setitem(performance_monitor.STRATEGY_VERSION_CUTOVER, "mtfai1", cutover)
    monkeypatch.setattr(performance_monitor, "activation_status", lambda strategy_id: performance_monitor.ACTIVE_MT5)
    with SessionLocal() as db:
        for _ in range(30):
            db.add(_position("mtfai1", realized_r=-1.0, closed_days_ago=3))
        db.commit()

    memory = performance_memory_for_confidence(strategy_id="mtfai1")

    assert memory is None


def test_strategy_without_a_registered_cutover_is_unaffected(monkeypatch: pytest.MonkeyPatch):
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    assert "ema_trend" not in performance_monitor.STRATEGY_VERSION_CUTOVER
    with SessionLocal() as db:
        db.add(_position("ema_trend", realized_r=1.0, closed_days_ago=10))
        db.commit()

    stats = _real_trade_stats("ema_trend", NOW - timedelta(days=14))

    assert stats["sample_size"] == 1


def test_symbol_performance_prefers_strategy_symbol_tier_over_cross_strategy(monkeypatch: pytest.MonkeyPatch):
    # 2026-08-25 Confidence Architecture & Calibration Audit (Part 4): symbol_performance must
    # not reward/penalize an ema_trend EURUSD candidate because an unrelated strategy won/lost
    # EURUSD. The strategy+symbol tier (10 losing ema_trend EURUSD trades) must win over the
    # cross-strategy symbol-only tier (10 winning trades from a DIFFERENT strategy on EURUSD).
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    monkeypatch.setattr(performance_monitor, "activation_status", lambda strategy_id: performance_monitor.ACTIVE_MT5)
    with SessionLocal() as db:
        for _ in range(10):
            db.add(_position("ema_trend", realized_r=-1.0, symbol="EURUSD"))
        for _ in range(10):
            db.add(_position("trend_pullback", realized_r=1.0, symbol="EURUSD"))
        db.commit()

    memory = performance_memory_for_confidence(strategy_id="ema_trend", symbol="EURUSD")

    assert memory is not None
    assert memory["closed_trade_count"] == 10
    assert memory["expectancy"] == pytest.approx(-1.0)  # the ema_trend-only losses, not the mixed pool


def test_symbol_performance_falls_back_to_cross_strategy_when_strategy_has_no_symbol_history(monkeypatch: pytest.MonkeyPatch):
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    monkeypatch.setattr(performance_monitor, "activation_status", lambda strategy_id: performance_monitor.ACTIVE_MT5)
    with SessionLocal() as db:
        # ema_trend has never traded EURUSD -- only trend_pullback has.
        for _ in range(10):
            db.add(_position("trend_pullback", realized_r=1.0, symbol="EURUSD"))
        db.commit()

    memory = performance_memory_for_confidence(strategy_id="ema_trend", symbol="EURUSD")

    assert memory is not None
    assert memory["closed_trade_count"] == 10
    assert memory["expectancy"] == pytest.approx(1.0)  # the cross-strategy fallback tier


def test_symbol_performance_strategy_tier_respects_version_cutover(monkeypatch: pytest.MonkeyPatch):
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    cutover = NOW - timedelta(days=1)
    monkeypatch.setitem(performance_monitor.STRATEGY_VERSION_CUTOVER, "mtfai1", cutover)
    with SessionLocal() as db:
        for _ in range(20):
            db.add(_position("mtfai1", realized_r=-1.0, symbol="EURUSD", closed_days_ago=5))
        db.add(_position("mtfai1", realized_r=1.0, symbol="EURUSD", closed_days_ago=0))
        db.commit()

    memory = performance_memory_for_confidence(strategy_id="mtfai1", symbol="EURUSD")

    assert memory is not None
    assert memory["closed_trade_count"] == 1
    assert memory["expectancy"] == pytest.approx(1.0)


def test_symbol_only_call_shape_still_pools_across_strategies(monkeypatch: pytest.MonkeyPatch):
    """Backward compatibility: a caller that still passes only `symbol` (no strategy_id) keeps
    the original cross-strategy behavior unchanged."""
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with SessionLocal() as db:
        db.add(_position("ema_trend", realized_r=-1.0, symbol="EURUSD"))
        db.add(_position("trend_pullback", realized_r=1.0, symbol="EURUSD"))
        db.commit()

    memory = performance_memory_for_confidence(symbol="EURUSD")

    assert memory is not None
    assert memory["closed_trade_count"] == 2


def test_real_trade_stats_excludes_outside_window(monkeypatch: pytest.MonkeyPatch):
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with SessionLocal() as db:
        db.add(_position("ema_trend", realized_r=1.0, closed_days_ago=1))
        db.add(_position("ema_trend", realized_r=-1.0, closed_days_ago=30))
        db.commit()

    stats = _real_trade_stats("ema_trend", NOW - timedelta(days=14))
    assert stats["sample_size"] == 1
    assert stats["expectancy_r"] == 1.0


def test_shadow_stats_uses_realized_r(monkeypatch: pytest.MonkeyPatch):
    # Uses a strategy with no registered STRATEGY_VERSION_CUTOVER (see that dict's own tests
    # below) -- "mtfai1" specifically now has one (2026-08-24 18:44 UTC), and this test's default
    # closed_days_ago=1 can fall on either side of that real wall-clock cutover depending on when
    # the suite runs, which is not what this test is about.
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with SessionLocal() as db:
        db.add(_shadow_eval("ema_trend", realized_r=0.5, realized_pnl=25.0))
        db.add(_shadow_eval("ema_trend", realized_r=-1.0, realized_pnl=-50.0))
        db.commit()

    stats = _shadow_stats("ema_trend", NOW - timedelta(days=14))
    assert stats["sample_size"] == 2
    assert stats["expectancy_r"] == pytest.approx(-0.25)
    assert stats["realized_usd"] == pytest.approx(-25.0)


def test_decide_insufficient_sample_never_recommends_change():
    stats = {"sample_size": MIN_SAMPLE_FOR_RECOMMENDATION - 1, "expectancy_r": -5.0, "realized_usd": -500.0, "avg_realized_usd": -50.0, "win_rate": 0.1}
    recommended, reasoning = _decide(current="ACTIVE_MT5", stats=stats)
    assert recommended == "ACTIVE_MT5"
    assert "INSUFFICIENT_SAMPLE" in reasoning


def test_decide_recommends_demotion_for_active_losing_strategy():
    stats = {"sample_size": 50, "expectancy_r": DEMOTE_EXPECTANCY_R_THRESHOLD - 0.05, "realized_usd": -500.0, "avg_realized_usd": -10.0, "win_rate": 0.2}
    recommended, reasoning = _decide(current="ACTIVE_MT5", stats=stats)
    assert recommended == "SHADOW_MT5"
    assert "demot" in reasoning.lower() or "SHADOW_MT5" in reasoning


def test_decide_never_recommends_disabled_only_shadow():
    stats = {"sample_size": 500, "expectancy_r": -2.0, "realized_usd": -5000.0, "avg_realized_usd": -50.0, "win_rate": 0.05}
    recommended, _ = _decide(current="ACTIVE_MT5", stats=stats)
    assert recommended == "SHADOW_MT5"
    assert recommended != "DISABLED"


def test_decide_recommends_promotion_for_shadow_winning_strategy():
    stats = {"sample_size": 30, "expectancy_r": PROMOTE_EXPECTANCY_R_THRESHOLD + 0.05, "realized_usd": 400.0, "avg_realized_usd": 13.0, "win_rate": 0.6}
    recommended, reasoning = _decide(current="SHADOW_MT5", stats=stats)
    assert recommended == "ACTIVE_MT5"


def test_decide_asymmetric_thresholds_damp_flip_flop():
    """A mildly positive shadow strategy (above the demote bar but below the promote bar) must
    NOT be recommended for reinstatement -- the promote bar is deliberately higher than the
    demote bar to avoid oscillating a strategy back and forth on noise near zero."""
    mildly_positive_r = (DEMOTE_EXPECTANCY_R_THRESHOLD + PROMOTE_EXPECTANCY_R_THRESHOLD) / 2
    assert DEMOTE_EXPECTANCY_R_THRESHOLD < mildly_positive_r < PROMOTE_EXPECTANCY_R_THRESHOLD
    stats = {"sample_size": 30, "expectancy_r": mildly_positive_r, "realized_usd": 10.0, "avg_realized_usd": 0.3, "win_rate": 0.4}
    recommended, _ = _decide(current="SHADOW_MT5", stats=stats)
    assert recommended == "SHADOW_MT5"


def test_decide_no_change_when_active_and_positive():
    stats = {"sample_size": 40, "expectancy_r": 0.5, "realized_usd": 500.0, "avg_realized_usd": 12.5, "win_rate": 0.6}
    recommended, reasoning = _decide(current="ACTIVE_MT5", stats=stats)
    assert recommended == "ACTIVE_MT5"
    assert "no change" in reasoning.lower()


def test_compute_recommendations_never_touches_activation(monkeypatch: pytest.MonkeyPatch):
    """The one non-negotiable invariant: computing (and persisting) recommendations must never
    itself change what activation_status() returns for any strategy."""
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    monkeypatch.delenv("MT5_STRATEGY_ACTIVATION_EMA_TREND", raising=False)
    with SessionLocal() as db:
        for i in range(25):
            db.add(_position("ema_trend", realized_r=-1.0, closed_days_ago=1))
        db.commit()

    from backend.mt5_strategies.models import activation_status

    before = activation_status("ema_trend")
    recs = compute_recommendations()
    written = _persist(recs)
    after = activation_status("ema_trend")

    assert before == after == "ACTIVE_MT5"
    ema_rec = next(r for r in recs if r["strategy_id"] == "ema_trend")
    assert ema_rec["recommended_activation"] == "SHADOW_MT5"  # the RECOMMENDATION differs...
    assert written > 0

    with SessionLocal() as db:
        row = db.query(StrategyPerformanceRecommendationORM).filter(StrategyPerformanceRecommendationORM.strategy_id == "ema_trend").first()
        assert row is not None
        assert row.status == "PENDING_REVIEW"
        assert row.recommended_activation == "SHADOW_MT5"
        assert row.current_activation == "ACTIVE_MT5"  # ...but current_activation (the REAL state) is unchanged


def test_persist_preserves_human_review_decision_across_reruns(monkeypatch: pytest.MonkeyPatch):
    """Once a human has reviewed a recommendation (status != PENDING_REVIEW), a later run must
    refresh the numbers but must never silently reset that review decision."""
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    rec = {
        "strategy_id": "ema_trend", "data_source": "REAL_TRADES", "window_days": 14,
        "current_activation": "ACTIVE_MT5", "recommended_activation": "SHADOW_MT5", "reasoning": "first pass",
        "sample_size": 25, "win_rate": 0.1, "expectancy_r": -0.5, "realized_usd": -500.0, "avg_realized_usd": -20.0,
    }
    _persist([rec])
    with SessionLocal() as db:
        from datetime import datetime as _dt

        row = db.query(StrategyPerformanceRecommendationORM).filter(StrategyPerformanceRecommendationORM.strategy_id == "ema_trend").first()
        row.status = "REJECTED"
        row.reviewed_by = "user"
        row.reviewed_at = _dt.now(timezone.utc)
        db.commit()

    rec2 = {**rec, "reasoning": "second pass, fresher numbers"}
    _persist([rec2])
    with SessionLocal() as db:
        row = db.query(StrategyPerformanceRecommendationORM).filter(StrategyPerformanceRecommendationORM.strategy_id == "ema_trend").first()
        assert row.status == "REJECTED"
        assert row.reviewed_by == "user"
        assert row.reasoning == "second pass, fresher numbers"


# --- performance_memory_for_confidence (Priority 1 confidence-engine fix) -----------------
# strategy_performance/symbol_performance in backend/brokers/mt5/confidence.py used to be a
# constant neutral score because nothing ever wrote to MT5TradeMemorySnapshotORM. These prove
# the bridge into this module's real, already-authoritative real-trade/shadow ledger instead.


def test_real_trade_stats_by_symbol_groups_across_strategies(monkeypatch: pytest.MonkeyPatch):
    """symbol_performance is deliberately NOT strategy-scoped -- a symbol's real trade history
    should include every strategy that traded it, unlike strategy_performance's solo-only rule."""
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with SessionLocal() as db:
        db.add(_position("ema_trend", realized_r=1.0, symbol="GBPUSD"))
        db.add(_position("momentum", realized_r=-0.5, symbol="GBPUSD"))
        db.add(_position("ema_trend", realized_r=5.0, symbol="EURUSD"))  # different symbol, excluded
        db.commit()

    stats = _real_trade_stats_by_symbol("GBPUSD", NOW - timedelta(days=14))
    assert stats["sample_size"] == 2
    assert stats["expectancy_r"] == pytest.approx(0.25)


def test_performance_memory_for_confidence_requires_at_least_one_scope(monkeypatch: pytest.MonkeyPatch):
    # 2026-08-25 Part 4: passing BOTH strategy_id and symbol is now a valid, meaningful call
    # shape (the strategy+symbol hierarchy tier) -- only "neither given" is invalid. Superseded
    # the old "exactly one, never both" invariant this test used to assert.
    assert performance_memory_for_confidence() is None
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    monkeypatch.setattr(performance_monitor, "activation_status", lambda strategy_id: performance_monitor.ACTIVE_MT5)
    assert performance_memory_for_confidence(strategy_id="ema_trend", symbol="EURUSD") is None  # no evidence at either tier -> None, not an error


def test_performance_memory_for_confidence_returns_none_without_evidence(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    assert performance_memory_for_confidence(symbol="EURUSD") is None
    assert performance_memory_for_confidence(strategy_id="ema_trend") is None


def test_performance_memory_for_confidence_shapes_symbol_stats_for_the_confidence_component(monkeypatch: pytest.MonkeyPatch):
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    with SessionLocal() as db:
        for _ in range(5):
            db.add(_position("ema_trend", realized_r=1.0, symbol="EURUSD"))
        db.commit()

    memory = performance_memory_for_confidence(symbol="EURUSD")
    assert memory == {"closed_trade_count": 5, "win_rate": 1.0, "recommendation": "NEUTRAL", "expectancy": 1.0}


def test_performance_memory_for_confidence_flags_avoid_below_demote_threshold(monkeypatch: pytest.MonkeyPatch):
    # "ema_trend", not "mtfai1" -- see test_shadow_stats_uses_realized_r's comment on why a
    # version-cutover strategy is the wrong generic example for a test unrelated to versioning.
    # activation_status forced ACTIVE_MT5 so this exercises the REAL_TRADES path the seeded
    # AdaptivePositionStateORM rows are meant for, independent of whatever this strategy's real
    # MT5_STRATEGY_ACTIVATION_<ID> happens to be set to in the environment the suite runs in.
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    monkeypatch.setattr(performance_monitor, "activation_status", lambda strategy_id: performance_monitor.ACTIVE_MT5)
    with SessionLocal() as db:
        for _ in range(20):
            db.add(_position("ema_trend", realized_r=DEMOTE_EXPECTANCY_R_THRESHOLD - 0.5))
        db.commit()

    memory = performance_memory_for_confidence(strategy_id="ema_trend")
    assert memory is not None
    assert memory["recommendation"] == "AVOID"
    assert memory["closed_trade_count"] == 20


def test_performance_memory_for_confidence_flags_reduce_risk_when_mildly_negative(monkeypatch: pytest.MonkeyPatch):
    """Between 0 and DEMOTE_EXPECTANCY_R_THRESHOLD is a caution zone, not yet a full AVOID --
    mirrors confidence.py's own REDUCE_RISK cap being softer than AVOID's."""
    # activation_status forced ACTIVE_MT5 -- see the comment on the AVOID test above; this
    # strategy's real container-env activation is independent of what this test is verifying.
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    monkeypatch.setattr(performance_monitor, "activation_status", lambda strategy_id: performance_monitor.ACTIVE_MT5)
    mildly_negative_r = DEMOTE_EXPECTANCY_R_THRESHOLD / 2
    assert DEMOTE_EXPECTANCY_R_THRESHOLD < mildly_negative_r < 0
    with SessionLocal() as db:
        for _ in range(20):
            db.add(_position("vwap_reversion", realized_r=mildly_negative_r))
        db.commit()

    memory = performance_memory_for_confidence(strategy_id="vwap_reversion")
    assert memory is not None
    assert memory["recommendation"] == "REDUCE_RISK"


def test_performance_memory_for_confidence_uses_shadow_source_for_shadow_strategies(monkeypatch: pytest.MonkeyPatch):
    """A strategy currently SHADOW_MT5/DISABLED has no real trades -- its memory must come from
    the shadow-tracking ledger, not silently report no evidence."""
    SessionLocal = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    monkeypatch.setenv("MT5_STRATEGY_ACTIVATION_WYCKOFF", "SHADOW_MT5")
    with SessionLocal() as db:
        for _ in range(3):
            db.add(_shadow_eval("wyckoff", realized_r=0.8, realized_pnl=40.0))
        db.commit()

    memory = performance_memory_for_confidence(strategy_id="wyckoff")
    assert memory is not None
    assert memory["closed_trade_count"] == 3
    assert memory["expectancy"] == pytest.approx(0.8)
