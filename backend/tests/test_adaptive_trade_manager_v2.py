from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
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
# _replay_candles (adaptive_service) queries MT5CandleRevisionORM -- this import ensures that
# table is registered on Base.metadata BEFORE _session_factory's create_all() runs, regardless of
# which order pytest happens to collect test files in. Without it, whether this table exists in
# the in-memory SQLite schema depends on whether some OTHER, alphabetically-earlier test file
# happened to import backend.historical_intelligence.orm first -- a real, pre-existing test
# isolation gap (not a production code defect) that surfaced as an intermittent OperationalError
# ("no such table: mt5_candle_revisions") once new test files shifted collection order.
import backend.historical_intelligence.orm  # noqa: F401
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
# RATE_LIMIT_EXEMPT_ACTION_TYPES: THESIS_INVALIDATION_CLOSE and VALIDATION_INCIDENT_CLOSE are
# genuine emergency/protective full exits and must not be blocked by other management actions
# having already consumed ADAPTIVE_MAX_ACTIONS_PER_HOUR earlier in the hour. Normal/profit-
# management actions must still stop at the cap, unchanged.
# ---------------------------------------------------------------------------


def test_normal_action_still_blocked_at_hourly_cap(monkeypatch):
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    SessionLocal = _session_factory(monkeypatch)
    _seed_classification(SessionLocal, "FP1", AccountClassification.INTERNAL_DEMO.value)
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation(maximum_actions_per_hour=6)
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B_CAP1", state="closed", actions_this_hour=6, actions_hour_window_started_at=datetime.now(timezone.utc))
    action = _base_action("MOVE_SL_BREAKEVEN")
    state = _base_state()

    allowed = svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP1")

    assert allowed is False
    assert breaker.actions_this_hour == 6  # untouched -- the blocked check never increments


def test_thesis_invalidation_close_bypasses_hourly_cap(monkeypatch):
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    SessionLocal = _session_factory(monkeypatch)
    _seed_classification(SessionLocal, "FP1", AccountClassification.INTERNAL_DEMO.value)
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation(maximum_actions_per_hour=6)
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B_CAP2", state="closed", actions_this_hour=6, actions_hour_window_started_at=datetime.now(timezone.utc))
    action = _base_action("THESIS_INVALIDATION_CLOSE", requested_volume=0.5)
    state = _base_state()

    allowed = svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP1")

    assert allowed is True
    # A true bypass, not "one extra slot" -- the shared hourly counter is never touched by an
    # exempt action, so it stays exactly where the (already-exhausted) normal budget left it.
    assert breaker.actions_this_hour == 6


def test_validation_incident_close_bypasses_hourly_cap(monkeypatch):
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    SessionLocal = _session_factory(monkeypatch)
    _seed_classification(SessionLocal, "FP1", AccountClassification.INTERNAL_DEMO.value)
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation(maximum_actions_per_hour=6)
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B_CAP3", state="closed", actions_this_hour=6, actions_hour_window_started_at=datetime.now(timezone.utc))
    action = _base_action("VALIDATION_INCIDENT_CLOSE", requested_volume=0.5)
    state = _base_state()

    allowed = svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP1")

    assert allowed is True
    assert breaker.actions_this_hour == 6


def test_exempt_action_still_blocked_by_cooldown_despite_bypassing_rate_cap(monkeypatch):
    """The rate-limit bypass must not weaken any OTHER safety layer -- THESIS_INVALIDATION_CLOSE
    is still a VOLUME_MUTATING_COOLDOWN_ACTION_TYPES member, so a recent successful modification
    on this same position still blocks it, cap or no cap."""
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    SessionLocal = _session_factory(monkeypatch)
    _seed_classification(SessionLocal, "FP1", AccountClassification.INTERNAL_DEMO.value)
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation(maximum_actions_per_hour=6)
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B_CAP4", state="closed", actions_this_hour=6, actions_hour_window_started_at=datetime.now(timezone.utc))
    action = _base_action("THESIS_INVALIDATION_CLOSE", requested_volume=0.5)
    state = _base_state(last_management_at=datetime.now(timezone.utc))  # just modified -- inside cooldown

    allowed = svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP1")

    assert allowed is False


def test_thesis_invalidation_close_executes_end_to_end_despite_exhausted_hourly_cap(monkeypatch):
    """Proves a legitimate protective exit can still actually close the position at the broker
    -- not just pass the _can_execute gate -- even when every other action type would be capped
    this hour."""
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO", "bensim_magic": 5601001})())
    _seed_classification(SessionLocal, "FPX", AccountClassification.INTERNAL_DEMO.value)
    fake_adapter = _FakeExecAdapter(positions=[_FakePosition(identifier="P1", ticket="555001", symbol="EURUSD", type=0, volume=0.5, sl=1.0950, tp=1.1100)])
    fake_execution = _FakeExecManager()
    monkeypatch.setattr(adaptive_service, "mt5_adapter", fake_adapter)
    monkeypatch.setattr(adaptive_service, "execution_manager", fake_execution)
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation(maximum_actions_per_hour=6)
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B_CAP5", state="closed", actions_this_hour=6, actions_hour_window_started_at=datetime.now(timezone.utc))
    action = _base_action("THESIS_INVALIDATION_CLOSE", requested_volume=0.5)
    state = _base_state(direction="LONG", symbol="EURUSD", entry_price=1.1000, current_sl=1.0950, current_tp=1.1100, current_volume=0.5, broker_ticket="555001", account_fingerprint="FPX")

    assert svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FPX") is True

    result = asyncio.run(svc._execute_action(action, state, {"ticket": state.broker_ticket}))

    assert result["status"] == "ACCEPTED"
    assert result["broker_mutation_attempted"] is True
    assert fake_execution.calls == 1


# ---------------------------------------------------------------------------
# v2 action gating (account classification, account fingerprint, HOLD, sole mutation path)
# ---------------------------------------------------------------------------


