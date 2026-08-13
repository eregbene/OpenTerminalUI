"""Per-account daily-loss baseline persistence and entry-protection evaluation.

Fixes two confirmed bugs from the multi-account risk/protection audit:

Bug 3 (daily-loss baseline / reset): backend/api/routes/brokers.py previously passed
`daily_baseline_equity=profile.expected_initial_balance` (the account's original starting
capital) into `prop_risk.challenge_status()`, so "today's" daily-loss utilization was actually
measured against lifetime state, never resetting. This module persists a write-once
(account_id, trading_day) baseline row (MT5PropDailyStateORM) captured the first time each
account is observed on a given trading day, keyed by that account's own configured
PropRiskProfile.daily_reset_timezone -- not server-local midnight, not UTC -- and surviving
backend/Docker/bridge restarts because it is a normal DB row, not in-memory state.

Bug 4 (entry blocker using zero P&L): the live autonomous entry gate (backend/brokers/mt5/
autonomous.py::_global_blockers) called `prop_risk.risk_status()` without real daily_pnl/
total_pnl, so its blockers could never fire. `evaluate_entry_protection()` below is the single
call site both the live entry gate and the read-only /prop-status route now use; it feeds
`challenge_status()` a real persisted daily baseline and produces the exact reason codes the
audit required (PROP_DAILY_LOSS_ENTRY_BLOCK, PROP_MAX_LOSS_ENTRY_BLOCK,
PROP_INTERNAL_DAILY_BUFFER_BLOCK, PROP_INTERNAL_MAX_LOSS_BUFFER_BLOCK) rather than reinventing
challenge_status()'s already-correct percentage-based formula.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.brokers.mt5 import account_registry
from backend.brokers.mt5.config import MT5Config
from backend.brokers.mt5.orm import MT5PropDailyStateORM, MT5TradeRecordORM
from backend.brokers.mt5.prop_risk import active_profile, challenge_status

# Maps challenge_status()'s existing entry_blockers vocabulary onto the exact reason codes the
# audit requires the live entry gate to surface. challenge_status()'s own formula/state machine
# is unchanged -- only these display/decision labels are added on top.
_ENTRY_BLOCK_REASON_MAP = {
    "PROP_DAILY_LOSS_BREACHED": "PROP_DAILY_LOSS_ENTRY_BLOCK",
    "PROP_MAX_LOSS_BREACHED": "PROP_MAX_LOSS_ENTRY_BLOCK",
    "PROP_DAILY_LOSS_BUFFER": "PROP_INTERNAL_DAILY_BUFFER_BLOCK",
    "PROP_MAX_LOSS_BUFFER": "PROP_INTERNAL_MAX_LOSS_BUFFER_BLOCK",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def trading_day_for(config: MT5Config, *, now: datetime | None = None) -> tuple[str, str]:
    """Returns (trading_day, reset_timezone) at instant `now` (defaults to utcnow()), using this
    account's configured PropRiskProfile.daily_reset_timezone."""
    tz_name = active_profile(config).daily_reset_timezone
    moment = now or utcnow()
    local = moment.astimezone(ZoneInfo(tz_name))
    return local.date().isoformat(), tz_name


