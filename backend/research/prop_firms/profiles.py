from __future__ import annotations

import os
from dataclasses import replace

from backend.research.prop_firms.models import ExecutionScenario, InternalSafetyProfile, PropFirmProfile


def internal_safety_profile() -> InternalSafetyProfile:
    return InternalSafetyProfile(
        risk_per_trade_percent=_float("PROP_INTERNAL_RISK_PER_TRADE_PERCENT", 0.25),
        max_daily_loss_percent=_float("PROP_INTERNAL_MAX_DAILY_LOSS_PERCENT", 1.0),
        max_weekly_loss_percent=_float("PROP_INTERNAL_MAX_WEEKLY_LOSS_PERCENT", 2.0),
        max_total_drawdown_percent=_float("PROP_INTERNAL_MAX_TOTAL_DRAWDOWN_PERCENT", 4.0),
        max_open_positions=_int("PROP_INTERNAL_MAX_OPEN_POSITIONS", 1),
        max_trades_per_day=_int("PROP_INTERNAL_MAX_TRADES_PER_DAY", 2),
        max_consecutive_losses=_int("PROP_INTERNAL_MAX_CONSECUTIVE_LOSSES", 2),
        daily_profit_lock_percent=_float("PROP_INTERNAL_DAILY_PROFIT_LOCK_PERCENT", 1.0),
        min_risk_reward=_float("PROP_INTERNAL_MIN_RISK_REWARD", 1.5),
        max_correlated_exposure_percent=_float("PROP_INTERNAL_MAX_CORRELATED_EXPOSURE_PERCENT", 0.5),
        drawdown_buffer_percent=_float("PROP_INTERNAL_DRAWDOWN_BUFFER_PERCENT", 1.0),
    )


