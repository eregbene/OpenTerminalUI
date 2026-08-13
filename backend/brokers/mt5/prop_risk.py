from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from backend.brokers.mt5.config import MT5Config


@dataclass(frozen=True)
class PropRiskProfile:
    name: str
    version: str
    starting_balance: Decimal
    profit_target_percent: Decimal
    maximum_daily_loss_percent: Decimal
    maximum_total_loss_percent: Decimal
    drawdown_type: str
    daily_reset_timezone: str
    minimum_trading_days: int
    minimum_profitable_days: int
    consistency_rules: dict[str, Any]
    news_restrictions: dict[str, Any]
    overnight_restrictions: dict[str, Any]
    weekend_restrictions: dict[str, Any]
    maximum_inactivity_days: int | None
    automation_permission_status: str
    verification_date: str
    source_reference: str

    def model_dump(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "starting_balance": str(self.starting_balance),
            "profit_target_percent": str(self.profit_target_percent),
            "maximum_daily_loss_percent": str(self.maximum_daily_loss_percent),
            "maximum_total_loss_percent": str(self.maximum_total_loss_percent),
            "drawdown_type": self.drawdown_type,
            "daily_reset_timezone": self.daily_reset_timezone,
            "minimum_trading_days": self.minimum_trading_days,
            "minimum_profitable_days": self.minimum_profitable_days,
            "consistency_rules": self.consistency_rules,
            "news_restrictions": self.news_restrictions,
            "overnight_restrictions": self.overnight_restrictions,
            "weekend_restrictions": self.weekend_restrictions,
            "maximum_inactivity_days": self.maximum_inactivity_days,
            "automation_permission_status": self.automation_permission_status,
            "verification_date": self.verification_date,
            "source_reference": self.source_reference,
        }


PROFILES = {
    "GENERIC_PROP_CONSERVATIVE": PropRiskProfile("GENERIC_PROP_CONSERVATIVE", "2026-08-03.v1", Decimal("100000"), Decimal("8"), Decimal("5"), Decimal("10"), "STATIC", "Europe/Bucharest", 5, 3, {}, {"restricted": False}, {"allowed": True}, {"allowed": False}, 30, "CONFIGURABLE", "2026-08-03", "placeholder: configurable profile"),
    "FTMO_2_STEP": PropRiskProfile("FTMO_2_STEP", "2026-08-03.v1", Decimal("100000"), Decimal("10"), Decimal("5"), Decimal("10"), "STATIC", "Europe/Prague", 4, 0, {}, {"restricted": True}, {"allowed": True}, {"allowed": False}, 30, "CHECK_CURRENT_RULES", "2026-08-03", "placeholder: verify current FTMO rules before use"),
    "FTMO_1_STEP": PropRiskProfile("FTMO_1_STEP", "2026-08-03.v1", Decimal("100000"), Decimal("10"), Decimal("3"), Decimal("6"), "STATIC", "Europe/Prague", 4, 0, {}, {"restricted": True}, {"allowed": True}, {"allowed": False}, 30, "CHECK_CURRENT_RULES", "2026-08-03", "placeholder: verify current FTMO rules before use"),
    "FUNDEDNEXT_STELLAR_2_STEP": PropRiskProfile("FUNDEDNEXT_STELLAR_2_STEP", "2026-08-03.v1", Decimal("100000"), Decimal("8"), Decimal("5"), Decimal("10"), "STATIC_OR_TRAILING_CONFIGURABLE", "UTC", 5, 0, {}, {"restricted": True}, {"allowed": True}, {"allowed": False}, 30, "CHECK_CURRENT_RULES", "2026-08-03", "placeholder: verify current FundedNext rules before use"),
    "THE5ERS_HIGH_STAKES": PropRiskProfile("THE5ERS_HIGH_STAKES", "2026-08-03.v1", Decimal("100000"), Decimal("8"), Decimal("5"), Decimal("10"), "STATIC_OR_TRAILING_CONFIGURABLE", "UTC", 3, 0, {}, {"restricted": True}, {"allowed": True}, {"allowed": False}, 30, "CHECK_CURRENT_RULES", "2026-08-03", "placeholder: verify current The5ers rules before use"),
    "CUSTOM": PropRiskProfile("CUSTOM", "2026-08-03.v1", Decimal("100000"), Decimal("0"), Decimal("0"), Decimal("0"), "CUSTOM", "UTC", 0, 0, {}, {"restricted": False}, {"allowed": True}, {"allowed": True}, None, "USER_CONFIGURED", "2026-08-03", "placeholder: user supplied custom profile"),
}


