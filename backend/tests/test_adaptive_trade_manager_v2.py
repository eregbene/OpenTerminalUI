from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.adaptive_management import service as adaptive_service
from backend.adaptive_management import tp_protection
from backend.adaptive_management.orm import (
    AdaptiveActivationORM,
    AdaptiveCircuitBreakerORM,
    AdaptiveManagementActionORM,
    AdaptivePositionStateORM,
    AdaptiveTradeEventORM,
    CounterfactualOutcomeORM,
    TradePathSnapshotORM,
)
from backend.brokers.mt5 import account_registry
from backend.brokers.mt5.account_registry import AccountClassification, MT5AccountProfileORM
from backend.brokers.mt5.risk_budget import compute_risk_multiplier, effective_risk_budget_usd
from backend.brokers.mt5.take_profit import select_take_profit
from backend.shared.db import Base


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(account_registry, "SessionLocal", SessionLocal)
    monkeypatch.setattr(adaptive_service, "SessionLocal", SessionLocal)
    return SessionLocal


class FakeAccount10k:
    login = 900001
    server = "Bensim-Demo"
    company = "Bensim"
    currency = "USD"


class FakeAccount100k:
    login = 800001
    server = "Bensim-Demo"
    company = "Bensim"
    currency = "USD"


class FakeAccountLive:
    login = 700001
    server = "Bensim-Live"
    company = "Bensim"
    currency = "USD"


# ---------------------------------------------------------------------------
# Account isolation / fingerprint / registry
# ---------------------------------------------------------------------------


def test_new_10k_account_gets_separate_isolated_state(monkeypatch):
    _session_factory(monkeypatch)
    fp_10k = account_registry.fingerprint_account(FakeAccount10k())
    fp_100k = account_registry.fingerprint_account(FakeAccount100k())

    blockers_10k = account_registry.account_blockers(FakeAccount10k(), account_mode="DEMO")

    assert fp_10k.fingerprint_hash != fp_100k.fingerprint_hash
    assert blockers_10k == []
    profile = account_registry.get_profile(fp_10k.fingerprint_hash)
    assert profile["classification"] == AccountClassification.INTERNAL_DEMO.value