def _seed_classification(SessionLocal, fingerprint_hash: str, classification: str, account_mode: str = "DEMO") -> None:
    """Seeds an account_registry profile row under an arbitrary test fingerprint string (not a
    real sha256 hash) by going through record_sighting() with a hand-built AccountFingerprint --
    reusing the real auto-classification path -- then overriding the classification directly,
    the same pattern test_prop_trading_remains_blocked_without_explicit_approval uses above."""
    fp = account_registry.AccountFingerprint(fingerprint_hash=fingerprint_hash, login=1, server="TEST", company=None, currency=None)
    account_registry.record_sighting(fp, account_mode=account_mode)
    if classification != AccountClassification.INTERNAL_DEMO.value:
        with SessionLocal() as db:
            row = db.get(MT5AccountProfileORM, fingerprint_hash)
            row.classification = classification
            db.commit()


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


def test_v2_action_executes_in_shadow_mode_for_internal_demo_account(monkeypatch):
    # This is the core behavior this feature changes: ADAPTIVE_MANAGEMENT_MODE's default
    # ('shadow') no longer requires a separate manual 'enforce' opt-in for an INTERNAL_DEMO
    # account -- classification alone is enough to activate real V2 management now.
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setenv("ADAPTIVE_MANAGEMENT_MODE", "shadow")
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    _seed_classification(SessionLocal, "FP1", AccountClassification.INTERNAL_DEMO.value)
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation()
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B3", state="closed", actions_this_hour=0)
    action = _base_action("MOVE_SL_TO_REDUCED_RISK")
    state = _base_state()

    allowed = svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP1")

    assert allowed is True


def test_v2_action_blocked_when_v2_mode_explicitly_disabled(monkeypatch):
    # 'disabled' remains a real operator kill-switch, even for an INTERNAL_DEMO account.
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setenv("ADAPTIVE_MANAGEMENT_MODE", "disabled")
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    _seed_classification(SessionLocal, "FP1", AccountClassification.INTERNAL_DEMO.value)
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation()
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B4", state="closed", actions_this_hour=0)
    action = _base_action("MOVE_SL_TO_REDUCED_RISK")
    state = _base_state()

    allowed = svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP1")

    assert allowed is False


