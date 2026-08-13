"""DEMO-only MTFAI1 rolling execution diversity cap.

MTFAI1 may account for at most MT5_DEMO_MTF_AI1_MAX_TRADES (default 3) of the last
MT5_DEMO_MTF_AI1_WINDOW (default 10) ACCEPTED executions from this engine. When MTFAI1 is
naturally ranked #1 but already at the cap, it is preserved as rank #1 in analytics (never
re-ranked, confidence never altered), tagged TEST_DIVERSITY_DEFERRED, and the next-ranked
ACTIVE_MT5 non-MTFAI1 candidate (still >=75 confidence, since eligible_for_execution is already
filtered) is evaluated instead. Never forces a trade if no alternative qualifies. DEMO only --
live mode ignores the rule entirely.

Tests _mtfai1_rolling_count and _apply_mtfai1_diversity_cap directly -- both are pure,
synchronous, side-effect-free (aside from tagging rejection_reasons on the candidate dicts
passed in, exactly as run_cycle relies on) -- rather than driving a full run_cycle(), which
would require mocking the entire screening/economic/portfolio pipeline for behavior this
narrow.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.brokers.mt5 import autonomous
from backend.brokers.mt5.autonomous import MT5AutonomousTradingService


def _mk_service(account_mode: str = "DEMO", trades: list[dict] | None = None) -> MT5AutonomousTradingService:
    adapter = SimpleNamespace(config=SimpleNamespace(account_mode=account_mode))
    service = MT5AutonomousTradingService(adapter=adapter)
    service.state.trades = trades or []
    return service


def _mk_executed_trade(strategy_id: str, *, status: str = "ACCEPTED") -> dict:
    return {"strategy_id": strategy_id, "submission": {"status": status}}


def _mk_candidate(strategy_id: str, *, broker_symbol: str, confidence: float, rank: int) -> dict:
    return {
        "broker_symbol": broker_symbol,
        "canonical_pair": broker_symbol,
        "context_hash": f"hash-{broker_symbol}-{strategy_id}-{rank}",
        "context": {"strategy_id": strategy_id},
        "trade_confidence": {"overall_score": confidence},
        "rejection_reasons": [],
        "ranking_score": 100 - rank,
    }


# ---------------------------------------------------------------------------
# _mtfai1_rolling_count
# ---------------------------------------------------------------------------


def test_rolling_count_counts_only_accepted_mtfai1_within_window():
    trades = [_mk_executed_trade("mtfai1")] * 3 + [_mk_executed_trade("breakout")] * 7
    service = _mk_service(trades=trades)

    assert service._mtfai1_rolling_count(10) == 3


def test_rolling_count_ignores_rejected_and_errored_submissions():
    trades = [
        _mk_executed_trade("mtfai1", status="ACCEPTED"),
        _mk_executed_trade("mtfai1", status="REJECTED"),  # never reached the broker -- not "executed"
        _mk_executed_trade("mtfai1", status="ACCEPTED"),
        _mk_executed_trade("breakout", status="ACCEPTED"),
    ]
    service = _mk_service(trades=trades)

    # Only the 2 ACCEPTED mtfai1 + 1 ACCEPTED breakout count as "executed" -- window of 10
    # covers all 3 accepted entries, giving a count of 2, not 3.
    assert service._mtfai1_rolling_count(10) == 2


def test_rolling_count_respects_window_boundary():
    # Newest first (matching _submit's insert(0, ...)): 1 breakout, then 10 mtfai1 -- 11 total.
    trades = [_mk_executed_trade("breakout")] + [_mk_executed_trade("mtfai1")] * 10
    service = _mk_service(trades=trades)

    assert service._mtfai1_rolling_count(10) == 9  # window of 10 = breakout + 9 of the 10 mtfai1
    assert service._mtfai1_rolling_count(3) == 2  # window of 3 = breakout + 2 mtfai1


def test_rolling_count_older_trades_roll_out_automatically():
    """Once older MTFAI1 trades age past the window (newer non-MTFAI1 trades pushed them out),
    the rolling count drops back below the cap with no manual intervention."""
    old_mtfai1_heavy = [_mk_executed_trade("mtfai1")] * 3 + [_mk_executed_trade("breakout")] * 7
    service = _mk_service(trades=old_mtfai1_heavy)
    assert service._mtfai1_rolling_count(10) == 3

    # 8 newer non-MTFAI1 trades arrive (inserted at the front, matching _submit's
    # insert(0, ...)) -- enough to push one of the 3 old MTFAI1 trades past position 10.
    newer = [_mk_executed_trade("smc_continuation")] * 8
    service.state.trades = newer + old_mtfai1_heavy
    assert service._mtfai1_rolling_count(10) == 2  # one MTFAI1 trade aged out of the window


# ---------------------------------------------------------------------------
# _apply_mtfai1_diversity_cap
# ---------------------------------------------------------------------------


def test_defers_mtfai1_when_cap_reached_and_picks_next_alternative():
    service = _mk_service(trades=[_mk_executed_trade("mtfai1")] * 3 + [_mk_executed_trade("breakout")] * 7)
    mtfai1_row = _mk_candidate("mtfai1", broker_symbol="EURUSD", confidence=90.0, rank=1)
    alt_row = _mk_candidate("breakout", broker_symbol="GBPUSD", confidence=80.0, rank=2)
    eligible = [mtfai1_row, alt_row]

    best, diversity_cap = service._apply_mtfai1_diversity_cap("C1", eligible)

    assert best is alt_row
    assert diversity_cap["enabled"] is True
    assert diversity_cap["deferred"] is True
    assert diversity_cap["defer_reason"] == "TEST_DIVERSITY_DEFERRED"
    assert diversity_cap["rolling_mtfai1_count"] == 3
    assert diversity_cap["natural_rank1_strategy_id"] == "mtfai1"
    assert diversity_cap["natural_rank1_confidence"] == 90.0
    assert diversity_cap["executed_strategy_id"] == "breakout"
    assert "TEST_DIVERSITY_DEFERRED" in mtfai1_row["rejection_reasons"]
    assert "LOWER_RANKED_CANDIDATE" not in mtfai1_row["rejection_reasons"]  # deferred, not just outranked
    assert alt_row["rejection_reasons"] == []  # the executing candidate is never tagged


def test_does_not_defer_when_under_cap():
    service = _mk_service(trades=[_mk_executed_trade("mtfai1")] * 2 + [_mk_executed_trade("breakout")] * 7)
    mtfai1_row = _mk_candidate("mtfai1", broker_symbol="EURUSD", confidence=90.0, rank=1)
    alt_row = _mk_candidate("breakout", broker_symbol="GBPUSD", confidence=80.0, rank=2)

    best, diversity_cap = service._apply_mtfai1_diversity_cap("C1", [mtfai1_row, alt_row])

    assert best is mtfai1_row  # only 2 of the last 10 -- under the cap of 3, MTFAI1 stays #1
    assert diversity_cap["deferred"] is False
    assert diversity_cap["rolling_mtfai1_count"] == 2
    assert diversity_cap["executed_strategy_id"] == "mtfai1"
    assert mtfai1_row["rejection_reasons"] == []
    assert "LOWER_RANKED_CANDIDATE" in alt_row["rejection_reasons"]


def test_skips_multiple_mtfai1_candidates_to_find_a_real_alternative():
    """Rank #1 AND #2 both MTFAI1 (different symbols) -- picking #2 would not solve the
    concentration problem, so it must be skipped too."""
    service = _mk_service(trades=[_mk_executed_trade("mtfai1")] * 3 + [_mk_executed_trade("breakout")] * 7)
    rank1 = _mk_candidate("mtfai1", broker_symbol="EURUSD", confidence=92.0, rank=1)
    rank2 = _mk_candidate("mtfai1", broker_symbol="USDJPY", confidence=88.0, rank=2)
    rank3 = _mk_candidate("smc_continuation", broker_symbol="GBPUSD", confidence=80.0, rank=3)

    best, diversity_cap = service._apply_mtfai1_diversity_cap("C1", [rank1, rank2, rank3])

    assert best is rank3
    assert diversity_cap["executed_strategy_id"] == "smc_continuation"
    assert "TEST_DIVERSITY_DEFERRED" in rank1["rejection_reasons"]
    assert "LOWER_RANKED_CANDIDATE" in rank2["rejection_reasons"]  # skipped for being MTFAI1 too
    assert rank3["rejection_reasons"] == []


def test_never_forces_a_trade_when_no_alternative_qualifies():
    """Every eligible candidate this cycle is MTFAI1 -- must return best=None, never fall back
    to submitting MTFAI1 anyway."""
    service = _mk_service(trades=[_mk_executed_trade("mtfai1")] * 3 + [_mk_executed_trade("breakout")] * 7)
    rank1 = _mk_candidate("mtfai1", broker_symbol="EURUSD", confidence=92.0, rank=1)
    rank2 = _mk_candidate("mtfai1", broker_symbol="USDJPY", confidence=88.0, rank=2)

    best, diversity_cap = service._apply_mtfai1_diversity_cap("C1", [rank1, rank2])

    assert best is None
    assert diversity_cap["deferred"] is True
    assert diversity_cap["executed_strategy_id"] is None
    assert "TEST_DIVERSITY_DEFERRED" in rank1["rejection_reasons"]
    assert "LOWER_RANKED_CANDIDATE" in rank2["rejection_reasons"]


def test_ignored_in_live_mode():
    service = _mk_service(account_mode="LIVE", trades=[_mk_executed_trade("mtfai1")] * 5)
    mtfai1_row = _mk_candidate("mtfai1", broker_symbol="EURUSD", confidence=92.0, rank=1)
    alt_row = _mk_candidate("breakout", broker_symbol="GBPUSD", confidence=80.0, rank=2)

    best, diversity_cap = service._apply_mtfai1_diversity_cap("C1", [mtfai1_row, alt_row])

    assert best is mtfai1_row  # rule never engages outside DEMO, regardless of rolling count
    assert diversity_cap["enabled"] is False
    assert diversity_cap["deferred"] is False
    assert diversity_cap["rolling_mtfai1_count"] is None
    assert mtfai1_row["rejection_reasons"] == []


def test_natural_rank1_confidence_and_ranking_never_altered():
    """The candidate dict's own confidence/ranking_score fields must be byte-identical before
    and after -- the cap only ever adds a rejection_reasons tag, never rewrites scoring."""
    service = _mk_service(trades=[_mk_executed_trade("mtfai1")] * 3 + [_mk_executed_trade("breakout")] * 7)
    mtfai1_row = _mk_candidate("mtfai1", broker_symbol="EURUSD", confidence=92.5, rank=1)
    alt_row = _mk_candidate("breakout", broker_symbol="GBPUSD", confidence=80.0, rank=2)
    original_confidence = dict(mtfai1_row["trade_confidence"])
    original_ranking_score = mtfai1_row["ranking_score"]

    service._apply_mtfai1_diversity_cap("C1", [mtfai1_row, alt_row])

    assert mtfai1_row["trade_confidence"] == original_confidence
    assert mtfai1_row["ranking_score"] == original_ranking_score


def test_preserves_natural_rank1_when_it_is_not_mtfai1():
    """Control case: natural #1 isn't MTFAI1 at all -- the cap engages (DEMO) but never
    defers anything, and the natural #1 executes normally."""
    service = _mk_service(trades=[_mk_executed_trade("mtfai1")] * 3)
    non_mtfai1_row = _mk_candidate("breakout", broker_symbol="EURUSD", confidence=90.0, rank=1)
    other_row = _mk_candidate("smc_continuation", broker_symbol="GBPUSD", confidence=80.0, rank=2)

    best, diversity_cap = service._apply_mtfai1_diversity_cap("C1", [non_mtfai1_row, other_row])

    assert best is non_mtfai1_row
    assert diversity_cap["enabled"] is True
    assert diversity_cap["deferred"] is False
    assert diversity_cap["natural_rank1_strategy_id"] == "breakout"
    assert diversity_cap["executed_strategy_id"] == "breakout"