def default_profiles(account_size: float = 100000.0) -> dict[str, PropFirmProfile]:
    base = {
        "version": "research_rules_v1",
        "verified_at": None,
        "source_reference": "PROGRAM_RULES_REQUIRE_EXTERNAL_VERIFICATION",
        "account_size": account_size,
        "account_currency": "USD",
        "daily_loss_calculation_method": "balance_and_equity_from_daily_start",
        "total_loss_calculation_method": "initial_balance_static",
        "static_or_trailing_drawdown": "STATIC",
        "minimum_trading_days": 0,
        "minimum_profitable_days": 0,
        "profitable_day_threshold_percent": 0.0,
        "maximum_trading_days": None,
        "trading_period_unlimited": True,
        "best_day_consistency_percent": None,
        "general_consistency_rule": "PROGRAM_RULES_REQUIRE_EXTERNAL_VERIFICATION",
        "inactivity_days": None,
        "overnight_holding_allowed": True,
        "weekend_holding_allowed": False,
        "news_holding_allowed": True,
        "news_entry_blackout_before_minutes": 0,
        "news_entry_blackout_after_minutes": 0,
        "automated_trading_allowed": "PROGRAM_VERIFICATION_REQUIRED",
        "maximum_leverage": None,
        "allowed_symbols": ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"],
        "prohibited_strategies": [],
        "daily_reset_timezone": "Europe/Prague",
        "phase_transition_rules": {"status": "REQUIRES_EXTERNAL_VERIFICATION"},
        "funded_account_rules": {"status": "REQUIRES_EXTERNAL_VERIFICATION"},
        "payout_consistency_rules": {"status": "REQUIRES_EXTERNAL_VERIFICATION"},
        "notes": "Research default only. Company rules can change and must be verified before use.",
    }
    profiles = [
        _profile(base, profile_id="FTMO_2_STEP_PHASE_1", provider_name="FTMO", program_name="2-Step Challenge", phase="PHASE_1", profit_target_percent=10, maximum_daily_loss_percent=5, maximum_total_loss_percent=10, minimum_trading_days=4, automated_trading_allowed="ALLOWED_IF_LEGITIMATE_AND_REPLICABLE"),
        _profile(base, profile_id="FTMO_2_STEP_PHASE_2", provider_name="FTMO", program_name="2-Step Verification", phase="PHASE_2", profit_target_percent=5, maximum_daily_loss_percent=5, maximum_total_loss_percent=10, minimum_trading_days=4, automated_trading_allowed="ALLOWED_IF_LEGITIMATE_AND_REPLICABLE"),
        _profile(base, profile_id="FTMO_1_STEP", provider_name="FTMO", program_name="1-Step Challenge", phase="ONE_STEP", profit_target_percent=10, maximum_daily_loss_percent=3, maximum_total_loss_percent=10, best_day_consistency_percent=50, automated_trading_allowed="ALLOWED_IF_LEGITIMATE_AND_REPLICABLE"),
        _profile(base, profile_id="FUNDEDNEXT_STELLAR_2_STEP_PHASE_1", provider_name="FundedNext", program_name="Stellar 2-Step", phase="PHASE_1", profit_target_percent=8, maximum_daily_loss_percent=5, maximum_total_loss_percent=10, minimum_trading_days=5),
        _profile(base, profile_id="FUNDEDNEXT_STELLAR_2_STEP_PHASE_2", provider_name="FundedNext", program_name="Stellar 2-Step", phase="PHASE_2", profit_target_percent=5, maximum_daily_loss_percent=5, maximum_total_loss_percent=10, minimum_trading_days=5),
        _profile(base, profile_id="THE5ERS_HIGH_STAKES_PHASE_1", provider_name="The5ers", program_name="High Stakes", phase="PHASE_1", profit_target_percent=10, maximum_daily_loss_percent=5, maximum_total_loss_percent=10, minimum_profitable_days=3, profitable_day_threshold_percent=0.5, inactivity_days=30, daily_reset_timezone="UTC+3", news_entry_blackout_before_minutes=2, news_entry_blackout_after_minutes=2, overnight_holding_allowed=True, weekend_holding_allowed=True),
        _profile(base, profile_id="THE5ERS_HIGH_STAKES_PHASE_2", provider_name="The5ers", program_name="High Stakes", phase="PHASE_2", profit_target_percent=5, maximum_daily_loss_percent=5, maximum_total_loss_percent=10, minimum_profitable_days=3, profitable_day_threshold_percent=0.5, inactivity_days=30, daily_reset_timezone="UTC+3", news_entry_blackout_before_minutes=2, news_entry_blackout_after_minutes=2, overnight_holding_allowed=True, weekend_holding_allowed=True),
    ]
    return {profile.profile_id: profile for profile in profiles}


EXECUTION_SCENARIOS = {
    "NORMAL": ExecutionScenario("NORMAL", spread_pips=1.2, commission_per_lot=7.0, slippage_pips=0.2, stop_slippage_pips=0.4, swap_per_day=1.5, rejected_entry_probability=0.01, partial_fill_probability=0.01, fill_delay_bars=0),
    "STRESSED": ExecutionScenario("STRESSED", spread_pips=2.0, commission_per_lot=9.0, slippage_pips=0.6, stop_slippage_pips=1.2, swap_per_day=2.5, rejected_entry_probability=0.03, partial_fill_probability=0.03, fill_delay_bars=1),
    "SEVERE": ExecutionScenario("SEVERE", spread_pips=3.5, commission_per_lot=12.0, slippage_pips=1.2, stop_slippage_pips=2.5, swap_per_day=4.0, rejected_entry_probability=0.08, partial_fill_probability=0.08, fill_delay_bars=1),
}


def profile_from_payload(payload: dict) -> PropFirmProfile:
    base = default_profiles(float(payload.get("account_size") or 100000.0)).get(str(payload.get("profile_id") or ""))
    if base:
        data = base.model_dump()
        data.update(payload)
        return PropFirmProfile(**data)
    return PropFirmProfile(**payload)


def _profile(base: dict, **overrides) -> PropFirmProfile:
    data = dict(base)
    data.update(overrides)
    return PropFirmProfile(**data)


def with_account_size(profile: PropFirmProfile, account_size: float) -> PropFirmProfile:
    return replace(profile, account_size=account_size)


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default