def test_previous_100k_account_state_not_inherited_by_new_account(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    fp_100k = account_registry.fingerprint_account(FakeAccount100k())
    account_registry.record_sighting(fp_100k, account_mode="DEMO")
    with SessionLocal() as db:
        row = db.get(MT5AccountProfileORM, fp_100k.fingerprint_hash)
        row.approved = True
        row.notes = "legacy_100k_progression_state"
        db.commit()

    fp_10k = account_registry.fingerprint_account(FakeAccount10k())
    account_registry.record_sighting(fp_10k, account_mode="DEMO")

    with SessionLocal() as db:
        fresh = db.get(MT5AccountProfileORM, fp_10k.fingerprint_hash)
        assert fresh.approved is False
        assert fresh.notes is None


def test_unknown_non_demo_account_is_blocked(monkeypatch):
    _session_factory(monkeypatch)
    blockers = account_registry.account_blockers(FakeAccountLive(), account_mode="LIVE")
    assert "ACCOUNT_NOT_APPROVED" in blockers


def test_live_trading_requires_both_env_gate_and_persisted_approval(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    fp = account_registry.fingerprint_account(FakeAccountLive())
    account_registry.record_sighting(fp, account_mode="LIVE")
    with SessionLocal() as db:
        row = db.get(MT5AccountProfileORM, fp.fingerprint_hash)
        row.classification = AccountClassification.PERSONAL_LIVE.value
        row.approved = True
        db.commit()

    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    blockers_no_env = account_registry.account_blockers(FakeAccountLive(), account_mode="LIVE")
    assert "LIVE_TRADING_ENV_GATE_DISABLED" in blockers_no_env

    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    blockers_with_env = account_registry.account_blockers(FakeAccountLive(), account_mode="LIVE")
    assert "LIVE_TRADING_ENV_GATE_DISABLED" not in blockers_with_env
    assert blockers_with_env == []


def test_prop_trading_remains_blocked_without_explicit_approval(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    fp = account_registry.fingerprint_account(FakeAccountLive())
    account_registry.record_sighting(fp, account_mode="LIVE")
    with SessionLocal() as db:
        row = db.get(MT5AccountProfileORM, fp.fingerprint_hash)
        row.classification = AccountClassification.PROP_FUNDED.value
        db.commit()
    monkeypatch.setenv("PROP_TRADING_ENABLED", "true")

    blockers = account_registry.account_blockers(FakeAccountLive(), account_mode="LIVE")

    assert "ACCOUNT_NOT_APPROVED" in blockers


def test_account_fingerprint_never_contains_password():
    fp = account_registry.fingerprint_account(FakeAccount10k())
    assert "password" not in fp.__dataclass_fields__
    assert fp.login == FakeAccount10k.login
    assert fp.server == FakeAccount10k.server


# ---------------------------------------------------------------------------
# Position sizing / risk budget (no martingale, drawdown/exposure/news aware)
# ---------------------------------------------------------------------------


def test_risk_multiplier_never_exceeds_full_configured_risk():
    adjustment = compute_risk_multiplier()
    assert adjustment.multiplier <= 1.0
    assert adjustment.multiplier > 0


def test_drawdown_reduces_effective_risk_budget():
    baseline, _ = effective_risk_budget_usd(Decimal("25.00"))
    reduced, detail = effective_risk_budget_usd(Decimal("25.00"), drawdown_pct=10.0)
    assert reduced < baseline
    assert detail["multiplier"] < 1.0


def test_correlated_exposure_reduces_volume_via_multiplier():
    baseline = compute_risk_multiplier()
    correlated = compute_risk_multiplier(correlated_exposure_ratio=0.9)
    assert correlated.multiplier < baseline.multiplier


def test_portfolio_exposure_reduces_multiplier():
    baseline = compute_risk_multiplier()
    exposed = compute_risk_multiplier(portfolio_exposure_ratio=0.9)
    assert exposed.multiplier < baseline.multiplier


def test_high_economic_risk_reduces_multiplier_never_increases_it():
    baseline = compute_risk_multiplier()
    high_news = compute_risk_multiplier(economic_risk_level="high")
    assert high_news.multiplier < baseline.multiplier
    assert high_news.multiplier <= 1.0


def test_no_martingale_losing_history_never_increases_multiplier():
    # A losing trade can only ever show up here as drawdown -- there is no code path that scales
    # multiplier upward in response to a loss. Increasing drawdown must never increase the result.
    after_small_loss = compute_risk_multiplier(drawdown_pct=1.0)
    after_bigger_loss = compute_risk_multiplier(drawdown_pct=5.0)
    assert after_bigger_loss.multiplier <= after_small_loss.multiplier


def test_multiplier_respects_configured_floor():
    adjustment = compute_risk_multiplier(drawdown_pct=50.0, portfolio_exposure_ratio=1.0, correlated_exposure_ratio=1.0, economic_risk_level="high", strategy_confidence=0.0, min_multiplier=0.25)
    assert adjustment.multiplier >= 0.25


# ---------------------------------------------------------------------------
# Stop-quality v2 evaluator
# ---------------------------------------------------------------------------


def test_stop_quality_v2_rejects_stop_inside_volatility_floor():
    result = tp_protection.classify_stop_quality_v2(sl_distance=0.0005, atr=0.0020, spread=0.0001, structure_distance=None, broker_min_stop_distance=None)
    assert result["classification"] == "TOO_TIGHT"


def test_stop_quality_v2_accepts_structure_based_stop_within_bounds():
    result = tp_protection.classify_stop_quality_v2(sl_distance=0.0030, atr=0.0020, spread=0.0001, structure_distance=0.0031, broker_min_stop_distance=0.0005)
    assert result["classification"] == "VALID"


def test_stop_quality_v2_rejects_below_broker_minimum_distance():
    result = tp_protection.classify_stop_quality_v2(sl_distance=0.0002, atr=0.0020, spread=0.0001, structure_distance=None, broker_min_stop_distance=0.0010)
    assert result["classification"] == "INVALID_BROKER_DISTANCE"


def test_stop_quality_v2_rejects_unaffordable_risk_even_if_structurally_valid():
    result = tp_protection.classify_stop_quality_v2(
        sl_distance=0.0030,
        atr=0.0020,
        spread=0.0001,
        structure_distance=0.0031,
        broker_min_stop_distance=0.0005,
        effective_risk_budget_usd=25.0,
        projected_monetary_loss_usd=100.0,
    )
    assert result["classification"] == "UNAFFORDABLE_RISK"


def test_stop_quality_v2_rejects_too_wide_stop():
    result = tp_protection.classify_stop_quality_v2(sl_distance=0.0090, atr=0.0020, spread=0.0001, structure_distance=None, broker_min_stop_distance=0.0005)
    assert result["classification"] == "TOO_WIDE"


# ---------------------------------------------------------------------------
# Take-profit selection
# ---------------------------------------------------------------------------


def test_take_profit_never_below_minimum_reward_multiple():
    tp = select_take_profit(direction="LONG", entry=Decimal("1.1000"), stop_loss=Decimal("1.0980"), opposing_structure_level=None, atr=None)
    stop_distance = Decimal("0.0020")
    assert (tp["tp1"] - Decimal("1.1000")) >= stop_distance * Decimal("1.5")


def test_take_profit_uses_opposing_structure_when_it_clears_reward_floor():
    tp = select_take_profit(direction="LONG", entry=Decimal("1.1000"), stop_loss=Decimal("1.0980"), opposing_structure_level=Decimal("1.1050"), atr=Decimal("0.0010"))
    assert tp["basis"] == "opposing_structure"
    assert tp["tp1"] == Decimal("1.1050")


def test_take_profit_capped_at_maximum_reward_multiple():
    tp = select_take_profit(direction="LONG", entry=Decimal("1.1000"), stop_loss=Decimal("1.0990"), opposing_structure_level=Decimal("1.2000"), atr=None)
    stop_distance = Decimal("0.0010")
    assert (tp["tp1"] - Decimal("1.1000")) <= stop_distance * Decimal("5.0")


# ---------------------------------------------------------------------------
# Rate limiting (previously stored-but-never-enforced columns)
# ---------------------------------------------------------------------------


def test_rate_limit_ok_enforces_hourly_cap():
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B1", state="closed", actions_this_hour=0)
    for _ in range(3):
        assert adaptive_service._rate_limit_ok(breaker, maximum_actions_per_hour=3) is True
    assert adaptive_service._rate_limit_ok(breaker, maximum_actions_per_hour=3) is False


def test_rate_limit_resets_after_window_elapses():
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B2", state="closed", actions_this_hour=5, actions_hour_window_started_at=datetime.now(timezone.utc) - timedelta(hours=2))
    assert adaptive_service._rate_limit_ok(breaker, maximum_actions_per_hour=3) is True
    assert breaker.actions_this_hour == 1


# ---------------------------------------------------------------------------
# v2 action gating (shadow vs enforce, account fingerprint, HOLD, sole mutation path)
# ---------------------------------------------------------------------------


def _base_activation(**overrides):
    defaults = dict(
        activation_id="ACT1",
        policy_id=adaptive_service.ACTIVE_POLICY_ID,
        policy_version="v1",
        mode="demo_active",
        demo_account="900001",
        account_fingerprint=None,
        effective_from=datetime.now(timezone.utc),
        eligible_strategies=[],
        eligible_symbols=[],
        maximum_actions_per_hour=6,
        active=True,
    )
    defaults.update(overrides)
    return AdaptiveActivationORM(**defaults)


def _base_action(action_type: str, **overrides):
    defaults = dict(
        action_id="A1",
        activation_id="ACT1",
        position_id="P1",
        policy_id=adaptive_service.ACTIVE_POLICY_ID,
        action_type=action_type,
        priority=50,
        mode="enforce",
        status="selected",
        idempotency_key="A1-KEY",
        broker_mutation_attempted=False,
    )
    defaults.update(overrides)
    return AdaptiveManagementActionORM(**defaults)


def _base_state(**overrides):
    defaults = dict(position_id="P1", symbol="EURUSD", direction="LONG", broker_ticket="P1", entry_price=1.1, managed_automatically=True)
    defaults.update(overrides)
    return AdaptivePositionStateORM(**defaults)


def test_v2_action_blocked_in_shadow_mode(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_MANAGEMENT_MODE", "shadow")
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation()
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B3", state="closed", actions_this_hour=0)
    action = _base_action("MOVE_SL_TO_REDUCED_RISK")
    state = _base_state()

    allowed = svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP1")

    assert allowed is False


def test_v2_action_allowed_in_enforce_mode_when_otherwise_valid(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_MANAGEMENT_MODE", "enforce")
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation()
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B4", state="closed", actions_this_hour=0)
    action = _base_action("MOVE_SL_TO_REDUCED_RISK")
    state = _base_state()

    allowed = svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP1")

    assert allowed is True


def test_existing_action_types_unaffected_by_v2_mode_shadow(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_MANAGEMENT_MODE", "shadow")
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation()
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B5", state="closed", actions_this_hour=0)
    action = _base_action("MOVE_SL_BREAKEVEN")
    state = _base_state()

    allowed = svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP1")

    assert allowed is True


def test_account_fingerprint_mismatch_blocks_execution(monkeypatch):
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation(account_fingerprint="FP_OLD_ACCOUNT")
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B6", state="closed", actions_this_hour=0)
    action = _base_action("MOVE_SL_BREAKEVEN")
    state = _base_state()

    allowed_wrong_account = svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP_NEW_ACCOUNT")
    allowed_unknown_account = svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint=None)
    allowed_matching_account = svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP_OLD_ACCOUNT")

    assert allowed_wrong_account is False
    assert allowed_unknown_account is False
    assert allowed_matching_account is True


def test_hold_action_never_executes(monkeypatch):
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation()
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B7", state="closed", actions_this_hour=0)
    action = _base_action("HOLD")
    state = _base_state()

    assert svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP1") is False


def test_live_account_mode_blocks_execution_regardless_of_other_checks(monkeypatch):
    monkeypatch.setenv("ADAPTIVE_MANAGEMENT_MODE", "enforce")
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "LIVE"})())
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation()
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B8", state="closed", actions_this_hour=0)
    action = _base_action("MOVE_SL_BREAKEVEN")
    state = _base_state()

    assert svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP1") is False


# ---------------------------------------------------------------------------
# _v2_candidates: SL never widened, reduced-risk stop only tightens
# ---------------------------------------------------------------------------


def test_move_sl_to_reduced_risk_only_tightens_never_widens(monkeypatch):
    svc = adaptive_service.AdaptiveManagementService()
    state = _base_state(direction="LONG", entry_price=1.1000, current_sl=1.0950, winner_classification="healthy_pullback")

    candidates = svc._v2_candidates(state, r_now=0.6, max_r=0.6, atr=0.0010, atr_r=0.2, regime="trend", zone="zone_50_65", normalized_candles=[])

    reduced_risk = [c for c in candidates if c.action_type == "MOVE_SL_TO_REDUCED_RISK"]
    assert reduced_risk, "expected a MOVE_SL_TO_REDUCED_RISK candidate between 0.5R and 1.0R"
    proposed_sl = reduced_risk[0].requested_sl
    assert proposed_sl > state.current_sl
    assert proposed_sl < state.entry_price


def test_no_reduced_risk_candidate_when_thesis_invalidated():
    svc = adaptive_service.AdaptiveManagementService()
    state = _base_state(direction="LONG", entry_price=1.1000, current_sl=1.0950, winner_classification="invalidated")

    candidates = svc._v2_candidates(state, r_now=0.6, max_r=0.6, atr=0.0010, atr_r=0.2, regime="trend", zone="zone_50_65", normalized_candles=[])

    assert not [c for c in candidates if c.action_type == "MOVE_SL_TO_REDUCED_RISK"]


# ---------------------------------------------------------------------------
# OpenAI / Portfolio Manager / Execution Manager separation of concerns
# ---------------------------------------------------------------------------


def test_v2_action_types_are_the_only_new_broker_mutating_vocabulary():
    # OpenAI never appears anywhere in this module's execution path -- _can_execute only reads
    # deterministic state (activation/breaker/mode/fingerprint/rate-limit). This test pins that
    # V2_ACTION_TYPES stays a closed, deterministic set so no advisory-only caller can widen it.
    assert adaptive_service.V2_ACTION_TYPES == {"MOVE_SL_TO_REDUCED_RISK", "EXTEND_TP", "REDUCE_TP"}
    assert adaptive_service.SLTP_MODIFY_ACTION_TYPES >= adaptive_service.V2_ACTION_TYPES


# ---------------------------------------------------------------------------
# Automatic replay-on-close (Trade Intelligence self-learning foundation)
# ---------------------------------------------------------------------------


def _replay_candles():
    start = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    prices = [1.1000, 1.1005, 1.1010, 1.1008, 1.1015, 1.1020, 1.1018, 1.1025, 1.1030, 1.1028, 1.1035, 1.1040, 1.1045]
    rows = []
    for i, close in enumerate(prices):
        rows.append({"time": (start + timedelta(minutes=5 * i)).isoformat(), "open": close - 0.0003, "high": close + 0.0004, "low": close - 0.0005, "close": close, "spread": 1})
    return rows


class _FakeReplayCandle:
    def __init__(self, payload):
        self.payload = payload

    def model_dump(self, mode="json"):
        return self.payload


class _FakeReplayAdapter:
    async def candles(self, symbol, timeframe, count=100):
        return [_FakeReplayCandle(row) for row in _replay_candles()]


def _closed_deal(position_id: str, price: float, utc_time: datetime, realized_pnl: float = 40.0):
    return AdaptiveTradeEventORM(
        event_id=f"DEAL_{position_id}",
        session_id="S1",
        trade_id=position_id,
        position_id=position_id,
        event_type="DEAL",
        symbol="EURUSD",
        price=price,
        realized_pnl=realized_pnl,
        utc_time=utc_time,
    )


def test_auto_replay_runs_for_closed_trade_with_deal_data(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(adaptive_service, "mt5_adapter", _FakeReplayAdapter())
    svc = adaptive_service.AdaptiveManagementService()
    entry_time = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    exit_time = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add(_base_state(position_id="CLOSED1", direction="LONG", entry_price=1.1000, current_sl=1.0980, original_sl=1.0980, original_tp=1.1040, opened_at=entry_time, closed_detected_at=exit_time, strategy_id="BENSIM_AUTO", timeframe="M5"))
        db.add(_closed_deal("CLOSED1", 1.1040, exit_time))
        db.commit()

    with SessionLocal() as db:
        asyncio.run(svc._auto_replay_recently_closed(db))

    with SessionLocal() as db:
        row = db.get(AdaptivePositionStateORM, "CLOSED1")
        assert row.replay_completed_at is not None
        outcomes = db.query(CounterfactualOutcomeORM).filter(CounterfactualOutcomeORM.trade_id == "CLOSED1").all()
        assert len(outcomes) > 1  # simulated against multiple named policies
        assert db.query(TradePathSnapshotORM).count() >= 1


def test_auto_replay_skips_trade_with_no_deal_data_yet(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(adaptive_service, "mt5_adapter", _FakeReplayAdapter())
    svc = adaptive_service.AdaptiveManagementService()
    entry_time = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    exit_time = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add(_base_state(position_id="CLOSED2", direction="LONG", entry_price=1.1000, current_sl=1.0980, original_sl=1.0980, original_tp=1.1040, opened_at=entry_time, closed_detected_at=exit_time))
        db.commit()

    with SessionLocal() as db:
        asyncio.run(svc._auto_replay_recently_closed(db))

    with SessionLocal() as db:
        row = db.get(AdaptivePositionStateORM, "CLOSED2")
        # No deal data was available yet -- must stay NULL so a later cycle retries, not skip forever.
        assert row.replay_completed_at is None


def test_auto_replay_never_touches_broker(monkeypatch):
    # _FakeReplayAdapter exposes ONLY .candles() -- no client/mt5/order_send/positions method
    # exists on it at all, so any code path that tried to read positions or submit an order
    # would raise AttributeError and fail this test, rather than silently doing nothing.
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(adaptive_service, "mt5_adapter", _FakeReplayAdapter())
    svc = adaptive_service.AdaptiveManagementService()
    entry_time = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    exit_time = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add(_base_state(position_id="CLOSED3", direction="LONG", entry_price=1.1000, current_sl=1.0980, original_sl=1.0980, original_tp=1.1040, opened_at=entry_time, closed_detected_at=exit_time))
        db.add(_closed_deal("CLOSED3", 1.1040, exit_time))
        db.commit()

    with SessionLocal() as db:
        asyncio.run(svc._auto_replay_recently_closed(db))

    with SessionLocal() as db:
        row = db.get(AdaptivePositionStateORM, "CLOSED3")
        assert row.replay_completed_at is not None


# ---------------------------------------------------------------------------
# Probability Engine Foundation (cohort statistics, sample-size confidence)
# ---------------------------------------------------------------------------


def test_probability_report_groups_by_composite_cohort_and_grades_confidence(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    svc = adaptive_service.AdaptiveManagementService()
    with SessionLocal() as db:
        # Cohort A: SMC/EURUSD/M15/insufficient_data-session -- 12 trades, all reaching >=1R,
        # half continuing to >=2R, all winners -> enough for MEDIUM confidence.
        for i in range(12):
            db.add(_base_state(position_id=f"A{i}", symbol="EURUSD", strategy_id="SMC", timeframe="M15", entry_regime="trending_up", max_achieved_r=2.0 if i % 2 == 0 else 1.2, current_giveback_r=0.1))
        # Cohort B: a single trade -- must stay LOW confidence.
        db.add(_base_state(position_id="B0", symbol="GBPUSD", strategy_id="ICT", timeframe="H1", entry_regime="ranging", max_achieved_r=0.3))
        db.commit()

    report = svc.probability_report()

    cohort_a_key = "strategy_id=SMC|symbol=EURUSD|timeframe=M15|session=UNKNOWN|entry_regime=trending_up"
    cohort_b_key = "strategy_id=ICT|symbol=GBPUSD|timeframe=H1|session=UNKNOWN|entry_regime=ranging"
    assert report["total_trades"] == 13
    cohort_a = report["cohorts"][cohort_a_key]
    cohort_b = report["cohorts"][cohort_b_key]
    assert cohort_a["sample_size"] == 12
    assert cohort_a["confidence"] == "MEDIUM"
    assert cohort_a["pct_reaching_1r_continued_to_2r"]["value"] == 0.5
    assert cohort_b["sample_size"] == 1
    assert cohort_b["confidence"] == "LOW"
    assert cohort_b["pct_reaching_1r_continued_to_2r"]["value"] is None  # never reached +1R
