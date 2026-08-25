"""Unit tests for backend/brokers/mt5/confidence.py -- the deterministic trade-
confidence engine that replaced the OpenAI-based entry decision in the MT5
autonomous cycle. Pure functions, no MT5/broker/DB mocking needed."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.brokers.mt5.confidence import (
    classify_confidence_band,
    compute_trade_confidence,
    is_autonomous_eligible,
    rank_candidates,
)


def _candidate(*, ranking_score=85.0, risk_reward="2.2", atr="0.0010", spread="0.0001", symbol="EURUSD", direction="LONG", timestamp=None) -> dict:
    return {
        "canonical_pair": symbol,
        "broker_symbol": symbol,
        "direction": direction,
        "ranking_score": ranking_score,
        "context": {
            "risk_reward": risk_reward,
            "atr": atr,
            "spread": spread,
            "timestamp": (timestamp or datetime.now(timezone.utc)).isoformat(),
        },
    }


def _entry_quality(total_score=0.75) -> dict:
    return {"status": "ok", "total_score": total_score, "positive_contributors": ["order_block"], "negative_contributors": [], "trend_state": "BULLISH"}


def _strong_memory(win_rate=0.65, sample=40, recommendation="PROCEED") -> dict:
    return {"closed_trade_count": sample, "win_rate": win_rate, "recommendation": recommendation, "expectancy": 12.0}


# 1. A valid candidate with confidence >=75 is eligible for autonomous execution.
def test_high_quality_candidate_is_eligible():
    confidence = compute_trade_confidence(
        candidate=_candidate(ranking_score=85.0, risk_reward="3.0"),
        entry_quality=_entry_quality(0.85),
        symbol_memory=_strong_memory(),
        global_memory=_strong_memory(),
    )
    assert confidence["overall_score"] >= 75.0
    assert is_autonomous_eligible(confidence["overall_score"])
    assert confidence["band"] in {"valid_autonomous", "strong", "very_strong", "exceptional"}


# 2. A candidate below 75 cannot execute (not autonomous eligible).
def test_weak_candidate_is_not_eligible():
    confidence = compute_trade_confidence(
        candidate=_candidate(ranking_score=40.0, risk_reward="1.0"),
        entry_quality=_entry_quality(0.2),
        symbol_memory={"closed_trade_count": 20, "win_rate": 0.2, "recommendation": "AVOID", "expectancy": -8.0},
        global_memory=_strong_memory(),
        correlation_penalty_points=40.0,
    )
    assert confidence["overall_score"] < 75.0
    assert not is_autonomous_eligible(confidence["overall_score"])


# 2026-08-25 Confidence Architecture & Calibration Audit (Part 1/2): trend_multi_timeframe must
# prefer a strategy-supplied context["trend_quality_score"] over the generic ranking_score
# fallback -- fixes mtfai1's raw score (88-spread_penalty, a spread proxy) being mislabeled as
# "trend alignment" and double-counted against volatility_suitability.
def test_trend_multi_timeframe_prefers_strategy_supplied_score():
    candidate = _candidate(ranking_score=60.0)  # a low ranking_score that would otherwise drag the component down
    candidate["context"]["trend_quality_score"] = 92.5

    confidence = compute_trade_confidence(candidate=candidate, entry_quality=_entry_quality(), symbol_memory=None, global_memory=None)

    trend_component = next(c for c in confidence["components"] if c["name"] == "trend_multi_timeframe")
    assert trend_component["score"] == pytest.approx(92.5)
    assert trend_component["inputs"]["source"] == "strategy_specific"


def test_trend_multi_timeframe_falls_back_to_ranking_score_when_unsupplied():
    candidate = _candidate(ranking_score=71.0)  # no trend_quality_score in context

    confidence = compute_trade_confidence(candidate=candidate, entry_quality=_entry_quality(), symbol_memory=None, global_memory=None)

    trend_component = next(c for c in confidence["components"] if c["name"] == "trend_multi_timeframe")
    assert trend_component["score"] == pytest.approx(71.0)
    assert trend_component["inputs"]["source"] == "generic_fallback"


def test_trend_multi_timeframe_ignores_unparseable_strategy_score():
    candidate = _candidate(ranking_score=66.0)
    candidate["context"]["trend_quality_score"] = "not-a-number"

    confidence = compute_trade_confidence(candidate=candidate, entry_quality=_entry_quality(), symbol_memory=None, global_memory=None)

    trend_component = next(c for c in confidence["components"] if c["name"] == "trend_multi_timeframe")
    assert trend_component["score"] == pytest.approx(66.0)
    assert trend_component["inputs"]["source"] == "generic_fallback"


# 3. Candidates scoring 70-74 are logged (observe_only band) but not eligible to execute.
def test_observe_only_band_is_not_eligible():
    assert classify_confidence_band(72.0) == "observe_only"
    assert classify_confidence_band(70.0) == "observe_only"
    assert classify_confidence_band(74.9) == "observe_only"
    assert not is_autonomous_eligible(72.0)
    assert not is_autonomous_eligible(74.9)


def test_confidence_bands_match_spec():
    assert classify_confidence_band(95) == "exceptional"
    assert classify_confidence_band(87) == "very_strong"
    assert classify_confidence_band(82) == "strong"
    assert classify_confidence_band(76) == "valid_autonomous"
    assert classify_confidence_band(71) == "observe_only"
    assert classify_confidence_band(50) == "reject"


# 4/5. Multiple valid candidates are ranked deterministically, and the strongest
# risk-adjusted candidate (not necessarily the highest raw trend score) is selected.
def test_multiple_candidates_ranked_deterministically_and_best_selected():
    gbpusd = dict(_candidate(symbol="GBPUSD", ranking_score=80.0, risk_reward="3.0"), trade_confidence=compute_trade_confidence(
        candidate=_candidate(symbol="GBPUSD", ranking_score=80.0, risk_reward="3.0"), entry_quality=_entry_quality(0.9), symbol_memory=_strong_memory(), global_memory=_strong_memory(),
    ))
    eurusd = dict(_candidate(symbol="EURUSD", ranking_score=82.0, risk_reward="1.6"), trade_confidence=compute_trade_confidence(
        candidate=_candidate(symbol="EURUSD", ranking_score=82.0, risk_reward="1.6"), entry_quality=_entry_quality(0.5), symbol_memory=None, global_memory=None,
    ))
    usdjpy = dict(_candidate(symbol="USDJPY", ranking_score=77.0, risk_reward="1.5"), trade_confidence=compute_trade_confidence(
        candidate=_candidate(symbol="USDJPY", ranking_score=77.0, risk_reward="1.5"), entry_quality=_entry_quality(0.4), symbol_memory=None, global_memory=None,
    ))

    ranked_once = rank_candidates([dict(usdjpy), dict(eurusd), dict(gbpusd)])
    ranked_twice = rank_candidates([dict(gbpusd), dict(usdjpy), dict(eurusd)])

    # Deterministic: same inputs (any input order) produce the same ranking.
    assert [row["canonical_pair"] for row in ranked_once] == [row["canonical_pair"] for row in ranked_twice]
    # GBPUSD has the strongest structure/reward:risk/memory profile despite a lower raw
    # trend score than EURUSD -- it should rank first (best risk-adjusted, not highest raw score).
    assert ranked_once[0]["canonical_pair"] == "GBPUSD"
    assert [row["rank"] for row in ranked_once] == [1, 2, 3]


# 6. A 90+ candidate can still be rejected by portfolio limits (tested at the confidence
# layer: the score itself must not be affected by portfolio state -- portfolio permission
# is evaluated entirely separately, see test_mt5_autonomous_deterministic.py for the
# cycle-level SIGNAL_VALID_BUT_PORTFOLIO_REJECTED behavior).
def test_confidence_score_is_independent_of_portfolio_state():
    high_confidence = compute_trade_confidence(
        candidate=_candidate(ranking_score=88.0, risk_reward="3.5"),
        entry_quality=_entry_quality(0.95),
        symbol_memory=_strong_memory(win_rate=0.75),
        global_memory=_strong_memory(win_rate=0.7),
    )
    assert high_confidence["overall_score"] >= 85.0
    # Nothing in compute_trade_confidence's signature accepts "portfolio can open trade" --
    # by construction the score cannot be lowered by a portfolio rejection.


# 7. No valid candidate (all below 70) reaching NO_TRADE.
def test_all_candidates_reject_band_yields_no_eligible_candidate():
    confidence = compute_trade_confidence(
        candidate=_candidate(ranking_score=10.0, risk_reward="0.5"),
        entry_quality=_entry_quality(0.05),
        symbol_memory={"closed_trade_count": 30, "win_rate": 0.1, "recommendation": "AVOID"},
        global_memory={"closed_trade_count": 30, "win_rate": 0.15, "recommendation": "AVOID"},
        correlation_penalty_points=60.0,
    )
    assert confidence["band"] == "reject"
    assert not is_autonomous_eligible(confidence["overall_score"])


# 11. Confidence scoring is deterministic and reproducible.
def test_confidence_is_deterministic():
    kwargs = dict(
        candidate=_candidate(ranking_score=81.5, risk_reward="2.1", timestamp=datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)),
        entry_quality=_entry_quality(0.6),
        symbol_memory=_strong_memory(win_rate=0.55, sample=15),
        global_memory=_strong_memory(win_rate=0.5, sample=100),
        correlation_penalty_points=10.0,
        correlated_symbols=["USD"],
        now=datetime(2026, 8, 10, 12, 2, tzinfo=timezone.utc),
    )
    first = compute_trade_confidence(**kwargs)
    second = compute_trade_confidence(**kwargs)
    assert first == second


# 12. Confidence components are persisted (present and well-shaped) so the system can
# explain WHY without any AI model.
def test_confidence_components_are_fully_explainable():
    confidence = compute_trade_confidence(
        candidate=_candidate(ranking_score=81.0, risk_reward="2.0"),
        entry_quality=_entry_quality(0.7),
        symbol_memory=_strong_memory(),
        global_memory=_strong_memory(),
    )
    names = {component["name"] for component in confidence["components"]}
    assert names == {
        "trend_multi_timeframe", "structure_confluence", "reward_risk_quality", "volatility_suitability",
        "execution_conditions", "signal_freshness", "strategy_performance", "symbol_performance", "correlation_quality",
    }
    for component in confidence["components"]:
        assert 0.0 <= component["score"] <= 100.0
        assert component["reason"]  # every component has a stated, non-empty reason
    total_weight = sum(component["weight"] for component in confidence["components"])
    assert total_weight == pytest.approx(1.0, abs=1e-6)
    assert confidence["rule_version"] == "deterministic_confidence_v1"


# 2026-08-25 MTFAI1 V2 confidence-component forensic analysis (Part 2): a single closed trade
# (or any sample below the minimum) must not be able to trigger a hard REDUCE_RISK/AVOID cap --
# the raw win_rate blend already tapers thin samples toward neutral(60), but the recommendation
# CAP previously had no such gate. Generic fix (confidence.py::_performance_component), so this
# is exercised strategy-agnostically through compute_trade_confidence, not an MTFAI1-only path.
def test_thin_sample_recommendation_cap_is_gated_by_minimum_sample_size():
    from backend.brokers.mt5.confidence import _MIN_SAMPLE_FOR_RECOMMENDATION_CAP

    # A single closed trade with a bad outcome, classified AVOID by whatever produced this
    # memory dict -- with the fix, sample=1 is below the minimum, so the AVOID cap (30.0) must
    # NOT apply; only the existing sample-size blend (already present, unaffected by this fix)
    # governs the score.
    thin_bad_memory = {"closed_trade_count": 1, "win_rate": 0.0, "recommendation": "AVOID", "expectancy": -0.05}
    confidence = compute_trade_confidence(
        candidate=_candidate(ranking_score=81.0, risk_reward="2.0"),
        entry_quality=_entry_quality(0.7),
        symbol_memory=thin_bad_memory,
        global_memory=_strong_memory(),
    )
    symbol_component = next(c for c in confidence["components"] if c["name"] == "symbol_performance")
    assert symbol_component["score"] > 30.0  # the AVOID cap (30.0) must not have applied at sample=1

    # The SAME memory shape, but with enough samples to clear the (default) minimum -- the cap
    # SHOULD apply here, proving this isn't just "the cap never works".
    well_powered_bad_memory = {"closed_trade_count": _MIN_SAMPLE_FOR_RECOMMENDATION_CAP, "win_rate": 0.0, "recommendation": "AVOID", "expectancy": -0.5}
    confidence2 = compute_trade_confidence(
        candidate=_candidate(ranking_score=81.0, risk_reward="2.0"),
        entry_quality=_entry_quality(0.7),
        symbol_memory=well_powered_bad_memory,
        global_memory=_strong_memory(),
    )
    symbol_component2 = next(c for c in confidence2["components"] if c["name"] == "symbol_performance")
    assert symbol_component2["score"] <= 30.0  # cap correctly applies once the sample is large enough