CHALLENGE_STATES = {
    "ACTIVE",
    "TARGET_REACHED",
    "MIN_DAYS_PENDING",
    "PASSED",
    "DAILY_LOSS_BREACHED",
    "MAX_LOSS_BREACHED",
    "DISABLED",
}


def active_profile(config: MT5Config) -> PropRiskProfile:
    return PROFILES.get(config.prop_profile, PROFILES["GENERIC_PROP_CONSERVATIVE"])


def ftmo_2step_limits(initial_balance: Decimal, *, profit_target_percent: Decimal = Decimal("10"), daily_loss_percent: Decimal = Decimal("5"), max_loss_percent: Decimal = Decimal("10"), minimum_trading_days: int = 4) -> dict[str, Any]:
    return {
        "initial_balance": str(initial_balance),
        "profit_target_percent": str(profit_target_percent),
        "profit_target": str((initial_balance * profit_target_percent / Decimal("100")).quantize(Decimal("0.01"))),
        "daily_loss_percent": str(daily_loss_percent),
        "daily_loss_limit": str((initial_balance * daily_loss_percent / Decimal("100")).quantize(Decimal("0.01"))),
        "max_loss_percent": str(max_loss_percent),
        "max_loss_limit": str((initial_balance * max_loss_percent / Decimal("100")).quantize(Decimal("0.01"))),
        "minimum_trading_days": minimum_trading_days,
        "trading_period": "UNLIMITED",
    }


def challenge_status(
    *,
    initial_balance: Decimal,
    daily_baseline_equity: Decimal,
    current_balance: Decimal,
    current_equity: Decimal,
    trading_days_completed: int = 0,
    enabled: bool = True,
    profit_target_percent: Decimal = Decimal("10"),
    daily_loss_percent: Decimal = Decimal("5"),
    max_loss_percent: Decimal = Decimal("10"),
    minimum_trading_days: int = 4,
    daily_entry_block_utilization: Decimal = Decimal("0.80"),
    max_loss_entry_block_utilization: Decimal = Decimal("0.80"),
    next_daily_reset: str | None = None,
) -> dict[str, Any]:
    limits = ftmo_2step_limits(
        initial_balance,
        profit_target_percent=profit_target_percent,
        daily_loss_percent=daily_loss_percent,
        max_loss_percent=max_loss_percent,
        minimum_trading_days=minimum_trading_days,
    )
    daily_limit = Decimal(limits["daily_loss_limit"])
    max_limit = Decimal(limits["max_loss_limit"])
    target = Decimal(limits["profit_target"])
    daily_loss_used = max(Decimal("0"), daily_baseline_equity - current_equity).quantize(Decimal("0.01"))
    max_loss_used = max(Decimal("0"), initial_balance - current_equity).quantize(Decimal("0.01"))
    net_profit = (current_balance - initial_balance).quantize(Decimal("0.01"))
    internal_daily_limit = (daily_limit * daily_entry_block_utilization).quantize(Decimal("0.01"))
    internal_max_limit = (max_limit * max_loss_entry_block_utilization).quantize(Decimal("0.01"))
    blockers: list[str] = []
    if not enabled:
        state = "DISABLED"
        blockers.append("ACCOUNT_PROFILE_DISABLED")
    elif daily_loss_used >= daily_limit:
        state = "DAILY_LOSS_BREACHED"
        blockers.append("PROP_DAILY_LOSS_BREACHED")
    elif max_loss_used >= max_limit:
        state = "MAX_LOSS_BREACHED"
        blockers.append("PROP_MAX_LOSS_BREACHED")
    elif net_profit >= target and trading_days_completed >= minimum_trading_days:
        state = "PASSED"
    elif net_profit >= target:
        state = "MIN_DAYS_PENDING"
    else:
        state = "ACTIVE"
    if daily_loss_used >= internal_daily_limit:
        blockers.append("PROP_DAILY_LOSS_BUFFER")
    if max_loss_used >= internal_max_limit:
        blockers.append("PROP_MAX_LOSS_BUFFER")
    return {
        **limits,
        "current_balance": str(current_balance),
        "current_equity": str(current_equity),
        "daily_baseline_equity": str(daily_baseline_equity),
        "daily_loss_used": str(daily_loss_used),
        "daily_loss_remaining": str(max(Decimal("0"), daily_limit - daily_loss_used).quantize(Decimal("0.01"))),
        "daily_loss_percent_used": str(((daily_loss_used / daily_limit) * Decimal("100")).quantize(Decimal("0.01")) if daily_limit else Decimal("0")),
        "max_loss_used": str(max_loss_used),
        "max_loss_remaining": str(max(Decimal("0"), max_limit - max_loss_used).quantize(Decimal("0.01"))),
        "max_loss_percent_used": str(((max_loss_used / max_limit) * Decimal("100")).quantize(Decimal("0.01")) if max_limit else Decimal("0")),
        "official_daily_loss_limit": str(daily_limit),
        "internal_daily_entry_limit": str(internal_daily_limit),
        "official_max_loss_limit": str(max_limit),
        "internal_max_loss_entry_limit": str(internal_max_limit),
        "profit_target_progress": str(((net_profit / target) * Decimal("100")).quantize(Decimal("0.01")) if target else Decimal("0")),
        "trading_days_completed": trading_days_completed,
        "challenge_status": state,
        "new_entries_allowed": not blockers,
        "entry_blockers": sorted(set(blockers)),
        "next_daily_reset": next_daily_reset,
    }


