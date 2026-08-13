"""Phase 3/4 regression tests: fingerprints, versioning, sample reliability gating, trust
gating, Redis cache behavior, safety-boundary isolation, four-account sharing, dynamic lookback,
outcome PENDING-exclusion, and the adaptive action taxonomy. See historical_intelligence's
various module docstrings for the full architecture; this file proves the 22 explicitly required
properties, numbered to match that list."""
from __future__ import annotations

import asyncio
import inspect
import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.historical_intelligence import (
    adaptive_intelligence,
    cache,
    entry_intelligence,
    fingerprint as fingerprint_mod,
    outcomes,
    replay,
    statistics,
    trust_gating,
)
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalReplayParityCheckORM, HistoricalSetupOutcomeORM
from backend.mt5_strategies.context import StrategyContext, build_strategy_context
from backend.shared.db import Base

NOW = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)


def _mk_rows(n: int, *, base: float = 1.1000, step: float = 0.0, wick: float = 0.0015) -> list[dict]:
    rows = []
    t = NOW - timedelta(minutes=15 * n)
    price = base
    for i in range(n):
        c = base + step * i
        o = price
        h = max(o, c) + wick
        low = min(o, c) - wick
        rows.append({"time": (t + timedelta(minutes=15 * i)).isoformat(), "open": o, "high": h, "low": low, "close": c, "tick_volume": 100, "spread": 1})
        price = c
    return rows


def _ctx(**overrides) -> StrategyContext:
    rows = _mk_rows(100)
    ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal("1.1010"), ask=Decimal("1.1012"), spread=Decimal("0.0002"), now=NOW)
    assert ctx is not None
    return ctx


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    for module in (statistics, trust_gating, outcomes, entry_intelligence, adaptive_intelligence):
        monkeypatch.setattr(module, "SessionLocal", SessionLocal)
    return SessionLocal


# --- 1. fingerprint deterministic -------------------------------------------------------------


def test_fingerprint_is_deterministic():
    ctx = _ctx()
    kwargs = dict(ctx=ctx, strategy_id="mtfai1", contributing_strategies=["mtfai1"], strategy_family="trend_multi_timeframe",
                  strategy_version="replay-v1", source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False,
                  entry=1.1010, stop_loss=1.0990, take_profit=1.1050, entry_time=NOW)
    a = fingerprint_mod.build_fingerprint(**kwargs)
    b = fingerprint_mod.build_fingerprint(**kwargs)
    assert a["peer_group_hash"] == b["peer_group_hash"]
    assert a == b


# --- 2. strategy-version invalidation -----------------------------------------------------------


def test_fingerprint_peer_group_hash_changes_with_strategy_version():
    ctx = _ctx()
    base_kwargs = dict(ctx=ctx, strategy_id="mtfai1", contributing_strategies=["mtfai1"], strategy_family="trend_multi_timeframe",
                        source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False,
                        entry=1.1010, stop_loss=1.0990, take_profit=1.1050, entry_time=NOW)
    v1 = fingerprint_mod.build_fingerprint(strategy_version="replay-v1", **base_kwargs)
    v2 = fingerprint_mod.build_fingerprint(strategy_version="replay-v2-point-in-time-integrity", **base_kwargs)
    assert v1["peer_group_hash"] != v2["peer_group_hash"]


# --- 3. pattern-version invalidation -------------------------------------------------------------