def test_diversity_cap_metadata_has_all_persisted_fields():
    service = _mk_service(trades=[_mk_executed_trade("mtfai1")] * 3 + [_mk_executed_trade("breakout")] * 7)
    mtfai1_row = _mk_candidate("mtfai1", broker_symbol="EURUSD", confidence=92.0, rank=1)
    alt_row = _mk_candidate("breakout", broker_symbol="GBPUSD", confidence=80.0, rank=2)

    _, diversity_cap = service._apply_mtfai1_diversity_cap("CYCLE_TEST", [mtfai1_row, alt_row])

    for field in ("natural_rank1_candidate_id", "natural_rank1_strategy_id", "natural_rank1_confidence", "rolling_mtfai1_count", "defer_reason", "executed_strategy_id", "window", "max_trades"):
        assert field in diversity_cap
    assert diversity_cap["natural_rank1_candidate_id"] == f"CYCLE_TEST:EURUSD:{mtfai1_row['context_hash'][:16]}"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def test_config_defaults():
    assert autonomous.MT5_DEMO_MTF_AI1_MAX_TRADES == 3
    assert autonomous.MT5_DEMO_MTF_AI1_WINDOW == 10


def test_max_trades_of_zero_defers_mtfai1_even_with_no_history(monkeypatch):
    monkeypatch.setattr(autonomous, "MT5_DEMO_MTF_AI1_MAX_TRADES", 0)
    service = _mk_service(trades=[])
    mtfai1_row = _mk_candidate("mtfai1", broker_symbol="EURUSD", confidence=92.0, rank=1)
    alt_row = _mk_candidate("breakout", broker_symbol="GBPUSD", confidence=80.0, rank=2)

    best, diversity_cap = service._apply_mtfai1_diversity_cap("C1", [mtfai1_row, alt_row])

    assert best is alt_row
    assert diversity_cap["deferred"] is True
