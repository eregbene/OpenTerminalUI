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


def active_profile(config: MT5Config) -> PropRiskProfile:
    return PROFILES.get(config.prop_profile, PROFILES["GENERIC_PROP_CONSERVATIVE"])


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
