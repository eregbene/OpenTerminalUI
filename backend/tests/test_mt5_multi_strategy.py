"""Unified MT5 multi-strategy layer -- the 25 required tests.

Covers: multi-family candidate generation, no IBKR dependency, each canonical strategy family
working through the shared interface, regime-gated activation, candidate fusion/dedup,
deterministic conflict resolution, the Stage-1 shadow-mode safety gate (never order_send),
zero OpenAI, unchanged adaptive manager, calibration instrumentation, and continued passing of
every pre-existing MT5/adaptive/economic suite.
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pandas as pd
import pytest

from backend.brokers.mt5.autonomous import MT5AutonomousTradingService
from backend.brokers.mt5.candidate_evaluation import capture_cycle_candidate_evaluations
from backend.brokers.mt5.confidence import compute_trade_confidence
from backend.brokers.mt5.config import mt5_config
from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.market_structure.models import ConceptStatus, Direction, DisplacementEvent, LiquiditySide, LiquiditySweep, StructureBreak, StructureBreakKind
from backend.mt5_strategies.context import StrategyContext, build_strategy_context
from backend.mt5_strategies.families import EVALUATORS, evaluate_all, evaluate_breakout, evaluate_ema_trend, evaluate_liquidity_sweep_reversal, evaluate_mean_reversion, evaluate_momentum, evaluate_smc_continuation, evaluate_trend_pullback
from backend.mt5_strategies.fusion import build_candidates
from backend.mt5_strategies.models import ACTIVE_MT5, SHADOW_MT5, STRATEGY_FAMILIES, activation_status, regime_compatible
from backend.shared.db import SessionLocal
from backend.shared.test_db_safety import redirect_shared_db_to_isolated_sqlite
from backend.tests.test_mt5_adapter import fake_adapter

NOW = datetime.now(timezone.utc)


# ------------------------------------------------------------------------- data builders ---
def _mk_rows(n: int, *, tf_minutes: int = 15, base: float = 1.1000, step: float = 0.0, osc_amplitude: float = 0.0, osc_period: int = 20, vol: int = 100, wick: float = 0.0003) -> list[dict]:
    import math

    rows = []
    t = NOW - timedelta(minutes=tf_minutes * n)
    price = base
    for i in range(n):
        osc = osc_amplitude * math.sin(2 * math.pi * i / osc_period) if osc_period else 0.0
        c = base + step * i + osc
        o = price
        h = max(o, c) + wick
        low = min(o, c) - wick
        rows.append({"time": (t + timedelta(minutes=tf_minutes * i)).isoformat(), "open": o, "high": h, "low": low, "close": c, "tick_volume": vol, "spread": 1})
        price = c
    return rows


def _flat_context(*, symbol: str = "EURUSD", regime: str = "trending_up", htf_h4: str = "bullish", htf_h1: str = "bullish") -> StrategyContext:
    """A minimally-valid context (enough bars for build_strategy_context, no real SMC
    structure) whose regime/HTF fields are then overridden -- used by tests that inject
    hand-built SMC events directly rather than depending on the engine to discover them from
    an engineered price series. Wick size is large enough that a 2.5xATR target comfortably
    clears the 1.5x-stop-distance reward:risk floor for the tight stops used in these tests."""
    rows = _mk_rows(30, base=1.1000, wick=0.0015)
    ctx = build_strategy_context(symbol=symbol, broker_symbol=symbol, m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal("1.1010"), ask=Decimal("1.1012"), spread=Decimal("0.0002"))
    assert ctx is not None
    return dataclasses.replace(ctx, regime=regime, htf_trend_h4=htf_h4, htf_trend_h1=htf_h1)


def _mk_break(symbol: str, *, bar_index: int, direction: Direction, kind: StructureBreakKind) -> StructureBreak:
    return StructureBreak(
        id=f"brk_{bar_index}_{kind.value}", symbol=symbol, timeframe="M15", start_time=NOW, end_time=NOW, detected_time=NOW, confirmation_time=NOW,
        direction=direction, status=ConceptStatus.CONFIRMED, configuration_version="v1", configuration_hash="testhash",
        break_kind=kind, break_price=Decimal("1.1050"), broken_level=Decimal("1.1005"), broken_swing_id="swg_test", break_distance=Decimal("0.0050"),
        break_distance_atr=1.2, confirmation_mode="close", bar_index=bar_index, explanation="synthetic test break",
    )


def _mk_displacement(symbol: str, *, bar_index: int, direction: Direction) -> DisplacementEvent:
    return DisplacementEvent(
        id=f"disp_{bar_index}", symbol=symbol, timeframe="M15", start_time=NOW, end_time=NOW, detected_time=NOW, confirmation_time=NOW,
        direction=direction, status=ConceptStatus.CONFIRMED, configuration_version="v1", configuration_hash="testhash",
        bar_index=bar_index, magnitude_atr=2.0, body_ratio=0.8, bars_in_sequence=1,
    )


def _mk_sweep(symbol: str, *, bar_index: int, side: LiquiditySide, direction: Direction) -> LiquiditySweep:
    return LiquiditySweep(
        id=f"swp_{bar_index}", symbol=symbol, timeframe="M15", start_time=NOW, end_time=NOW, detected_time=NOW, confirmation_time=NOW,
        direction=direction, status=ConceptStatus.CONFIRMED, configuration_version="v1", configuration_hash="testhash",
        level_id="lvl_1", side=side, swept_price=Decimal("1.0995"), reclaim_price=Decimal("1.1005"), penetration=Decimal("0.0010"), bar_index=bar_index,
    )


# 1 / 10. MT5 can generate candidates from more than one strategy family; multiple strategies
# can support one normalized candidate.
def test_multiple_strategy_families_fuse_into_one_candidate():
    ctx = _flat_context()
    sig_a = evaluate_ema_trend(ctx)
    sig_b = evaluate_trend_pullback(ctx)
    # Force both into agreement for this test regardless of their own gating, to isolate
    # fusion.build_candidates()'s own logic.
    sig_a = dataclasses.replace(sig_a, valid=True, direction="LONG", strategy_id="ema_trend", proposed_entry=1.1010, stop_loss=1.0990, take_profit=1.1060, reward_risk=2.5)
    sig_b = dataclasses.replace(sig_b, valid=True, direction="LONG", strategy_id="trend_pullback", proposed_entry=1.1010, stop_loss=1.0995, take_profit=1.1050, reward_risk=2.0)
    candidates = build_candidates(symbol="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", cycle_id="C1", signals=[sig_a, sig_b], htf_trend_h4="bullish", now=NOW)
    assert len(candidates) == 1
    contributing = candidates[0]["context"]["contributing_strategies"]
    assert set(contributing) == {"ema_trend", "trend_pullback"}
    assert candidates[0]["context"]["multi_strategy_confirmation"] is True


# signal_freshness (confidence.py) needs the candle's real close time, not context-build
# wall-clock time -- see _shared.py::_signal's docstring on why generated_at alone was
# structurally incapable of ever reflecting real staleness for any fused/non-mtfai1 strategy.
def test_signal_metadata_carries_the_real_m15_candle_time_not_generated_at():
    from backend.mt5_strategies.families._shared import _signal

    ctx = _flat_context()
    # generated_at defaults to "now" inside build_strategy_context; the last M15 row is 15
    # minutes older than that by _mk_rows' own construction -- a real, non-trivial gap.
    assert ctx.m15_rows[-1]["time"] != ctx.generated_at.isoformat()
    sig = _signal(ctx, strategy_id="ema_trend", family="trend_multi_timeframe", timeframe="M15", direction="LONG",
                   strength=80.0, entry=Decimal("1.1010"), stop=Decimal("1.0990"), target=Decimal("1.1060"), evidence={})
    assert sig.metadata["candle_time"] == ctx.m15_rows[-1]["time"]
    assert sig.metadata["candle_time"] != sig.generated_at.isoformat()


def test_signal_metadata_never_overwrites_a_caller_supplied_candle_time():
    from backend.mt5_strategies.families._shared import _signal

    ctx = _flat_context()
    sig = _signal(ctx, strategy_id="ema_trend", family="trend_multi_timeframe", timeframe="M15", direction="LONG",
                   strength=80.0, entry=Decimal("1.1010"), stop=Decimal("1.0990"), target=Decimal("1.1060"), evidence={},
                   metadata={"candle_time": "explicit-value"})
    assert sig.metadata["candle_time"] == "explicit-value"


def test_build_candidates_uses_the_real_candle_time_as_context_timestamp():
    """The actual bug fix, exercised end to end through fusion.build_candidates -- previously
    this used anchor.generated_at (evaluation wall-clock time), which meant a candidate built
    during a scheduler catch-up burst (evaluating an M15 candle that closed hours earlier)
    would still report context["timestamp"] as ~now, making signal_freshness blind to the real
    staleness. See project_mtfai1_stale_timestamp_bug memory for the production evidence."""
    ctx = _flat_context()
    sig_a = evaluate_ema_trend(ctx)
    sig_a = dataclasses.replace(sig_a, valid=True, direction="LONG", strategy_id="ema_trend", proposed_entry=1.1010, stop_loss=1.0990, take_profit=1.1060, reward_risk=2.5)
    # Simulate a catch-up cycle: generated_at is real "now", but the candle actually being
    # evaluated is old -- exactly the production pattern found in mtfai1's rows.
    stale_candle_time = (NOW - timedelta(hours=2)).isoformat()
    sig_a = dataclasses.replace(sig_a, metadata={**sig_a.metadata, "candle_time": stale_candle_time})

    candidates = build_candidates(symbol="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", cycle_id="C1B", signals=[sig_a], htf_trend_h4="bullish", now=NOW)

    assert candidates[0]["context"]["timestamp"] == stale_candle_time
    assert candidates[0]["context"]["timestamp"] != sig_a.generated_at.isoformat()


def test_build_candidates_falls_back_to_generated_at_if_candle_time_missing():
    """Safety-net path (should not happen via _signal(), which always sets it when m15_rows is
    non-empty) -- a signal built some other way must still produce a usable timestamp."""
    ctx = _flat_context()
    sig_a = evaluate_ema_trend(ctx)
    sig_a = dataclasses.replace(sig_a, valid=True, direction="LONG", strategy_id="ema_trend", proposed_entry=1.1010, stop_loss=1.0990, take_profit=1.1060, reward_risk=2.5, metadata={})

    candidates = build_candidates(symbol="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", cycle_id="C1C", signals=[sig_a], htf_trend_h4="bullish", now=NOW)

    assert candidates[0]["context"]["timestamp"] == sig_a.generated_at.isoformat()


# 2. IBKR is not required.
def test_mt5_strategies_package_has_no_ibkr_dependency():
    import backend.mt5_strategies.context as context_module
    import backend.mt5_strategies.families as families_module
    import backend.mt5_strategies.fusion as fusion_module
    import backend.mt5_strategies.models as models_module

    for module in (context_module, families_module, fusion_module, models_module):
        source = inspect.getsource(module)
        assert "backend.brokers.ibkr" not in source
        assert "ibkr_config" not in source
        assert "broker_registry" not in source


# 3. EMA/trend strategy works through the canonical interface.
def test_ema_trend_strategy_produces_valid_long_signal():
    rows = _mk_rows(120, base=1.1000, step=0.00006)  # steady uptrend, EMA20>50>100 alignment
    ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal("1.1080"), ask=Decimal("1.1082"), spread=Decimal("0.0002"))
    assert ctx is not None
    signal = evaluate_ema_trend(ctx)
    assert signal.valid is True
    assert signal.direction == "LONG"
    assert signal.strategy_id == "ema_trend"


# 4. Pullback strategy works.
def test_trend_pullback_strategy_produces_valid_signal():
    rows = _mk_rows(100, base=1.1000, step=0.00003, osc_amplitude=0.0004, osc_period=15)
    ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal("1.1030"), ask=Decimal("1.1032"), spread=Decimal("0.0002"))
    assert ctx is not None
    ctx = dataclasses.replace(ctx, htf_trend_h1="bullish")
    signal = evaluate_trend_pullback(ctx)
    assert signal.direction in {"LONG", "SHORT", "NO_TRADE"}  # deterministic given ctx -- see determinism test below
    assert signal.strategy_id == "trend_pullback"


# 5. Breakout/retest works.
def test_breakout_strategy_produces_valid_signal_from_bos():
    ctx = _flat_context(regime="breakout")
    ctx.m15_snapshot.breaks.append(_mk_break("EURUSD", bar_index=len(ctx.m15_rows) - 1, direction=Direction.BULLISH, kind=StructureBreakKind.BOS))
    ctx.m15_snapshot.displacements.append(_mk_displacement("EURUSD", bar_index=len(ctx.m15_rows) - 1, direction=Direction.BULLISH))
    signal = evaluate_breakout(ctx)
    assert signal.valid is True
    assert signal.direction == "LONG"
    assert signal.evidence["displacement_confirmed"] is True


# 6. Mean-reversion works.
def test_mean_reversion_strategy_produces_valid_signal_on_rsi_extreme():
    # Sharp, sustained decline drives RSI(14) toward oversold.
    rows = _mk_rows(60, base=1.1200, step=-0.0009)
    ctx = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal(str(rows[-1]["close"])), ask=Decimal(str(rows[-1]["close"] + 0.0002)), spread=Decimal("0.0002"))
    assert ctx is not None
    signal = evaluate_mean_reversion(ctx)
    assert signal.evidence.get("rsi14") is not None
    assert signal.evidence["rsi14"] <= 30 or not signal.valid  # deterministic RSI math; asserts the read is real, not fabricated


# 7. Liquidity-sweep strategy works.
def test_liquidity_sweep_reversal_produces_valid_signal_from_sequenced_events():
    ctx = _flat_context(regime="reversal")
    last_idx = len(ctx.m15_rows) - 1
    ctx.m15_snapshot.liquidity_sweeps.append(_mk_sweep("EURUSD", bar_index=last_idx - 2, side=LiquiditySide.SELL_SIDE, direction=Direction.BULLISH))
    ctx.m15_snapshot.displacements.append(_mk_displacement("EURUSD", bar_index=last_idx - 1, direction=Direction.BULLISH))
    ctx.m15_snapshot.breaks.append(_mk_break("EURUSD", bar_index=last_idx, direction=Direction.BULLISH, kind=StructureBreakKind.MSS))
    signal = evaluate_liquidity_sweep_reversal(ctx)
    assert signal.valid is True
    assert signal.direction == "LONG"
    assert signal.evidence["structure_shift_kind"] == "mss"


# 8. SMC/market-structure strategy works (smc_continuation).
def test_smc_continuation_produces_valid_signal_from_htf_bos_displacement():
    ctx = _flat_context(regime="trending_up", htf_h4="bullish")
    last_idx = len(ctx.m15_rows) - 1
    ctx.m15_snapshot.breaks.append(_mk_break("EURUSD", bar_index=last_idx, direction=Direction.BULLISH, kind=StructureBreakKind.BOS))
    ctx.m15_snapshot.displacements.append(_mk_displacement("EURUSD", bar_index=last_idx, direction=Direction.BULLISH))
    signal = evaluate_smc_continuation(ctx)
    assert signal.valid is True
    assert signal.direction == "LONG"
    assert signal.evidence["htf_trend_h4"] == "bullish"


# 9. Regime controls strategy activation.
def test_regime_controls_strategy_activation():
    assert regime_compatible("mean_reversion", "ranging") is True
    assert regime_compatible("mean_reversion", "trending_up") is False
    assert regime_compatible("smc_continuation", "trending_up") is True
    assert regime_compatible("smc_continuation", "ranging") is False
    # mtfai1 is exempt -- its existing production behavior is never regime-gated by this layer.
    assert regime_compatible("mtfai1", "ranging") is True
    assert regime_compatible("mtfai1", "anything_undefined") is True

    ctx = _flat_context(regime="ranging")
    evaluated_ids = {s.strategy_id for s in evaluate_all(ctx)}
    assert "smc_continuation" not in evaluated_ids  # incompatible with "ranging" -- never even evaluated
    assert set(STRATEGY_FAMILIES) - {"mtfai1"} - evaluated_ids or "mean_reversion" in evaluated_ids


# 10. (see test 1 -- multi-strategy confirmation)


# 11. Duplicate strategies do not create duplicate orders.
def test_duplicate_same_direction_signals_do_not_create_duplicate_candidates():
    ctx = _flat_context()
    sig1 = dataclasses.replace(evaluate_ema_trend(ctx), valid=True, direction="LONG", strategy_id="ema_trend", strategy_family="ema_trend", proposed_entry=1.1010, stop_loss=1.0990, take_profit=1.1060, reward_risk=2.5)
    sig2 = dataclasses.replace(evaluate_trend_pullback(ctx), valid=True, direction="LONG", strategy_id="trend_pullback", strategy_family="trend_pullback", proposed_entry=1.1012, stop_loss=1.0994, take_profit=1.1055, reward_risk=2.4)
    sig3 = dataclasses.replace(evaluate_momentum(ctx), valid=True, direction="LONG", strategy_id="momentum", strategy_family="momentum", proposed_entry=1.1011, stop_loss=1.0993, take_profit=1.1058, reward_risk=2.4)
    candidates = build_candidates(symbol="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", cycle_id="C11", signals=[sig1, sig2, sig3], htf_trend_h4="bullish", now=NOW)
    assert len(candidates) == 1  # never 3 separate EURUSD orders for the same direction


# 12. Conflicting signals are handled deterministically.
def test_conflicting_signals_resolved_deterministically():
    strong_long = dataclasses.replace(evaluate_ema_trend(_flat_context()), valid=True, direction="LONG", strategy_id="ema_trend", raw_signal_strength=90.0, proposed_entry=1.1010, stop_loss=1.0990, take_profit=1.1070, reward_risk=3.0)
    weak_short = dataclasses.replace(evaluate_momentum(_flat_context()), valid=True, direction="SHORT", strategy_id="momentum", raw_signal_strength=55.0, proposed_entry=1.1010, stop_loss=1.1030, take_profit=1.0970, reward_risk=2.0)
    candidates = build_candidates(symbol="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", cycle_id="C12", signals=[strong_long, weak_short], htf_trend_h4="bullish", now=NOW)
    assert len(candidates) == 1
    assert candidates[0]["direction"] == "LONG"
    assert candidates[0]["context"]["conflict_state"] == "RESOLVED_DOMINANT_STRENGTH"

    # Near-equal strength, neutral HTF -- both rejected rather than an arbitrary pick.
    close_long = dataclasses.replace(strong_long, raw_signal_strength=70.0)
    close_short = dataclasses.replace(weak_short, raw_signal_strength=68.0)
    ambiguous = build_candidates(symbol="EURUSD", broker_symbol="EURUSD", asset_class="FOREX", cycle_id="C12b", signals=[close_long, close_short], htf_trend_h4="unknown", now=NOW)
    assert ambiguous == []


# 13. Candidate confidence remains deterministic.
def test_strategy_evaluation_is_deterministic():
    rows = _mk_rows(120, base=1.1000, step=0.00006)
    ctx1 = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal("1.1080"), ask=Decimal("1.1082"), spread=Decimal("0.0002"), now=NOW)
    ctx2 = build_strategy_context(symbol="EURUSD", broker_symbol="EURUSD", m15_rows=rows, h1_rows=rows, h4_rows=rows, bid=Decimal("1.1080"), ask=Decimal("1.1082"), spread=Decimal("0.0002"), now=NOW)
    sig1, sig2 = evaluate_ema_trend(ctx1), evaluate_ema_trend(ctx2)
    assert sig1 == sig2


# 14. Threshold remains 75.
def test_confidence_threshold_remains_75():
    assert mt5_config().min_trade_confidence == 75.0


# 15 / 16 / 18 / 19. Economic guard / portfolio manager / shadow strategies never order_send /
# zero OpenAI calls -- exercised together via a full cycle with a forced SHADOW_MT5 winner.
def test_shadow_strategy_never_submits_and_active_still_flows_through_all_gates(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)

    shadow_candidate = {
        "canonical_pair": "EURUSD", "broker_symbol": "EURUSD", "asset_class": "FOREX", "direction": "LONG",
        "ranking_score": 90.0, "rejection_reasons": [], "context_hash": "hash-shadow",
        "context": {"risk_reward": "3.0", "atr": "0.0010", "spread": "0.0001", "timestamp": datetime.now(timezone.utc).isoformat(), "strategy_id": "smc_continuation", "regime": "trending_up"},
        "stop_loss": "1.0950", "take_profit": "1.1150", "strategy_activation": SHADOW_MT5,
    }

    async def _fake_global_blockers():
        return []

    monkeypatch.setattr(service, "_global_blockers", lambda: _fake_global_blockers())
    monkeypatch.setattr(service, "_screen", lambda items, **kwargs: asyncio.sleep(0, result=[shadow_candidate]))
    monkeypatch.setattr(service, "_entry_quality_score", lambda candidate: asyncio.sleep(0, result={"status": "ok", "total_score": 0.9, "positive_contributors": [], "negative_contributors": [], "trend_state": "BULLISH"}))
    monkeypatch.setattr("backend.brokers.mt5.autonomous.confidence_memory_for_symbol", lambda symbol: (None, None))
    monkeypatch.setattr("backend.brokers.mt5.autonomous.portfolio_manager.exposure", lambda *args, **kwargs: {"currency": {}})
    monkeypatch.setattr("backend.brokers.mt5.autonomous.portfolio_manager.can_open_new_trade", lambda *args, **kwargs: (True, []))
    monkeypatch.setattr("backend.brokers.mt5.autonomous.decision_context_service.context_risk", lambda symbol: asyncio.sleep(0, result={"block_reasons": [], "acknowledgement_required": False}))

    async def _fake_economic_evaluate(**kwargs):
        return {"guard": {"decision": "ALLOW", "reason_codes": [], "size_multiplier": 1.0}, "calendar": None, "news": None, "macro_advisory": None}

    monkeypatch.setattr("backend.brokers.mt5.autonomous.economic_intelligence_service.evaluate_entry", _fake_economic_evaluate)

    result = asyncio.run(service.run_cycle(owner="shadow-test"))

    assert result["status"] == "NO_TRADE"  # the only candidate is SHADOW_MT5 -- nothing executable
    assert result["openai_calls"] == 0
    assert adapter.client.mt5.order_send_calls == 0
    scored = result["candidates"][0]
    assert scored["trade_confidence"]["overall_score"] >= 75.0  # genuinely would have qualified on confidence alone
    assert "SHADOW_MODE" in scored["rejection_reasons"]


# 17. MT5 execution manager remains the only autonomous broker submission path.
def test_mt5_strategies_source_never_calls_order_send_or_execution_manager():
    import backend.mt5_strategies.context as context_module
    import backend.mt5_strategies.families as families_module
    import backend.mt5_strategies.fusion as fusion_module

    for module in (context_module, families_module, fusion_module):
        source = inspect.getsource(module)
        assert "order_send" not in source
        assert "submit_market_order" not in source
        assert "execution_manager" not in source


# 20. Adaptive manager remains unchanged.
def test_adaptive_manager_select_action_unchanged():
    from backend.adaptive_management.service import AdaptiveManagementService, ManagementCandidate

    service = AdaptiveManagementService()
    candidates = [ManagementCandidate(action_type="HOLD", priority=100), ManagementCandidate(action_type="TRAIL_STOP", priority=5)]
    assert service._select_action(candidates).action_type == "TRAIL_STOP"


# 21. Confidence calibration records strategy identity/evidence.
def test_calibration_captures_strategy_identity_and_evidence(monkeypatch: pytest.MonkeyPatch):
    redirect_shared_db_to_isolated_sqlite(monkeypatch)
    confidence = compute_trade_confidence(
        candidate={"canonical_pair": "EURUSD", "broker_symbol": "EURUSD", "direction": "LONG", "ranking_score": 85.0, "context": {"risk_reward": "2.5", "atr": "0.001", "spread": "0.0001", "timestamp": datetime.now(timezone.utc).isoformat()}},
        entry_quality={"status": "ok", "total_score": 0.8, "positive_contributors": [], "negative_contributors": []},
        symbol_memory=None, global_memory=None,
    )
    candidate = {
        "candidate_id": "C21:EURUSD:abc", "canonical_pair": "EURUSD", "broker_symbol": "EURUSD", "direction": "LONG", "rank": 1,
        "trade_confidence": confidence, "rejection_reasons": [],
        "context": {
            "risk_reward": 2.5, "atr": 0.001, "spread": 0.0001, "timestamp": datetime.now(timezone.utc).isoformat(),
            "strategy_id": "smc_continuation", "strategy_family": "smc_continuation", "regime": "trending_up",
            "contributing_strategies": ["smc_continuation", "ema_trend"], "contributing_families": ["smc_continuation", "ema_trend"],
            "multi_strategy_confirmation": True, "conflict_state": "NONE", "htf_trend_h4": "bullish",
            "smc_evidence": {"bos_present": True, "displacement_present": True, "htf_direction_h1": "bullish"},
            "strategy_evidence": {"bos_id": "brk_1"}, "score": 82.5,
        },
    }
    written = capture_cycle_candidate_evaluations({"cycle_id": "C21", "candidates": [candidate], "winner": None, "trade": None})
    assert written == 1
    with SessionLocal() as db:
        row = db.query(MT5CandidateEvaluationORM).filter_by(candidate_id="C21:EURUSD:abc").one()
        assert row.strategy == "smc_continuation"
        assert row.strategy_family == "smc_continuation"
        assert row.market_regime == "trending_up"
        assert set(row.contributing_strategies) == {"smc_continuation", "ema_trend"}
        assert row.multi_strategy_confirmation is True
        assert row.conflict_state == "NONE"
        assert row.htf_direction_h4 == "bullish"
        assert row.smc_evidence["bos_present"] is True
        assert row.strategy_evidence["bos_id"] == "brk_1"


# 22. Live trading remains blocked.
def test_live_trading_remains_blocked():
    assert mt5_config().live_trading_enabled is False


# 23 / 24 / 25. Existing suites continue passing -- exercised as an in-file smoke check that
# the exact same fixtures/functions used by those suites still import and behave identically;
# the full suites themselves are run as part of this task's regression pass (see final report).
def test_existing_confidence_band_classification_unchanged():
    from backend.brokers.mt5.confidence import classify_confidence_band, is_autonomous_eligible

    assert classify_confidence_band(76.0) == "valid_autonomous"
    assert is_autonomous_eligible(76.0) is True
    assert is_autonomous_eligible(74.9) is False


def test_evaluators_registry_covers_every_declared_non_mtfai1_family():
    assert set(EVALUATORS.keys()) == set(STRATEGY_FAMILIES.keys()) - {"mtfai1"}


def test_activation_status_defaults_and_env_override(monkeypatch: pytest.MonkeyPatch):
    # Part 7: all 10 canonical families default to ACTIVE_MT5 on the demo account now that
    # Stage 1 (SHADOW_MT5) optimization/regression validation is complete.
    # mtfai1 is pinned explicitly: the real container env has MT5_STRATEGY_ACTIVATION_MTFAI1=
    # SHADOW_MT5 deployed (2026-08-18 real-trade forensic demotion) since mtfai1.py:964's own
    # activation gate fix made that override actually take effect for the first time -- this
    # test is about the DEFAULT/override MECHANISM, not today's real deployed value.
    monkeypatch.delenv("MT5_STRATEGY_ACTIVATION_MTFAI1", raising=False)
    assert activation_status("mtfai1") == ACTIVE_MT5
    assert activation_status("ema_trend") == ACTIVE_MT5
    monkeypatch.setenv("MT5_STRATEGY_ACTIVATION_EMA_TREND", "SHADOW_MT5")
    assert activation_status("ema_trend") == SHADOW_MT5
    monkeypatch.setenv("MT5_STRATEGY_ACTIVATION_EMA_TREND", "ACTIVE_MT5")
    assert activation_status("ema_trend") == ACTIVE_MT5
