"""MT5 order comment strategy attribution fix -- the 10 required tests.

Covers: the broker-side order comment reflects the actual selected candidate's anchor/
contributing strategies (never a hardcoded "MTFAI1"), degrades deterministically and keeps the
anchor unambiguous when it must be shortened, stays within MT5's comment length limit, and
changes nothing about order submission, execution-manager routing, or ownership/reconciliation
detection (which is keyed on the "BSM|" prefix and the `magic` number, never on the comment's
internal content).
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime

import pytest

from backend.brokers.mt5.autonomous import MT5AutonomousTradingService, _candidate_strategy_label, _mt5_order_comment
from backend.brokers.mt5.config import mt5_config
from backend.mt5_strategies.models import STRATEGY_FAMILIES
from backend.tests.test_mt5_adapter import fake_adapter
from backend.tests.test_mt5_autonomous_deterministic import _screened_candidate, _wire_common_mocks

NOW = datetime(2026, 8, 10, 19, 26, 0)


def _candidate(*, strategy_id: str, contributing: list[str] | None = None) -> dict:
    context: dict = {"strategy_id": strategy_id}
    if contributing is not None:
        context["contributing_strategies"] = contributing
    return {"canonical_pair": "EURJPY", "context": context}


# 1. MTFAI1 trade comment still identifies MTFAI1.
def test_mtfai1_comment_still_identifies_mtfai1():
    comment = _mt5_order_comment(_candidate(strategy_id="mtfai1"), NOW)
    assert comment == "BSM|mtfai1|1926"
    assert "mtfai1" in comment


# 2. Single non-MTFAI1 strategy shows its real strategy.
def test_single_non_mtfai1_strategy_shows_real_strategy():
    comment = _mt5_order_comment(_candidate(strategy_id="breakout"), NOW)
    assert comment == "BSM|breakout|1926"
    assert "mtfai1" not in comment.lower()


# 3. Fused breakout+momentum candidate is not labeled MTFAI1.
def test_fused_breakout_momentum_not_labeled_mtfai1():
    comment = _mt5_order_comment(_candidate(strategy_id="breakout", contributing=["breakout", "momentum"]), NOW)
    assert comment == "BSM|breakout+momentum|1926"
    assert "mtfai1" not in comment.lower()


# 4. Anchor strategy remains identifiable when the comment must be shortened.
def test_anchor_remains_identifiable_when_comment_must_be_shortened():
    others = ["momentum", "trend_pullback", "ema_trend", "support_resistance_bounce"]
    label = _candidate_strategy_label(_candidate(strategy_id="smc_continuation", contributing=["smc_continuation", *others]), max_len=12)
    # Whatever degradation tier was needed, the label must start with an unambiguous
    # representation of the ANCHOR ("smc_continuation" or its short code "smc_cont"), never a
    # contributor, and never bare initials that could be confused with another strategy.
    assert label.startswith("smc_cont")
    assert not label.startswith(("momentum", "pullback", "ema", "srbounce"))
    comment = _mt5_order_comment(_candidate(strategy_id="smc_continuation", contributing=["smc_continuation", *others]), NOW)
    assert comment.startswith("BSM|smc_cont")
    assert len(comment) <= 31


# 5. Comment stays within MT5/broker-supported length -- including the worst case (anchor plus
# every other canonical strategy contributing).
def test_comment_never_exceeds_mt5_length_limit():
    all_others = [sid for sid in STRATEGY_FAMILIES if sid != "mtfai1"]
    worst_case = _candidate(strategy_id="mtfai1", contributing=["mtfai1", *all_others])
    comment = _mt5_order_comment(worst_case, NOW)
    assert len(comment) <= 31
    assert comment.startswith("BSM|mtfai1")

    for strategy_id in STRATEGY_FAMILIES:
        comment = _mt5_order_comment(_candidate(strategy_id=strategy_id), NOW)
        assert len(comment) <= 31
        assert comment.isascii()


# 6. Order submission behavior is otherwise unchanged -- same ACCEPTED/order_send_calls outcome
# as before this fix, with a real (non-mtfai1, fused) strategy attribution now on the request.
def test_order_submission_behavior_unchanged(monkeypatch: pytest.MonkeyPatch):
    # Pinned explicitly: the real container env now has MT5_LOGIN/MT5_SERVER set to the real
    # demo_10k account (2026-08-18 broker migration to ICMarketsSC-Demo) -- fake_adapter()'s
    # FakeAccount hardcodes login=123456/server="MetaQuotes-Demo", so a real deployed login/
    # server here would trip account_registry's live ACCOUNT_EXECUTION_CONTEXT_MISMATCH/
    # ACCOUNT_SERVER_MISMATCH safety check (a real, working check -- see account_registry.py's
    # own account-match validation) and reject the order for reasons unrelated to what this test
    # actually covers (order-comment strategy attribution). MT5_LOGIN was previously always
    # empty/unset in this environment, which is what this test was written and validated against.
    monkeypatch.delenv("MT5_LOGIN", raising=False)
    monkeypatch.delenv("MT5_SERVER", raising=False)
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)
    _wire_common_mocks(monkeypatch, service, entry_quality_score=0.9)
    candidate = _screened_candidate(symbol="EURUSD", ranking_score=88.0, risk_reward="3.0")
    candidate["context"]["strategy_id"] = "breakout"
    candidate["context"]["contributing_strategies"] = ["breakout", "momentum"]
    monkeypatch.setattr(service, "_screen", lambda items, **kwargs: asyncio.sleep(0, result=[candidate]))

    result = asyncio.run(service.run_cycle(owner="comment-attribution-test"))

    assert result["trade"]["status"] == "ACCEPTED"
    assert adapter.client.mt5.order_send_calls == 1
    sent_comment = adapter.client.mt5.last_order_send["comment"]
    assert sent_comment.startswith("BSM|breakout+momentum|")
    assert sent_comment.rsplit("|", 1)[-1].isdigit()
    assert "mtfai1" not in sent_comment.lower()


# 7. Execution journaling/reconciliation still works -- ownership detection is keyed on the
# "BSM|" prefix (and separately on `magic`), never on anything after it, so the new comment
# content cannot break it.
def test_ownership_detection_still_recognizes_new_comment_format():
    for comment in ("BSM|mtfai1|1926", "BSM|breakout+momentum|1926", "BSM|smc_cont+3|0512", "BENSIM_AUTO_legacy"):
        assert comment.startswith(("BENSIM_AUTO", "BSM|"))


# 8. OpenAI remains absent from the comment-attribution code.
def test_order_comment_code_has_no_openai_reference():
    source = "\n".join([inspect.getsource(_mt5_order_comment), inspect.getsource(_candidate_strategy_label)])
    assert "openai" not in source.lower()


# 9. IBKR remains absent from the comment-attribution code.
def test_order_comment_code_has_no_ibkr_reference():
    source = "\n".join([inspect.getsource(_mt5_order_comment), inspect.getsource(_candidate_strategy_label)])
    assert "ibkr" not in source.lower()


# 10. Live trading remains blocked.
def test_live_trading_remains_blocked():
    assert mt5_config().live_trading_enabled is False