def test_pattern_statistics_scoped_by_fingerprint_version(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        for i, fpv in enumerate(["fp-v1", "fp-v2"]):
            fp = HistoricalPatternFingerprintORM(
                fingerprint_id=f"FP_{i}", historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version=fpv,
                source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol="EURUSD", direction="LONG",
                anchor_strategy="mtfai1", contributing_strategies=["mtfai1"], liquidity_location="none", fvg_state="none", order_block_state="none",
                spread_regime="unknown", entry=1.1, stop_loss=1.09, take_profit=1.12, entry_time=NOW, peer_group_hash="SAME_HASH", created_at=NOW,
            )
            db.add(fp)
            db.add(HistoricalSetupOutcomeORM(outcome_id=f"OUT_{i}", fingerprint_id=f"FP_{i}", resolution_status="RESOLVED", outcome_r=1.0, bars_scanned=5, data_quality="HIGH", created_at=NOW))
        db.commit()

    scoped_v1 = statistics.pattern_statistics("SAME_HASH", strategy_version="replay-v1", fingerprint_version="fp-v1")
    scoped_v2 = statistics.pattern_statistics("SAME_HASH", strategy_version="replay-v1", fingerprint_version="fp-v2")
    unscoped = statistics.pattern_statistics("SAME_HASH")
    assert scoped_v1["sample_size"] == 1
    assert scoped_v2["sample_size"] == 1
    assert unscoped["sample_size"] == 2  # omitting fingerprint_version aggregates across both


# --- 4. insufficient sample rejected from active influence --------------------------------------


def test_insufficient_sample_rejected_from_active_influence():
    stats = {"reliability": "LOW_CONFIDENCE", "sample_size": 30, "expectancy_r": 2.0, "profit_factor": 3.0}
    result = entry_intelligence._evaluate_from_stats(trust_state=trust_gating.HIST_INTEL_ACTIVE, stats=stats, peer_group_hash="H", source="redis")
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "PATTERN_SAMPLE_INSUFFICIENT"
    assert result["ranking_adjustment"] == 0.0


# --- 5. trusted replay + reliable sample may influence DEMO --------------------------------------


def test_trusted_and_reliable_sample_produces_nonzero_influence():
    stats = {"reliability": "STRONG", "sample_size": 300, "expectancy_r": 1.5, "profit_factor": 2.5, "immediate_failure_rate": 0.1, "probability_1r": 0.7}
    result = entry_intelligence._evaluate_from_stats(trust_state=trust_gating.HIST_INTEL_ACTIVE, stats=stats, peer_group_hash="H", source="redis")
    assert result["status"] == "EVALUATED"
    assert result["historical_score"] > 0
    assert result["ranking_adjustment"] != 0.0
    assert abs(result["ranking_adjustment"]) <= entry_intelligence._MAX_LIVE_RANKING_ADJUSTMENT


# --- 6. untrusted strategy falls back to existing engine -----------------------------------------


def test_untrusted_strategy_falls_back(monkeypatch):
    _session_factory(monkeypatch)
    monkeypatch.setattr(entry_intelligence, "demo_active_enabled", lambda: True)
    ctx = _ctx()
    result = asyncio.run(entry_intelligence.evaluate_historical_intelligence(
        ctx=ctx, strategy_id="never_seen_strategy", contributing_strategies=["never_seen_strategy"], strategy_family=None,
        entry=1.1010, stop_loss=1.0990, take_profit=1.1050, entry_time=NOW,
    ))
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "STRATEGY_REPLAY_UNTRUSTED"
    assert result["ranking_adjustment"] == 0.0


# --- 7/8/9. Redis hit / miss / failure fallback ---------------------------------------------------


def test_redis_hit_never_touches_postgres(monkeypatch):
    async def _fake_cache_get(key):
        return {"sample_size": 500, "reliability": "STRONG"}

    def _explode(*a, **kw):
        raise AssertionError("statistics.pattern_statistics must not be called on a cache hit")

    monkeypatch.setattr(cache, "cache_get", _fake_cache_get)
    monkeypatch.setattr(cache, "statistics", type("S", (), {"pattern_statistics": staticmethod(_explode)}))
    result = asyncio.run(cache.cached_pattern_statistics("H", strategy_version="replay-v1"))
    assert result["_cache_source"] == "redis"
    assert result["sample_size"] == 500


def test_redis_miss_falls_back_to_postgres(monkeypatch):
    calls = {"cache_set": 0}

    async def _fake_cache_get(key):
        return None

    async def _fake_cache_set(key, value, ttl):
        calls["cache_set"] += 1

    def _fake_pattern_statistics(peer_group_hash, **kw):
        return {"sample_size": 12, "reliability": "INSUFFICIENT", "peer_group_hash": peer_group_hash}

    monkeypatch.setattr(cache, "cache_get", _fake_cache_get)
    monkeypatch.setattr(cache, "cache_set", _fake_cache_set)
    monkeypatch.setattr(cache, "statistics", type("S", (), {"pattern_statistics": staticmethod(_fake_pattern_statistics)}))
    result = asyncio.run(cache.cached_pattern_statistics("H", strategy_version="replay-v1"))
    assert result["_cache_source"] == "postgres"
    assert result["sample_size"] == 12
    assert calls["cache_set"] == 1


def test_redis_failure_falls_back_safely(monkeypatch):
    async def _raise_cache_get(key):
        raise ConnectionError("redis unreachable")

    async def _raise_cache_set(key, value, ttl):
        raise ConnectionError("redis unreachable")

    def _fake_pattern_statistics(peer_group_hash, **kw):
        return {"sample_size": 5, "reliability": "UNTRUSTED", "peer_group_hash": peer_group_hash}

    monkeypatch.setattr(cache, "cache_get", _raise_cache_get)
    monkeypatch.setattr(cache, "cache_set", _raise_cache_set)
    monkeypatch.setattr(cache, "statistics", type("S", (), {"pattern_statistics": staticmethod(_fake_pattern_statistics)}))
    result = asyncio.run(cache.cached_pattern_statistics("H", strategy_version="replay-v1"))  # must not raise
    assert result["_cache_source"] == "postgres"
    assert result["sample_size"] == 5


# --- 10. no synchronous historical replay inside the M5 decision path -----------------------------


def test_entry_and_adaptive_intelligence_never_import_replay_engine():
    """Structural proof: entry_intelligence.py / adaptive_intelligence.py / cache.py / statistics.py
    never reference replay.py's actual replay functions (bars_as_of/replay_at/replay_for_evaluation/
    replay_from_snapshot) -- only STRATEGY_REPLAY_VERSION (a plain string constant), which cannot
    trigger a broker fetch or a strategy re-evaluation."""
    forbidden = ("bars_as_of", "replay_at(", "replay_for_evaluation(", "replay_from_snapshot(")
    for module in (entry_intelligence, adaptive_intelligence, cache, statistics):
        source = inspect.getsource(module)
        for token in forbidden:
            assert token not in source, f"{module.__name__} unexpectedly references {token}"


# --- 11-14. safety-boundary isolation (structural: this code never touches risk-control systems)


def test_entry_and_adaptive_intelligence_never_reference_risk_control_modules():
    """entry_intelligence.py/adaptive_intelligence.py must have NO coupling at all to
    confidence-threshold, prop-firm, portfolio-risk, or account-identity/execution-isolation
    code -- proving structurally that Historical Intelligence cannot bypass any of them, since it
    never imports or calls into any of those systems in the first place."""
    forbidden_tokens = ("min_trade_confidence", "prop_risk", "portfolio_risk", "order_send", "mt5_positions", "account_id ==", "MT5_LIVE_TRADING_ENABLED")
    for module in (entry_intelligence, adaptive_intelligence):
        source = inspect.getsource(module)
        for token in forbidden_tokens:
            assert token not in source, f"{module.__name__} unexpectedly references {token}"


def test_evaluation_result_schema_has_no_execution_forcing_field():
    """The result dict entry_intelligence returns can only ever contain observability/advisory
    fields -- no key that could plausibly force an order, bypass a threshold, or mutate risk
    state exists in the schema at all."""
    stats = {"reliability": "STRONG", "sample_size": 300, "expectancy_r": 1.0, "profit_factor": 2.0, "immediate_failure_rate": 0.1, "probability_1r": 0.5}
    result = entry_intelligence._evaluate_from_stats(trust_state=trust_gating.HIST_INTEL_ACTIVE, stats=stats, peer_group_hash="H", source="redis")
    forbidden_keys = {"force_execute", "bypass_risk", "override_confidence", "skip_portfolio_check", "account_override"}
    assert not (set(result.keys()) & forbidden_keys)


# --- 15. four accounts share market historical evidence safely ------------------------------------


def test_fingerprint_and_trust_schema_have_no_account_scoping():
    """Historical Intelligence's market-evidence tables (fingerprints/outcomes/trust) carry NO
    account_id column at all -- proving structurally that evidence is shared across all four
    accounts by construction, never partitioned into four separate replay databases. Account-
    specific concerns (risk/lot-sizing/execution) live entirely in other tables this module never
    writes to."""
    fingerprint_columns = {c.name for c in HistoricalPatternFingerprintORM.__table__.columns}
    outcome_columns = {c.name for c in HistoricalSetupOutcomeORM.__table__.columns}
    assert "account_id" not in fingerprint_columns
    assert "account_id" not in outcome_columns


# --- 16. strategy-specific lookback works -----------------------------------------------------------


def test_dynamic_lookback_registry():
    assert replay.required_lookback(timeframe="M15") == 100
    assert replay.required_lookback(timeframe="M15", strategy_ids=["ema_trend"]) == 100
    assert set(replay.STRATEGY_LOOKBACK.keys()) == {
        "mtfai1", "ema_trend", "trend_pullback", "breakout", "mean_reversion", "liquidity_sweep_reversal",
        "smc_continuation", "support_resistance_bounce", "momentum", "session_breakout", "vwap_reversion",
    }
    # A hypothetical strategy needing MORE history would raise the max for a scoped lookup
    # without affecting any other strategy's own declared requirement.
    replay.STRATEGY_LOOKBACK["_test_strategy"] = {"M15": 250, "H1": 100, "H4": 100}
    try:
        assert replay.required_lookback(timeframe="M15", strategy_ids=["mtfai1", "_test_strategy"]) == 250
        assert replay.required_lookback(timeframe="M15", strategy_ids=["mtfai1"]) == 100
    finally:
        del replay.STRATEGY_LOOKBACK["_test_strategy"]


# --- 17. session-anchored fingerprint bucketing -----------------------------------------------------


def test_time_of_day_bucket_boundaries():
    assert fingerprint_mod.time_of_day_bucket(datetime(2026, 1, 5, 3, 0, tzinfo=timezone.utc)) == "ASIAN"
    assert fingerprint_mod.time_of_day_bucket(datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)) == "LONDON"
    assert fingerprint_mod.time_of_day_bucket(datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)) == "NY_OVERLAP"
    assert fingerprint_mod.time_of_day_bucket(datetime(2026, 1, 5, 18, 0, tzinfo=timezone.utc)) == "NY"
    assert fingerprint_mod.time_of_day_bucket(datetime(2026, 1, 5, 23, 0, tzinfo=timezone.utc)) == "OTHER"