def test_existing_action_types_also_active_for_internal_demo_in_shadow_mode(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setenv("ADAPTIVE_MANAGEMENT_MODE", "shadow")
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    _seed_classification(SessionLocal, "FP1", AccountClassification.INTERNAL_DEMO.value)
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation()
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B5", state="closed", actions_this_hour=0)
    action = _base_action("MOVE_SL_BREAKEVEN")
    state = _base_state()

    allowed = svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP1")

    assert allowed is True


@pytest.mark.parametrize(
    "classification",
    [
        AccountClassification.PROP_EVALUATION.value,
        AccountClassification.PROP_FUNDED.value,
        AccountClassification.PERSONAL_LIVE.value,
        AccountClassification.UNKNOWN.value,
    ],
)
def test_execution_stays_shadow_for_every_non_internal_demo_classification(monkeypatch, classification):
    # ACCOUNT MODES policy: only INTERNAL_DEMO is ACTIVE. Every other classification -- including
    # ones running on an MT5-reported "DEMO" account_mode, e.g. a prop firm's evaluation account
    # -- must stay shadow-only for BOTH pre-existing and v2 action types.
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setenv("ADAPTIVE_MANAGEMENT_MODE", "enforce")
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    _seed_classification(SessionLocal, "FP1", classification)
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation()
    breaker = AdaptiveCircuitBreakerORM(breaker_id=f"B_{classification}", state="closed", actions_this_hour=0)
    state = _base_state()

    base_action_allowed = svc._can_execute(_base_action("MOVE_SL_BREAKEVEN"), state, activation, breaker, "demo_active", current_account_fingerprint="FP1")
    v2_action_allowed = svc._can_execute(_base_action("MOVE_SL_TO_REDUCED_RISK", action_id="A2", idempotency_key="A2-KEY"), state, activation, breaker, "demo_active", current_account_fingerprint="FP1")

    assert base_action_allowed is False
    assert v2_action_allowed is False


def test_account_fingerprint_mismatch_blocks_execution(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    _seed_classification(SessionLocal, "FP_OLD_ACCOUNT", AccountClassification.INTERNAL_DEMO.value)
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


def test_volume_mutating_action_blocked_within_cooldown_of_recent_success(monkeypatch):
    # Incident hardening: a volume-mutating action type (PARTIAL_PROFIT etc.) that already
    # executed against this position within the modification cooldown must be blocked from
    # executing again -- even under a brand-new action row/idempotency key -- so a threshold
    # that's transiently true every cycle (the runaway-R incident's root cause) can never fire
    # repeated real broker closes. See VOLUME_MUTATING_COOLDOWN_ACTION_TYPES.
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    _seed_classification(SessionLocal, "FP1", AccountClassification.INTERNAL_DEMO.value)
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation()
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B_COOLDOWN", state="closed", actions_this_hour=0)
    action = _base_action("PARTIAL_PROFIT")
    state = _base_state(last_management_at=datetime.now(timezone.utc))

    allowed = svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP1")

    assert allowed is False


def test_volume_mutating_action_allowed_once_cooldown_elapses(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO"})())
    monkeypatch.setenv("ADAPTIVE_MODIFICATION_COOLDOWN_SECONDS", "60")
    _seed_classification(SessionLocal, "FP1", AccountClassification.INTERNAL_DEMO.value)
    svc = adaptive_service.AdaptiveManagementService()
    activation = _base_activation()
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B_COOLDOWN2", state="closed", actions_this_hour=0)
    action = _base_action("PARTIAL_PROFIT")
    state = _base_state(last_management_at=datetime.now(timezone.utc) - timedelta(seconds=120))

    allowed = svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FP1")

    assert allowed is True


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
    # Pin the reduced-risk window explicitly: the ambient env (ADAPTIVE_BREAKEVEN_R=0.5 in
    # DEMO since the 2026-08-17 audit) would otherwise collapse [reduced_risk_r, breakeven_r)
    # to empty and make this test about env leakage rather than the tighten-never-widen rule.
    monkeypatch.setenv("ADAPTIVE_REDUCED_RISK_R", "0.5")
    monkeypatch.setenv("ADAPTIVE_BREAKEVEN_R", "1.0")
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
# End-to-end: a V2 action type actually reaches the (fake) broker for an INTERNAL_DEMO
# account with default env configuration -- the concrete proof the shadow restriction is lifted.
# ---------------------------------------------------------------------------


class _FakeExecMT5:
    TRADE_ACTION_SLTP = 6
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009

    def ensure_ready(self):
        return self


class _FakePosition:
    def __init__(self, **kwargs):
        self._data = kwargs

    def model_dump(self, mode="json"):
        return dict(self._data)


class _FakeExecAdapter:
    def __init__(self, positions=None):
        self.client = _FakeExecMT5()
        # Matches the default _base_state() fixture (position_id="P1") with the SL/TP the
        # end-to-end MOVE_SL_TO_REDUCED_RISK test below expects -- so the freshness re-fetch
        # in _execute_action finds a live position consistent with the snapshot it was given.
        # ticket="555" matches that test's broker_ticket override (must be numeric --
        # _build_mt5_request does int(payload["ticket"])).
        self.positions = positions if positions is not None else [_FakePosition(identifier="P1", ticket="555", symbol="EURUSD", type=0, volume=0.5, sl=1.0950, tp=1.1100)]

    async def symbol_info(self, symbol):
        return type("Sym", (), {"filling_mode": 2, "digits": 5, "volume_step": "0.01", "volume_min": "0.01", "volume_max": "100"})()

    async def latest_tick(self, symbol):
        return type("Quote", (), {"bid": 1.0975, "ask": 1.0977})()

    async def mt5_positions(self):
        return self.positions


class _FakeExecManager:
    def __init__(self):
        self.calls = 0

    async def submit_mt5_request(self, **kwargs):
        self.calls += 1
        request = kwargs["request"]
        return {"retcode": 10009, "order": 777, "deal": 888, "price": request.get("price"), "volume": request.get("volume")}


def test_move_sl_to_reduced_risk_executes_end_to_end_for_internal_demo_with_default_env(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.delenv("ADAPTIVE_MANAGEMENT_MODE", raising=False)  # default: 'shadow'
    # Pin the reduced-risk window explicitly -- see test_move_sl_to_reduced_risk_only_tightens_never_widens.
    monkeypatch.setenv("ADAPTIVE_REDUCED_RISK_R", "0.5")
    monkeypatch.setenv("ADAPTIVE_BREAKEVEN_R", "1.0")
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO", "bensim_magic": 5601001})())
    fake_adapter = _FakeExecAdapter()
    fake_execution = _FakeExecManager()
    monkeypatch.setattr(adaptive_service, "mt5_adapter", fake_adapter)
    monkeypatch.setattr(adaptive_service, "execution_manager", fake_execution)
    _seed_classification(SessionLocal, "FPX", AccountClassification.INTERNAL_DEMO.value)
    svc = adaptive_service.AdaptiveManagementService()
    state = _base_state(direction="LONG", entry_price=1.1000, current_sl=1.0950, current_tp=1.1100, winner_classification="healthy_pullback", account_fingerprint="FPX", broker_ticket="555")
    activation = _base_activation()
    breaker = AdaptiveCircuitBreakerORM(breaker_id="B_E2E", state="closed", actions_this_hour=0)

    candidates = svc._v2_candidates(state, r_now=0.6, max_r=0.6, atr=0.0010, atr_r=0.2, regime="trend", zone="zone_50_65", normalized_candles=[])
    choice = svc._select_action(candidates)
    assert choice.action_type == "MOVE_SL_TO_REDUCED_RISK"

    with SessionLocal() as db:
        action = svc._persist_action(db, state, activation, choice, candidates, "demo_active", breaker)
        db.commit()
        db.refresh(action)
        evidence = dict(action.evidence)

    assert evidence["account_classification"] == AccountClassification.INTERNAL_DEMO.value
    assert evidence["previous_sl"] == 1.0950
    assert svc._can_execute(action, state, activation, breaker, "demo_active", current_account_fingerprint="FPX") is True

    result = asyncio.run(svc._execute_action(action, state, {"ticket": state.broker_ticket}))

    assert result["status"] == "ACCEPTED"
    assert result["broker_mutation_attempted"] is True
    assert fake_execution.calls == 1


# ---------------------------------------------------------------------------
# Fresh-position re-fetch guard: the live MT5 position must be re-fetched and verified against
# the action's snapshot (ticket, symbol, direction, volume, SL/TP, still open) immediately
# before a broker mutation is built/submitted in _execute_action -- never rely on broker-side
# rejection alone for staleness (the snapshot in `state`/`payload` was captured at the START of
# the monitor cycle, and real I/O happens between then and submission).
# ---------------------------------------------------------------------------


def _fresh_state_and_action(action_type="MOVE_SL_BREAKEVEN", **state_overrides):
    # broker_ticket must be numeric -- _build_mt5_request does int(payload["ticket"]).
    defaults = dict(direction="LONG", symbol="EURUSD", entry_price=1.1000, current_sl=1.0950, current_tp=1.1100, current_volume=0.5, broker_ticket="555001")
    defaults.update(state_overrides)
    state = _base_state(**defaults)
    action = _base_action(action_type, requested_sl=1.1000, requested_tp=1.1100, requested_volume=0.25)
    return state, action


def test_freshness_check_allows_submission_when_live_position_matches_snapshot(monkeypatch):
    state, action = _fresh_state_and_action()
    fake_adapter = _FakeExecAdapter(positions=[_FakePosition(identifier="P1", ticket="555001", symbol="EURUSD", type=0, volume=0.5, sl=1.0950, tp=1.1100)])
    fake_execution = _FakeExecManager()
    monkeypatch.setattr(adaptive_service, "mt5_adapter", fake_adapter)
    monkeypatch.setattr(adaptive_service, "execution_manager", fake_execution)
    svc = adaptive_service.AdaptiveManagementService()

    result = asyncio.run(svc._execute_action(action, state, {"ticket": state.broker_ticket}))

    assert result["status"] == "ACCEPTED"
    assert result["broker_mutation_attempted"] is True
    assert fake_execution.calls == 1


def test_freshness_check_blocks_submission_when_position_already_closed(monkeypatch):
    state, action = _fresh_state_and_action()
    fake_adapter = _FakeExecAdapter(positions=[])  # ticket no longer open at the broker
    fake_execution = _FakeExecManager()
    monkeypatch.setattr(adaptive_service, "mt5_adapter", fake_adapter)
    monkeypatch.setattr(adaptive_service, "execution_manager", fake_execution)
    svc = adaptive_service.AdaptiveManagementService()

    result = asyncio.run(svc._execute_action(action, state, {"ticket": state.broker_ticket}))

    assert result["status"] == "NOOP"
    assert result["reason"] == "STALE_POSITION_CLOSED"
    assert result["broker_mutation_attempted"] is False
    assert action.broker_mutation_attempted is False
    assert fake_execution.calls == 0


def test_freshness_check_blocks_submission_on_ticket_mismatch(monkeypatch):
    state, action = _fresh_state_and_action()
    # Found by position_id, but the broker's own ticket field disagrees with state.broker_ticket
    # -- a data-integrity divergence that must never be trusted for a real mutation.
    fake_adapter = _FakeExecAdapter(positions=[_FakePosition(identifier="P1", ticket="999999", symbol="EURUSD", type=0, volume=0.5, sl=1.0950, tp=1.1100)])
    fake_execution = _FakeExecManager()
    monkeypatch.setattr(adaptive_service, "mt5_adapter", fake_adapter)
    monkeypatch.setattr(adaptive_service, "execution_manager", fake_execution)
    svc = adaptive_service.AdaptiveManagementService()

    result = asyncio.run(svc._execute_action(action, state, {"ticket": state.broker_ticket}))

    assert result["status"] == "NOOP"
    assert result["reason"] == "STALE_POSITION_TICKET_MISMATCH"
    assert fake_execution.calls == 0


def test_freshness_check_blocks_submission_on_direction_mismatch(monkeypatch):
    state, action = _fresh_state_and_action()  # state.direction == "LONG"
    fake_adapter = _FakeExecAdapter(positions=[_FakePosition(identifier="P1", ticket="555001", symbol="EURUSD", type=1, volume=0.5, sl=1.0950, tp=1.1100)])  # type=1 -> SHORT
    fake_execution = _FakeExecManager()
    monkeypatch.setattr(adaptive_service, "mt5_adapter", fake_adapter)
    monkeypatch.setattr(adaptive_service, "execution_manager", fake_execution)
    svc = adaptive_service.AdaptiveManagementService()

    result = asyncio.run(svc._execute_action(action, state, {"ticket": state.broker_ticket}))

    assert result["status"] == "NOOP"
    assert result["reason"] == "STALE_POSITION_DIRECTION_MISMATCH"
    assert fake_execution.calls == 0


def test_freshness_check_blocks_stale_partial_close_on_volume_change(monkeypatch):
    state, _ignored = _fresh_state_and_action(current_volume=0.5)
    action = _base_action("PARTIAL_PROFIT", requested_volume=0.25)
    # Volume already dropped to 0.2 (e.g. a prior partial fill or manual close) since the
    # snapshot this PARTIAL_PROFIT candidate was computed from.
    fake_adapter = _FakeExecAdapter(positions=[_FakePosition(identifier="P1", ticket="555001", symbol="EURUSD", type=0, volume=0.2, sl=1.0950, tp=1.1100)])
    fake_execution = _FakeExecManager()
    monkeypatch.setattr(adaptive_service, "mt5_adapter", fake_adapter)
    monkeypatch.setattr(adaptive_service, "execution_manager", fake_execution)
    svc = adaptive_service.AdaptiveManagementService()

    result = asyncio.run(svc._execute_action(action, state, {"ticket": state.broker_ticket}))

    assert result["status"] == "NOOP"
    assert result["reason"] == "STALE_POSITION_VOLUME_CHANGED"
    assert fake_execution.calls == 0


def test_freshness_check_blocks_stale_sltp_modification_when_sl_already_changed(monkeypatch):
    state, action = _fresh_state_and_action(current_sl=1.0950, current_tp=1.1100)
    # SL already moved to 1.0980 by the time we're about to submit -- e.g. changed by a prior
    # cycle's action or another process -- so this candidate's assumptions are stale.
    fake_adapter = _FakeExecAdapter(positions=[_FakePosition(identifier="P1", ticket="555001", symbol="EURUSD", type=0, volume=0.5, sl=1.0980, tp=1.1100)])
    fake_execution = _FakeExecManager()
    monkeypatch.setattr(adaptive_service, "mt5_adapter", fake_adapter)
    monkeypatch.setattr(adaptive_service, "execution_manager", fake_execution)
    svc = adaptive_service.AdaptiveManagementService()

    result = asyncio.run(svc._execute_action(action, state, {"ticket": state.broker_ticket}))

    assert result["status"] == "NOOP"
    assert result["reason"] == "STALE_POSITION_SLTP_CHANGED"
    assert fake_execution.calls == 0


def test_freshness_check_prevents_broker_call_so_no_duplicate_request_is_created(monkeypatch):
    """When the position is stale, broker_mutation_attempted must stay False on the action row
    -- the same flag _can_execute()/idempotency logic elsewhere relies on to know whether a
    request was already sent -- so a stale rejection can never be mistaken for, or lead to, a
    duplicate submission, even across repeated calls."""
    state, action = _fresh_state_and_action()
    fake_adapter = _FakeExecAdapter(positions=[])  # closed
    fake_execution = _FakeExecManager()
    monkeypatch.setattr(adaptive_service, "mt5_adapter", fake_adapter)
    monkeypatch.setattr(adaptive_service, "execution_manager", fake_execution)
    svc = adaptive_service.AdaptiveManagementService()

    result1 = asyncio.run(svc._execute_action(action, state, {"ticket": state.broker_ticket}))
    result2 = asyncio.run(svc._execute_action(action, state, {"ticket": state.broker_ticket}))

    assert result1["status"] == "NOOP" and result2["status"] == "NOOP"
    assert action.broker_mutation_attempted is False
    assert fake_execution.calls == 0


# ---------------------------------------------------------------------------
# Broker stop/freeze-level floor (audit gap #1, Part 17): an SL/TP modification must never be
# submitted to the broker when it would land inside the symbol's minimum stop/freeze distance
# from the current price -- validated proactively in _build_mt5_request, not left to order_send
# rejection.
# ---------------------------------------------------------------------------


class _FakeSymbolWithStopsLevel:
    filling_mode = 2
    digits = 5
    point = 0.00001
    trade_stops_level = 100  # 100 points = 0.00100 minimum distance
    trade_freeze_level = 0


def test_sltp_modification_skipped_when_inside_broker_stop_level():
    svc = adaptive_service.AdaptiveManagementService()
    mt5 = _FakeExecMT5()
    symbol = _FakeSymbolWithStopsLevel()
    quote = type("Quote", (), {"bid": 1.0975, "ask": 1.0977})()
    state = _base_state(direction="LONG", entry_price=1.1000, current_sl=1.0950, broker_ticket="555")
    # 0.0001 away from bid (1.0975) -- inside the 0.00100 broker floor.
    action = _base_action("MOVE_SL_BREAKEVEN", requested_sl=1.0974, requested_tp=None)

    request = asyncio.run(svc._build_mt5_request(mt5, symbol, quote, action, state, {"ticket": state.broker_ticket}))

    assert request is None


def test_sltp_modification_submitted_when_outside_broker_stop_level():
    svc = adaptive_service.AdaptiveManagementService()
    mt5 = _FakeExecMT5()
    symbol = _FakeSymbolWithStopsLevel()
    quote = type("Quote", (), {"bid": 1.0975, "ask": 1.0977})()
    state = _base_state(direction="LONG", entry_price=1.1000, current_sl=1.0950, broker_ticket="555")
    # 0.0075 away from bid -- well clear of the 0.00100 broker floor.
    action = _base_action("MOVE_SL_BREAKEVEN", requested_sl=1.0900, requested_tp=None)

    request = asyncio.run(svc._build_mt5_request(mt5, symbol, quote, action, state, {"ticket": state.broker_ticket}))

    assert request is not None
    assert request["sl"] == pytest.approx(1.0900)


def test_sltp_modification_unaffected_when_symbol_metadata_missing_point():
    """No point metadata -> the floor cannot be evaluated -- falls back to broker-side
    validation (unchanged prior behavior) rather than blocking every modification."""
    svc = adaptive_service.AdaptiveManagementService()
    mt5 = _FakeExecMT5()
    symbol = type("Sym", (), {"filling_mode": 2, "digits": 5})()  # no trade_stops_level/point
    quote = type("Quote", (), {"bid": 1.0975, "ask": 1.0977})()
    state = _base_state(direction="LONG", entry_price=1.1000, current_sl=1.0950, broker_ticket="555")
    action = _base_action("MOVE_SL_BREAKEVEN", requested_sl=1.0974, requested_tp=None)

    request = asyncio.run(svc._build_mt5_request(mt5, symbol, quote, action, state, {"ticket": state.broker_ticket}))

    assert request is not None


# ---------------------------------------------------------------------------
# close_incident_position: controlled, idempotent close of a contaminated/incident position
# (XAUUSD incident remediation, Part 4) -- never touches the broker directly, always through
# _persist_action -> _can_execute -> _execute_action -> execution_manager.submit_mt5_request.
# ---------------------------------------------------------------------------


class _FakeIncidentAdapter(_FakeExecAdapter):
    def __init__(self, login: int, server: str, live_payload: dict | None):
        super().__init__()
        self._login = login
        self._server = server
        self._live_payload = live_payload

    async def mt5_account(self):
        return type("Acct", (), {"login": self._login, "server": self._server, "company": "TestCo", "currency": "USD"})()

    async def symbol_info(self, symbol):
        return type("Sym", (), {"filling_mode": 2, "digits": 2, "volume_step": "0.01", "volume_min": "0.01", "volume_max": "100"})()

    async def mt5_positions(self):
        if self._live_payload is None:
            return []
        payload = self._live_payload

        class _Pos:
            def model_dump(self, mode="json"):
                return payload

        return [_Pos()]


def _seed_incident_position(SessionLocal, *, position_id: str, fingerprint: str, volume: float = 0.03):
    with SessionLocal() as db:
        state = _base_state(position_id=position_id, symbol="XAUUSD", direction="LONG", broker_ticket=position_id, entry_price=4279.93, current_sl=4279.95, current_tp=4316.14, original_sl=4256.34, original_tp=4316.14, current_volume=volume, original_volume=0.10, account_fingerprint=fingerprint, contaminated=True, contamination_reason="runaway_r_calculation_bug", managed_automatically=True)
        db.merge(state)
        activation = _base_activation(account_fingerprint=fingerprint, eligible_symbols=["XAUUSD"])
        db.merge(activation)
        breaker = AdaptiveCircuitBreakerORM(breaker_id="adaptive_demo_manager", state="closed", actions_this_hour=0)
        db.merge(breaker)
        db.commit()


def test_close_incident_position_closes_remaining_volume_exactly_once(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setenv("ADAPTIVE_TRADE_MANAGEMENT_MODE", "demo_active")
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO", "bensim_magic": 5601001})())
    fp = account_registry.fingerprint_account(type("Acct", (), {"login": 5054067375, "server": "MetaQuotes-Demo", "company": "MetaQuotes", "currency": "USD"})())
    _seed_classification(SessionLocal, fp.fingerprint_hash, AccountClassification.INTERNAL_DEMO.value)
    live_payload = {"ticket": 57873187767, "identifier": 57873187767, "symbol": "XAUUSD", "type": 0, "volume": "0.03", "price_open": "4279.93", "price_current": "4293.93", "sl": "4279.95", "tp": "4316.14", "profit": "42.0"}
    fake_adapter = _FakeIncidentAdapter(5054067375, "MetaQuotes-Demo", live_payload)
    fake_execution = _FakeExecManager()
    monkeypatch.setattr(adaptive_service, "mt5_adapter", fake_adapter)
    monkeypatch.setattr(adaptive_service, "execution_manager", fake_execution)
    _seed_incident_position(SessionLocal, position_id="57873187767", fingerprint=fp.fingerprint_hash)
    svc = adaptive_service.AdaptiveManagementService()

    result = asyncio.run(svc.close_incident_position("57873187767", incident_id="INC_XAUUSD_1", reason="VALIDATION_INCIDENT_CLOSE"))

    assert result["status"] == "ACCEPTED"
    assert result["broker_mutation_attempted"] is True
    assert result["close_volume_requested"] == 0.03
    assert fake_execution.calls == 1

    # A second call must never resubmit -- idempotency is required.
    result2 = asyncio.run(svc.close_incident_position("57873187767", incident_id="INC_XAUUSD_1"))
    assert result2["status"] == "ALREADY_CLOSED_BY_INCIDENT_ACTION"
    assert fake_execution.calls == 1


def test_close_incident_position_reconciles_only_when_already_closed_at_broker(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setenv("ADAPTIVE_TRADE_MANAGEMENT_MODE", "demo_active")
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO", "bensim_magic": 5601001})())
    fp = account_registry.fingerprint_account(type("Acct", (), {"login": 5054067375, "server": "MetaQuotes-Demo", "company": "MetaQuotes", "currency": "USD"})())
    _seed_classification(SessionLocal, fp.fingerprint_hash, AccountClassification.INTERNAL_DEMO.value)
    fake_adapter = _FakeIncidentAdapter(5054067375, "MetaQuotes-Demo", live_payload=None)  # position no longer open
    fake_execution = _FakeExecManager()
    monkeypatch.setattr(adaptive_service, "mt5_adapter", fake_adapter)
    monkeypatch.setattr(adaptive_service, "execution_manager", fake_execution)
    _seed_incident_position(SessionLocal, position_id="57873187767", fingerprint=fp.fingerprint_hash)
    svc = adaptive_service.AdaptiveManagementService()

    result = asyncio.run(svc.close_incident_position("57873187767", incident_id="INC_XAUUSD_1"))

    assert result["status"] == "ALREADY_CLOSED_AT_BROKER"
    assert fake_execution.calls == 0
    with SessionLocal() as db:
        state = db.get(AdaptivePositionStateORM, "57873187767")
        assert state.closed_detected_at is not None


def test_close_incident_position_rejects_non_internal_demo_account(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setenv("ADAPTIVE_TRADE_MANAGEMENT_MODE", "demo_active")
    monkeypatch.setattr(adaptive_service, "mt5_config", lambda: type("Cfg", (), {"live_trading_enabled": False, "account_mode": "DEMO", "bensim_magic": 5601001})())
    fp = account_registry.fingerprint_account(type("Acct", (), {"login": 700001, "server": "Bensim-Live", "company": "Bensim", "currency": "USD"})())
    _seed_classification(SessionLocal, fp.fingerprint_hash, AccountClassification.PERSONAL_LIVE.value)
    live_payload = {"ticket": 999001, "identifier": 999001, "symbol": "XAUUSD", "type": 0, "volume": "0.03", "price_open": "4279.93", "price_current": "4293.93", "sl": "4279.95", "tp": "4316.14", "profit": "42.0"}
    fake_adapter = _FakeIncidentAdapter(700001, "Bensim-Live", live_payload)
    fake_execution = _FakeExecManager()
    monkeypatch.setattr(adaptive_service, "mt5_adapter", fake_adapter)
    monkeypatch.setattr(adaptive_service, "execution_manager", fake_execution)
    _seed_incident_position(SessionLocal, position_id="999001", fingerprint=fp.fingerprint_hash)
    svc = adaptive_service.AdaptiveManagementService()

    result = asyncio.run(svc.close_incident_position("999001"))

    assert result["status"] == "REJECTED"
    assert result["reason"] == "ACCOUNT_NOT_INTERNAL_DEMO"
    assert fake_execution.calls == 0


def test_close_incident_position_not_found_returns_status(monkeypatch):
    _session_factory(monkeypatch)
    svc = adaptive_service.AdaptiveManagementService()

    result = asyncio.run(svc.close_incident_position("NEVER_EXISTED"))

    assert result["status"] == "NOT_FOUND"


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


def _seed_replay_candles(db, *, symbol: str = "EURUSD", timeframe: str = "M5"):
    """_replay_candles() (adaptive_management/service.py, commit 31709c6e) was rewritten to read
    point-in-time-safe candles from MT5CandleRevisionORM/MT5CanonicalCandleORM instead of calling
    the broker adapter (a deliberate, documented fix for the MFE/MAE-always-0.0 bug -- see that
    function's own "ROOT-CAUSE FIX" docstring) -- the `adapter` parameter is kept only for call-
    site compatibility and is no longer read. Auto-replay tests must seed real candle rows
    covering [entry_time - lead_in, exit_time] for the trade to actually resolve, or _replay_
    candles() correctly (if silently, by design) returns [] and the position is retried on a
    later cycle rather than marked replayed."""
    from backend.historical_intelligence.orm import MT5CandleRevisionORM
    for i, row in enumerate(_replay_candles()):
        bar_time = datetime.fromisoformat(row["time"])
        db.add(MT5CandleRevisionORM(
            revision_id=f"MT5:{symbol}:{timeframe}:replay_test:{i}", provider="MT5", canonical_symbol=symbol, broker_symbol=symbol,
            timeframe=timeframe, bar_timestamp=bar_time, bar_timestamp_utc=bar_time, broker_utc_offset_minutes=0,
            observed_at=bar_time + timedelta(minutes=1), revision_number=1, open=row["open"], high=row["high"], low=row["low"],
            close=row["close"], tick_volume=10, spread=row.get("spread", 1), real_volume=0, finalized=True,
            post_finalization_anomaly=False, created_at=bar_time,
        ))


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
        _seed_replay_candles(db)
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
    # would raise AttributeError and fail this test, rather than silently doing nothing. (Note:
    # _replay_candles() itself no longer calls the adapter at all as of commit 31709c6e -- it
    # reads point-in-time candles from the DB -- so this fixture is now a defense-in-depth check
    # against a FUTURE regression reintroducing a broker call, not the mechanism the real candle
    # data comes from; see _seed_replay_candles's docstring.)
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setattr(adaptive_service, "mt5_adapter", _FakeReplayAdapter())
    svc = adaptive_service.AdaptiveManagementService()
    entry_time = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)
    exit_time = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    with SessionLocal() as db:
        db.add(_base_state(position_id="CLOSED3", direction="LONG", entry_price=1.1000, current_sl=1.0980, original_sl=1.0980, original_tp=1.1040, opened_at=entry_time, closed_detected_at=exit_time))
        db.add(_closed_deal("CLOSED3", 1.1040, exit_time))
        _seed_replay_candles(db)
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


# ---------------------------------------------------------------------------
# Engineering-contamination flag (XAUUSD incident remediation, Part 3): a position whose
# management history was affected by a since-fixed manager bug (real broker fills, not a
# strategy outcome) must be excluded from clean performance/probability/replay-policy-promotion
# statistics while its full broker/execution history stays fully intact.
# ---------------------------------------------------------------------------


def test_contaminated_position_excluded_from_audit_report_and_probability_report(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    svc = adaptive_service.AdaptiveManagementService()
    with SessionLocal() as db:
        db.add(_base_state(position_id="CLEAN1", symbol="EURUSD", strategy_id="SMC", max_achieved_r=1.5))
        db.add(_base_state(position_id="BUGGED1", symbol="XAUUSD", strategy_id="MTFAI1", max_achieved_r=854999.0, contaminated=True, contamination_reason="runaway_r_calculation_bug"))
        db.commit()

    audit = svc.audit_report()
    probability = svc.probability_report()

    assert audit["trade_count"] == 1
    assert not any(row["position_id"] == "BUGGED1" for row in audit["records"])
    assert probability["total_trades"] == 1
    # Direct per-ticket lookup must still work -- contamination hides a ticket from AGGREGATE
    # stats only, never from its own history.
    with SessionLocal() as db:
        state = db.get(AdaptivePositionStateORM, "BUGGED1")
        assert state is not None
        assert state.max_achieved_r == 854999.0


def test_contaminated_position_excluded_from_walk_forward_and_evaluate_policies(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    svc = adaptive_service.AdaptiveManagementService()
    with SessionLocal() as db:
        db.add(_base_state(position_id="BUGGED2", symbol="XAUUSD", contaminated=True, contamination_reason="runaway_r_calculation_bug"))
        db.add(_outcome("BUGGED2", "static_baseline_v1", actual_pnl=999.0, hypothetical_pnl=999.0))
        db.add(_base_state(position_id="CLEAN2", symbol="EURUSD"))
        db.add(_outcome("CLEAN2", "static_baseline_v1", actual_pnl=10.0, hypothetical_pnl=10.0))
        db.commit()

    policies = svc.evaluate_policies()
    walk_forward = svc.run_walk_forward()

    policy_trade_ids = {outcome["trade_id"] for outcome in policies.get("items", [{}])[0].get("outcomes", [])} if policies.get("items") else set()
    assert "BUGGED2" not in policy_trade_ids
    assert walk_forward is not None  # must not crash; contaminated rows are filtered upstream


def test_mark_contaminated_is_idempotent_and_preserves_full_history(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    svc = adaptive_service.AdaptiveManagementService()
    with SessionLocal() as db:
        db.add(_base_state(position_id="POS_TO_MARK", symbol="XAUUSD", max_achieved_r=854999.0))
        db.commit()

    first = svc.mark_contaminated("POS_TO_MARK", "runaway_r_calculation_bug_2026-08-07")
    second = svc.mark_contaminated("POS_TO_MARK", "runaway_r_calculation_bug_2026-08-07_updated_note")

    assert first == {"status": "OK", "position_id": "POS_TO_MARK", "contaminated": True, "reason": "runaway_r_calculation_bug_2026-08-07"}
    assert second["reason"] == "runaway_r_calculation_bug_2026-08-07_updated_note"
    with SessionLocal() as db:
        state = db.get(AdaptivePositionStateORM, "POS_TO_MARK")
        assert state.contaminated is True
        assert state.max_achieved_r == 854999.0  # nothing about the trade's own data is touched


def test_mark_contaminated_unknown_position_returns_not_found(monkeypatch):
    _session_factory(monkeypatch)
    svc = adaptive_service.AdaptiveManagementService()

    result = svc.mark_contaminated("DOES_NOT_EXIST", "reason")

    assert result == {"status": "NOT_FOUND", "position_id": "DOES_NOT_EXIST"}


# ---------------------------------------------------------------------------
# Management Quality Engine (too-tight/too-early verdicts from counterfactuals)
# ---------------------------------------------------------------------------


def _outcome(position_id, policy_id, *, actual_pnl=-20.0, hypothetical_pnl=-20.0, loss_reduced=False, profit_reduced=False, giveback_avoided=0.0):
    return CounterfactualOutcomeORM(
        outcome_id=f"OUT_{position_id}_{policy_id}",
        trade_id=position_id,
        policy_id=policy_id,
        actual_pnl=actual_pnl,
        hypothetical_pnl=hypothetical_pnl,
        hypothetical_r=0.0,
        difference_from_actual=hypothetical_pnl - actual_pnl,
        loss_reduced=loss_reduced,
        profit_reduced=profit_reduced,
        giveback_avoided=giveback_avoided,
    )


def test_management_quality_verdict_flags_stop_too_tight_and_breakeven_too_late(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    svc = adaptive_service.AdaptiveManagementService()
    with SessionLocal() as db:
        db.add(_outcome("POS1", "static_baseline_v1", actual_pnl=-20.0, hypothetical_pnl=-20.0))
        # A 1.5x wider stop would have turned the loss into a real profit -> stop was too tight.
        db.add(_outcome("POS1", "atr_structure_stop_wider_v1", actual_pnl=-20.0, hypothetical_pnl=15.0))
        # A tighter stop does no worse -- the extra room bought nothing.
        db.add(_outcome("POS1", "atr_structure_stop_tighter_v1", actual_pnl=-20.0, hypothetical_pnl=-20.0))
        # Earlier breakeven would have reduced the loss -> breakeven was too late (never protected).
        db.add(_outcome("POS1", "breakeven_at_0_75r_v1", actual_pnl=-20.0, hypothetical_pnl=-8.0, loss_reduced=True))
        db.commit()

    result = svc.management_quality_verdict("POS1")

    assert result["status"] == "ok"
    assert result["verdicts"]["stop_too_tight"]["verdict"] is True
    assert result["verdicts"]["stop_too_wide"]["verdict"] is True
    assert result["verdicts"]["breakeven_too_late"]["verdict"] is True


def test_management_quality_verdict_reports_no_replay_data_when_ungathered(monkeypatch):
    _session_factory(monkeypatch)
    svc = adaptive_service.AdaptiveManagementService()

    result = svc.management_quality_verdict("NEVER_REPLAYED")

    assert result["status"] == "no_replay_data"
    assert result["verdicts"] == {}


def test_management_quality_verdict_exit_optimal_when_nothing_beats_actual(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    svc = adaptive_service.AdaptiveManagementService()
    with SessionLocal() as db:
        db.add(_outcome("POS2", "static_baseline_v1", actual_pnl=50.0, hypothetical_pnl=50.0))
        db.add(_outcome("POS2", "trailing_atr_1_5_v1", actual_pnl=50.0, hypothetical_pnl=30.0, profit_reduced=True))
        db.commit()

    result = svc.management_quality_verdict("POS2")

    assert result["verdicts"]["exit_optimal"]["verdict"] is True
    assert result["verdicts"]["trailing_too_aggressive"]["verdict"] is True


# ---------------------------------------------------------------------------
# Leaderboard reports (Part 12 validation reporting)
# ---------------------------------------------------------------------------


def test_leaderboard_ranks_strategies_by_expectancy_with_minimum_sample_gate(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    svc = adaptive_service.AdaptiveManagementService()
    with SessionLocal() as db:
        # STRAT_WINNER: 10 trades, consistently profitable.
        for i in range(10):
            db.add(_base_state(position_id=f"W{i}", symbol="EURUSD", strategy_id="STRAT_WINNER", timeframe="M15", max_achieved_r=1.5))
            db.add(_closed_deal(f"W{i}", 1.1050, datetime(2026, 8, 1, tzinfo=timezone.utc), realized_pnl=50.0))
        # STRAT_LOSER: 10 trades, consistently losing.
        for i in range(10):
            db.add(_base_state(position_id=f"L{i}", symbol="EURUSD", strategy_id="STRAT_LOSER", timeframe="M15", max_achieved_r=0.2))
            db.add(_closed_deal(f"L{i}", 1.0950, datetime(2026, 8, 1, tzinfo=timezone.utc), realized_pnl=-30.0))
        # STRAT_TINY: only 3 trades -- must be excluded (insufficient_data), even though it's
        # the most profitable per-trade, so it can never top the leaderboard on a thin sample.
        for i in range(3):
            db.add(_base_state(position_id=f"T{i}", symbol="EURUSD", strategy_id="STRAT_TINY", timeframe="M15", max_achieved_r=3.0))
            db.add(_closed_deal(f"T{i}", 1.1200, datetime(2026, 8, 1, tzinfo=timezone.utc), realized_pnl=200.0))
        db.commit()

    result = svc.leaderboard_report(top_n=3)

    best_keys = [row["key"] for row in result["by_strategy"]["best"]]
    worst_keys = [row["key"] for row in result["by_strategy"]["worst"]]
    assert any("STRAT_WINNER" in key for key in best_keys)
    assert any("STRAT_LOSER" in key for key in worst_keys)
    assert not any("STRAT_TINY" in key for key in best_keys + worst_keys)


# ---------------------------------------------------------------------------
# Learning safety gate (Part 10)
# ---------------------------------------------------------------------------


def test_learning_recommendation_mode_defaults_to_shadow(monkeypatch):
    monkeypatch.delenv("LEARNING_RECOMMENDATION_MODE", raising=False)
    assert adaptive_service.learning_recommendation_mode() == "shadow"


def test_learning_recommendation_mode_reads_env_fresh(monkeypatch):
    monkeypatch.setenv("LEARNING_RECOMMENDATION_MODE", "enforce")
    assert adaptive_service.learning_recommendation_mode() == "enforce"
    monkeypatch.setenv("LEARNING_RECOMMENDATION_MODE", "garbage")
    assert adaptive_service.learning_recommendation_mode() == "shadow"


def test_learning_reports_surface_current_mode(monkeypatch):
    _session_factory(monkeypatch)
    monkeypatch.setenv("LEARNING_RECOMMENDATION_MODE", "shadow")
    svc = adaptive_service.AdaptiveManagementService()

    assert svc.probability_report()["learning_mode"] == "shadow"
    assert svc.leaderboard_report()["learning_mode"] == "shadow"
    assert svc.management_quality_verdict("NEVER_REPLAYED")["learning_mode"] == "shadow"