def risk_status(config: MT5Config, *, equity: Decimal, balance: Decimal, daily_pnl: Decimal = Decimal("0"), total_pnl: Decimal = Decimal("0")) -> dict[str, Any]:
    profile = active_profile(config)
    internal_daily_cap = min(balance * Decimal(str(config.internal_max_daily_loss_percent)) / Decimal("100"), Decimal(str(config.max_daily_loss_usd)))
    internal_total_cap = balance * Decimal(str(config.internal_max_total_drawdown_percent)) / Decimal("100")
    prop_daily_cap = balance * profile.maximum_daily_loss_percent / Decimal("100") if profile.maximum_daily_loss_percent else internal_daily_cap
    prop_total_cap = balance * profile.maximum_total_loss_percent / Decimal("100") if profile.maximum_total_loss_percent else internal_total_cap
    daily_cap = min(internal_daily_cap, prop_daily_cap)
    total_cap = min(internal_total_cap, prop_total_cap)
    blockers: list[str] = []
    if daily_pnl <= -daily_cap:
        blockers.append("DAILY_LOSS_LIMIT")
    if total_pnl <= -total_cap:
        blockers.append("TOTAL_DRAWDOWN_LIMIT")
    return {
        "active_prop_profile": profile.model_dump(),
        "internal": {
            "risk_per_trade_percent": str(config.risk_percent_per_trade),
            "daily_loss_percent": str(config.internal_max_daily_loss_percent),
            "total_drawdown_percent": str(config.internal_max_total_drawdown_percent),
            "aggregate_open_risk_percent": str(config.max_total_open_risk_percent),
            "daily_loss_cap_usd": str(internal_daily_cap),
            "total_drawdown_cap_usd": str(internal_total_cap),
        },
        "external": {
            "daily_loss_cap_usd": str(prop_daily_cap),
            "total_drawdown_cap_usd": str(prop_total_cap),
            "automation_permission_status": profile.automation_permission_status,
        },
        "effective": {
            "daily_loss_cap_usd": str(daily_cap),
            "total_drawdown_cap_usd": str(total_cap),
            "stricter_limit_wins": True,
        },
        "blockers": blockers,
        "trading_lock_active": bool(blockers),
    }