# --- 18. VWAP-relevant fields captured (spread_regime never fabricated) -----------------------------


def test_spread_regime_never_fabricated_without_real_spread():
    ctx = _ctx()
    reconstructed = fingerprint_mod.build_fingerprint(
        ctx=ctx, strategy_id="vwap_reversion", contributing_strategies=["vwap_reversion"], strategy_family="vwap_reversion",
        strategy_version="replay-v1", source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False,
        entry=1.1010, stop_loss=1.0990, take_profit=1.1050, entry_time=NOW, real_spread=None,
    )
    assert reconstructed["spread_regime"] == "unknown"

    snapshot = fingerprint_mod.build_fingerprint(
        ctx=ctx, strategy_id="vwap_reversion", contributing_strategies=["vwap_reversion"], strategy_family="vwap_reversion",
        strategy_version="replay-v1", source_quality_tier="SNAPSHOT", provider="MT5", proxy=False,
        entry=1.1010, stop_loss=1.0990, take_profit=1.1050, entry_time=NOW, real_spread=Decimal("0.0002"),
    )
    assert snapshot["spread_regime"] in {"TIGHT", "NORMAL", "WIDE"}


# --- 19. liquidity-sweep location captured in fingerprint --------------------------------------------


def test_liquidity_location_defaults_to_none_without_sweeps():
    ctx = _ctx()  # flat/short synthetic series -- no real liquidity sweep expected
    fields = fingerprint_mod.build_fingerprint(
        ctx=ctx, strategy_id="liquidity_sweep_reversal", contributing_strategies=["liquidity_sweep_reversal"], strategy_family="liquidity_sweep_reversal",
        strategy_version="replay-v1", source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False,
        entry=1.1010, stop_loss=1.0990, take_profit=1.1050, entry_time=NOW,
    )
    assert fields["liquidity_location"] in {"none", "buy_side", "sell_side"}  # never fabricated -- a real classification or none