def _day_boundaries_utc(trading_day: str, tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    start_local = datetime.fromisoformat(trading_day).replace(tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def get_or_create_daily_state(
    db: Session,
    account_id: str,
    config: MT5Config,
    *,
    balance: Decimal,
    equity: Decimal,
    now: datetime | None = None,
) -> MT5PropDailyStateORM:
    """Write-once daily baseline. A mid-day call always returns the FIRST observation of the
    day for this account -- never overwritten -- so the baseline is stable regardless of how
    many times this is called, and survives backend/Docker/bridge restarts because it is
    persisted, not held in memory."""
    moment = now or utcnow()
    trading_day, tz_name = trading_day_for(config, now=moment)
    existing = db.get(MT5PropDailyStateORM, (account_id, trading_day))
    if existing is not None:
        return existing
    row = MT5PropDailyStateORM(
        account_id=account_id,
        trading_day=trading_day,
        reset_timezone=tz_name,
        day_start_balance=float(balance),
        day_start_equity=float(equity),
        captured_at=moment,
        created_at=moment,
    )
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        # Lost a race against a concurrent first-observation for the same account/day -- the
        # other writer's row is the real baseline.
        db.rollback()
        existing = db.get(MT5PropDailyStateORM, (account_id, trading_day))
        if existing is not None:
            return existing
        raise
    db.refresh(row)
    return row


def realized_costs_today(db: Session, account_id: str, trading_day: str, tz_name: str) -> dict[str, Decimal]:
    """Informational breakdown only (Part 3 logging requirement) -- NOT an input to the
    daily-loss blocking decision, which is driven purely by day_start_equity vs current live
    equity, matching challenge_status()'s own already-correct formula."""
    start_utc, end_utc = _day_boundaries_utc(trading_day, tz_name)
    rows = (
        db.query(MT5TradeRecordORM)
        .filter(
            MT5TradeRecordORM.account_id == account_id,
            MT5TradeRecordORM.close_timestamp >= start_utc,
            MT5TradeRecordORM.close_timestamp < end_utc,
        )
        .all()
    )
    totals = {
        "realized_pnl": Decimal("0"),
        "gross_pnl": Decimal("0"),
        "commission": Decimal("0"),
        "swap": Decimal("0"),
        "fee": Decimal("0"),
    }
    for row in rows:
        totals["realized_pnl"] += Decimal(str(row.realized_pnl or 0))
        totals["gross_pnl"] += Decimal(str(row.gross_pnl or 0))
        totals["commission"] += Decimal(str(row.commission or 0))
        totals["swap"] += Decimal(str(row.swap or 0))
        totals["fee"] += Decimal(str(row.fee or 0))
    totals["trades_closed_today"] = Decimal(len(rows))
    return totals


def daily_state_snapshot(
    db: Session,
    account_id: str,
    config: MT5Config,
    *,
    balance: Decimal,
    equity: Decimal,
    now: datetime | None = None,
) -> dict:
    """Full per-account daily-loss state: the persisted write-once baseline, the live
    mark-to-market delta from it (the actual blocking input), and an informational realized/
    floating/cost breakdown for logging."""
    moment = now or utcnow()
    state = get_or_create_daily_state(db, account_id, config, balance=balance, equity=equity, now=moment)
    costs_today = realized_costs_today(db, account_id, state.trading_day, state.reset_timezone)
    day_start_equity = Decimal(str(state.day_start_equity))
    return {
        "account_id": account_id,
        "trading_day": state.trading_day,
        "reset_timezone": state.reset_timezone,
        "day_start_balance": Decimal(str(state.day_start_balance)),
        "day_start_equity": day_start_equity,
        "current_balance": balance,
        "current_equity": equity,
        "realized_pnl_today": costs_today["realized_pnl"],
        "gross_pnl_today": costs_today["gross_pnl"],
        "commission_today": costs_today["commission"],
        "swap_today": costs_today["swap"],
        "fee_today": costs_today["fee"],
        "trades_closed_today": int(costs_today["trades_closed_today"]),
        "floating_pnl": equity - balance,
        "daily_pnl_total": equity - day_start_equity,
    }


def remaining_safety_budget_usd(protection: dict) -> Decimal:
    """Tightest remaining dollar cushion across this account's official daily/max-loss prop
    limits and the internal 80%-utilization safety buffers -- the account-scaled replacement
    for the old flat `config.max_daily_loss_usd`/`max_total_open_risk_usd` constants that
    previously bound every account's per-trade sizing to the same absolute dollar figure
    regardless of equity (Bug 2). Feeds execution.py::calculate_risk_size's
    `prop_remaining_budget_usd` parameter."""
    status = protection["challenge_status"]
    candidates = [
        Decimal(status["daily_loss_remaining"]),
        Decimal(status["max_loss_remaining"]),
        max(Decimal("0"), Decimal(status["internal_daily_entry_limit"]) - Decimal(status["daily_loss_used"])),
        max(Decimal("0"), Decimal(status["internal_max_loss_entry_limit"]) - Decimal(status["max_loss_used"])),
    ]
    return min(candidates)


def evaluate_entry_protection(
    db: Session,
    account_id: str,
    config: MT5Config,
    *,
    balance: Decimal,
    equity: Decimal,
    now: datetime | None = None,
) -> dict:
    """Single authoritative source for prop daily/max-loss entry blocking. Feeds
    challenge_status() a real persisted daily baseline instead of the account's lifetime
    starting capital, and translates its entry_blockers into the exact reason codes the audit
    requires: PROP_DAILY_LOSS_ENTRY_BLOCK, PROP_MAX_LOSS_ENTRY_BLOCK,
    PROP_INTERNAL_DAILY_BUFFER_BLOCK, PROP_INTERNAL_MAX_LOSS_BUFFER_BLOCK. Used by both the live
    autonomous entry gate and the read-only /mt5/accounts/{id}/prop-status route so they can
    never disagree."""
    profile = account_registry.profile_by_id(account_id)
    initial_balance = profile.expected_initial_balance if profile else Decimal(str(equity))
    enabled = profile.enabled if profile else True
    snapshot = daily_state_snapshot(db, account_id, config, balance=balance, equity=equity, now=now)
    prop = active_profile(config)
    status = challenge_status(
        initial_balance=initial_balance,
        daily_baseline_equity=snapshot["day_start_equity"],
        current_balance=balance,
        current_equity=equity,
        enabled=enabled,
        profit_target_percent=prop.profit_target_percent,
        daily_loss_percent=prop.maximum_daily_loss_percent,
        max_loss_percent=prop.maximum_total_loss_percent,
        minimum_trading_days=prop.minimum_trading_days,
    )
    entry_block_reasons = sorted(
        {_ENTRY_BLOCK_REASON_MAP[reason] for reason in status["entry_blockers"] if reason in _ENTRY_BLOCK_REASON_MAP}
    )
    return {
        **snapshot,
        "prop_profile": config.prop_profile,
        "initial_balance": initial_balance,
        "challenge_status": status,
        "entry_block_reasons": entry_block_reasons,
        "new_entries_allowed": not entry_block_reasons,
    }