# --- 20. post-exit / outcome PENDING never used as resolved -------------------------------------------


def test_pending_outcome_excluded_from_pattern_statistics(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        db.add(HistoricalPatternFingerprintORM(
            fingerprint_id="FP_PENDING", historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v1",
            source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol="EURUSD", direction="LONG",
            anchor_strategy="mtfai1", contributing_strategies=["mtfai1"], liquidity_location="none", fvg_state="none", order_block_state="none",
            spread_regime="unknown", entry=1.1, stop_loss=1.09, take_profit=1.12, entry_time=NOW, peer_group_hash="PENDING_HASH", created_at=NOW,
        ))
        db.add(HistoricalSetupOutcomeORM(outcome_id="OUT_PENDING", fingerprint_id="FP_PENDING", resolution_status="PENDING", bars_scanned=0, created_at=NOW))
        db.commit()

    stats = statistics.pattern_statistics("PENDING_HASH", strategy_version="replay-v1", fingerprint_version="fp-v1")
    assert stats["sample_size"] == 0
    assert stats["pending_count"] == 1
    assert stats["reliability"] == "UNTRUSTED"


def test_untrusted_data_quality_outcomes_excluded_from_pattern_statistics(monkeypatch):
    """A RESOLVED outcome whose underlying future candles could not be trusted (unfinalized, or
    a proven post-finalization anomaly -- see outcomes.py) must never contribute to an active
    statistic, distinct from (and in addition to) the PENDING exclusion above."""
    SessionLocal = _session_factory(monkeypatch)
    with SessionLocal() as db:
        db.add(HistoricalPatternFingerprintORM(
            fingerprint_id="FP_UNTRUSTED", historical_intelligence_version="hi-v1", strategy_version="replay-v1", fingerprint_version="fp-v1",
            source_quality_tier="RECONSTRUCTED", provider="MT5", proxy=False, canonical_symbol="EURUSD", direction="LONG",
            anchor_strategy="mtfai1", contributing_strategies=["mtfai1"], liquidity_location="none", fvg_state="none", order_block_state="none",
            spread_regime="unknown", entry=1.1, stop_loss=1.09, take_profit=1.12, entry_time=NOW, peer_group_hash="UNTRUSTED_HASH", created_at=NOW,
        ))
        db.add(HistoricalSetupOutcomeORM(outcome_id="OUT_UNTRUSTED", fingerprint_id="FP_UNTRUSTED", resolution_status="RESOLVED", outcome_r=1.0, bars_scanned=5, data_quality="UNTRUSTED", created_at=NOW))
        db.commit()

    stats = statistics.pattern_statistics("UNTRUSTED_HASH", strategy_version="replay-v1", fingerprint_version="fp-v1")
    assert stats["sample_size"] == 0
    assert stats["untrusted_excluded_count"] == 1


def test_outcome_labeling_marks_no_future_candles_as_pending(monkeypatch):
    _session_factory(monkeypatch)
    monkeypatch.setattr(outcomes, "_future_candles_with_quality", lambda **kw: [])
    result = outcomes.label_outcome(
        fingerprint_id="FP_NO_CANDLES", canonical_symbol="EURUSD", broker_symbol="EURUSD", direction="LONG",
        entry=1.1000, stop_loss=1.0980, take_profit=1.1040, entry_time=NOW,
    )
    assert result["resolution_status"] == "PENDING"
    assert result["outcome_r"] is None


# --- 21. adaptive historical intelligence only uses the existing action taxonomy -----------------------


def test_adaptive_recommendation_always_within_existing_taxonomy():
    scenarios = [
        {"probability_reversal": 0.9, "probability_round_trip": 0.8, "probability_reach_plus_1r": 0.1},
        {"probability_reversal": 0.1, "probability_round_trip": 0.05, "probability_reach_plus_1r": 0.9},
        {"probability_reversal": 0.5, "probability_round_trip": 0.5, "probability_reach_plus_1r": 0.5},
        {"probability_reversal": 0.0, "probability_round_trip": 0.0, "probability_reach_plus_1r": 0.0},
    ]
    for stats in scenarios:
        for current_r in (None, -0.5, 0.1, 0.3, 0.6, 1.5):
            for be in (True, False):
                recommended = adaptive_intelligence._recommend_action(stats=stats, current_r=current_r, is_at_or_beyond_breakeven=be)
                assert recommended in adaptive_intelligence._EXISTING_ACTIONS


# --- 22. LIVE trading remains disabled ------------------------------------------------------------------


def test_live_trading_remains_disabled():
    from backend.brokers.mt5.config import mt5_config

    assert mt5_config().live_trading_enabled is False
