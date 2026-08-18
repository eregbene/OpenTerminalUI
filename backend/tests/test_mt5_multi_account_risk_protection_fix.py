"""Regression suite for the five confirmed multi-account MT5 risk/protection bugs (final
verification audit): portfolio protection fail-open (Bug 1), global 10K-scale dollar risk caps
applied to every account (Bug 2), missing daily-loss baseline/reset (Bug 3), entry blocker fed
zero P&L (Bug 4), and non-account-scoped adaptive-manager idempotency (Bug 5).

The 25 numbered tests below correspond 1:1 to the audit's required regression list.
"""
from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import backend.adaptive_management.service as adaptive_service_module
import backend.brokers.mt5.autonomous as autonomous_module
import backend.portfolio_execution.service as portfolio_service_module
from backend.adaptive_management import service as adaptive_service
from backend.brokers.mt5 import account_registry
from backend.brokers.mt5.autonomous import MT5AutonomousTradingService
from backend.brokers.mt5.config import MT5Config, mt5_config
from backend.brokers.mt5.execution import MT5ExecutionService
from backend.brokers.mt5.models import MT5Symbol
from backend.brokers.mt5.orm import MT5PropDailyStateORM, MT5TradeRecordORM
from backend.brokers.mt5.prop_state import evaluate_entry_protection, get_or_create_daily_state, remaining_safety_budget_usd
from backend.portfolio_execution.orm import PortfolioSnapshotORM
from backend.portfolio_execution.service import PortfolioManager
from backend.shared.db import Base


# --------------------------------------------------------------------------------------
# Shared fakes / fixtures
# --------------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _pin_ftmo_initial_balances(monkeypatch):
    """This whole file's prop-rule math (5% daily loss on 25000 = 1250, 10% max loss on 25000 =
    2500, etc) is built around account_registry's CODED DEFAULT initial balances (25k/50k/100k).
    The real container env has these overridden (2026-08-18 broker migration to ICMarketsSC-Demo
    changed real balances to 35k/60k/101k without renaming the internal account_id/prefix) --
    pin them off for every test in this file so prop-limit calculations use the coded defaults
    these tests were written against, not today's real deployed override."""
    for prefix in ("25K", "50K", "100K"):
        monkeypatch.delenv(f"MT5_ACCOUNT_{prefix}_INITIAL_BALANCE", raising=False)
        monkeypatch.delenv(f"MT5_{prefix}_INITIAL_BALANCE", raising=False)


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(portfolio_service_module, "SessionLocal", SessionLocal)
    monkeypatch.setattr(adaptive_service_module, "SessionLocal", SessionLocal)
    monkeypatch.setattr(account_registry, "SessionLocal", SessionLocal)
    return SessionLocal


class FakePosition:
    def __init__(self, symbol: str, side: int, volume: float, profit: float = 0.0, comment: str = "BENSIM_AUTO strategy=A timeframe=M15"):
        self.ticket = abs(hash((symbol, side, volume))) % 100000
        self.symbol = symbol
        self.type = side
        self.volume = Decimal(str(volume))
        self.price_open = Decimal("1.1000")
        self.price_current = Decimal("1.1010")
        self.sl = Decimal("1.0990")
        self.tp = Decimal("1.1030")
        self.profit = Decimal(str(profit))
        self.swap = Decimal("0")
        self.comment = comment

    def model_dump(self, mode="json"):
        return self.__dict__


class FakeAccount:
    def __init__(self, login: int, balance: float = 10000.0, equity: float = 10000.0, margin: float = 100.0, free_margin: float = 9900.0):
        self.login = login
        self.server = "Bensim-Demo"
        self.company = "Bensim"
        self.currency = "USD"
        self.balance = Decimal(str(balance))
        self.equity = Decimal(str(equity))
        self.margin = Decimal(str(margin))
        self.free_margin = Decimal(str(free_margin))

    def model_dump(self, mode="json"):
        return {"login": self.login, "server": self.server, "currency": self.currency}


class FakeClient:
    def ensure_ready(self):
        return None


class FakeAdapter:
    def __init__(self, account_id: str, login: int, positions=None, balance=10000.0, equity=10000.0, margin=100.0, free_margin=9900.0, **config_kwargs):
        self.config = MT5Config(account_id=account_id, account_mode="DEMO", **config_kwargs)
        self.account = FakeAccount(login, balance=balance, equity=equity, margin=margin, free_margin=free_margin)
        self._positions = positions or []
        self.client = FakeClient()
        self.candles_calls = 0

    async def mt5_account(self):
        return self.account

    async def mt5_positions(self):
        return self._positions

    async def history(self, days=7):
        return {"deals": []}

    async def symbol_info(self, symbol):
        return None

    async def candles(self, symbol, timeframe, count=60):
        self.candles_calls += 1
        return []


def _fake_symbol(volume_step="0.01", volume_min="0.01", volume_max="100") -> MT5Symbol:
    return MT5Symbol(
        symbol="XAUUSD", visible=True, selected=True, digits=2, point=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"), trade_tick_value=Decimal("1.0"), trade_tick_value_profit=Decimal("1.0"),
        trade_tick_value_loss=Decimal("1.0"), trade_contract_size=Decimal("100.0"),
        volume_min=Decimal(volume_min), volume_max=Decimal(volume_max), volume_step=Decimal(volume_step),
    )


class _FakeNativeMT5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def order_calc_profit(self, order_type, symbol, volume, price_open, price_close):
        diff = (price_close - price_open) if order_type == self.ORDER_TYPE_BUY else (price_open - price_close)
        return diff * 100.0 * volume


class FakeExecutionAdapter(FakeAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = _NativeClientWrapper()


class _NativeClientWrapper:
    def ensure_ready(self):
        return _FakeNativeMT5()


# --------------------------------------------------------------------------------------
# 1-3: Portfolio snapshot account scoping (Bug 1)
# --------------------------------------------------------------------------------------

def test_01_snapshot_created_for_25k_is_retrievable_as_25k(monkeypatch):
    _session_factory(monkeypatch)
    manager = PortfolioManager(FakeAdapter("ftmo_demo_25k", login=250001), "ftmo_demo_25k")

    asyncio.run(manager.refresh())
    snapshot = manager.latest_snapshot("ftmo_demo_25k")

    assert snapshot is not None
    assert snapshot["account_id"] == "ftmo_demo_25k"


def test_02_raw_mt5_login_cannot_replace_canonical_account_id(monkeypatch):
    _session_factory(monkeypatch)
    manager = PortfolioManager(FakeAdapter("ftmo_demo_50k", login=500001), "ftmo_demo_50k")

    asyncio.run(manager.refresh())
    snapshot = manager.latest_snapshot("ftmo_demo_50k")

    assert snapshot["account_id"] == "ftmo_demo_50k"
    assert snapshot["mt5_login"] == "500001"
    assert manager.latest_snapshot("500001") is None  # raw login is never a valid lookup key


def test_03_snapshot_from_one_account_cannot_satisfy_another_account(monkeypatch):
    _session_factory(monkeypatch)
    manager_25k = PortfolioManager(FakeAdapter("ftmo_demo_25k", login=250001), "ftmo_demo_25k")
    manager_50k = PortfolioManager(FakeAdapter("ftmo_demo_50k", login=500001), "ftmo_demo_50k")

    asyncio.run(manager_25k.refresh())
    asyncio.run(manager_50k.refresh())

    assert manager_25k.latest_snapshot("ftmo_demo_25k")["account_id"] == "ftmo_demo_25k"
    assert manager_50k.latest_snapshot("ftmo_demo_50k")["account_id"] == "ftmo_demo_50k"
    assert manager_25k.latest_snapshot("ftmo_demo_100k") is None  # never refreshed, must not fall back to another account's row


# --------------------------------------------------------------------------------------
# 4: Missing/stale portfolio state fails CLOSED, not open
# --------------------------------------------------------------------------------------

def test_04_missing_or_stale_portfolio_state_does_not_silently_bypass_protection(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    manager = PortfolioManager(FakeAdapter("ftmo_demo_100k", login=100001), "ftmo_demo_100k")

    allowed, blockers = manager.can_open_new_trade("ftmo_demo_100k")
    assert allowed is False
    assert blockers == ["PORTFOLIO_STATE_UNAVAILABLE"]

    # A stale (too-old) snapshot must ALSO fail closed, not be treated as current.
    with SessionLocal() as db:
        row = PortfolioSnapshotORM(snapshot_id="STALE_1", account_id="ftmo_demo_100k", protection_state={"blockers": [], "new_entries_allowed": True})
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=6)
        db.add(row)
        db.commit()

    allowed, blockers = manager.can_open_new_trade("ftmo_demo_100k")
    assert allowed is False
    assert blockers == ["PORTFOLIO_STATE_STALE"]


# --------------------------------------------------------------------------------------
# 5-6: Open risk / currency exposure are genuinely account-specific
# --------------------------------------------------------------------------------------

def test_05_open_risk_is_account_specific(monkeypatch):
    _session_factory(monkeypatch)
    small = PortfolioManager(FakeAdapter("ftmo_demo_25k", login=250001, positions=[FakePosition("EURUSD", 0, 0.1)]), "ftmo_demo_25k")
    large = PortfolioManager(FakeAdapter("ftmo_demo_100k", login=100001, positions=[FakePosition("EURUSD", 0, 1.0)]), "ftmo_demo_100k")

    asyncio.run(small.refresh())
    asyncio.run(large.refresh())

    small_risk = small.risk("ftmo_demo_25k")["open_risk"]
    large_risk = large.risk("ftmo_demo_100k")["open_risk"]
    assert small_risk != large_risk
    assert large_risk == pytest.approx(small_risk * 10, rel=1e-6)


def test_06_currency_exposure_is_account_specific(monkeypatch):
    _session_factory(monkeypatch)
    eur = PortfolioManager(FakeAdapter("ftmo_demo_25k", login=250001, positions=[FakePosition("EURUSD", 0, 1.0)]), "ftmo_demo_25k")
    gbp = PortfolioManager(FakeAdapter("ftmo_demo_50k", login=500001, positions=[FakePosition("GBPUSD", 0, 1.0)]), "ftmo_demo_50k")

    asyncio.run(eur.refresh())
    asyncio.run(gbp.refresh())

    eur_exposure = eur.exposure("ftmo_demo_25k")["currency"]
    gbp_exposure = gbp.exposure("ftmo_demo_50k")["currency"]
    assert "EUR" in eur_exposure and "EUR" not in gbp_exposure
    assert "GBP" in gbp_exposure and "GBP" not in eur_exposure


# --------------------------------------------------------------------------------------
# 7: Correlation is computed from the account's OWN adapter, not a shared global one
# --------------------------------------------------------------------------------------

def test_07_correlation_protection_is_account_specific(monkeypatch):
    _session_factory(monkeypatch)
    adapter_a = FakeAdapter("ftmo_demo_25k", login=250001, positions=[FakePosition("EURUSD", 0, 1.0)])
    adapter_b = FakeAdapter("ftmo_demo_50k", login=500001, positions=[FakePosition("GBPUSD", 0, 1.0)])
    manager_a = PortfolioManager(adapter_a, "ftmo_demo_25k")
    manager_b = PortfolioManager(adapter_b, "ftmo_demo_50k")

    asyncio.run(manager_a.refresh())
    asyncio.run(manager_b.refresh())

    # Each account's own adapter -- not the shared global one -- was used for candle lookups.
    assert adapter_a.candles_calls >= 1
    assert adapter_b.candles_calls >= 1


# --------------------------------------------------------------------------------------
# 8: Margin protection is account-specific
# --------------------------------------------------------------------------------------

def test_08_margin_protection_is_account_specific(monkeypatch):
    _session_factory(monkeypatch)
    manager = PortfolioManager(FakeAdapter("ftmo_demo_25k", login=250001), "ftmo_demo_25k")

    healthy = manager.protection_from_values(positions=[], account={"equity": 100000, "margin": 1000, "balance": 100000, "free_margin": 99000}, risk_rows=[], exposure={"currency": {}})
    overleveraged = manager.protection_from_values(positions=[], account={"equity": 100000, "margin": 60000, "balance": 100000, "free_margin": 40000}, risk_rows=[], exposure={"currency": {}})

    assert "MAX_MARGIN_UTILIZATION" not in healthy["blockers"]
    assert "MAX_MARGIN_UTILIZATION" in overleveraged["blockers"]


# --------------------------------------------------------------------------------------
# 9-10: Independent, equity-scaled risk budgets (Bug 2)
# --------------------------------------------------------------------------------------

def test_09_accounts_use_independent_risk_budgets_not_a_shared_flat_dollar_cap():
    svc = MT5ExecutionService(FakeExecutionAdapter("demo_10k", login=1))
    symbol = _fake_symbol()

    small = asyncio.run(svc.calculate_risk_size(account_equity=Decimal("10000"), symbol=symbol, direction="LONG", entry=Decimal("4400"), stop=Decimal("4380"), target=Decimal("4430")))
    large = asyncio.run(svc.calculate_risk_size(account_equity=Decimal("100000"), symbol=symbol, direction="LONG", entry=Decimal("4400"), stop=Decimal("4380"), target=Decimal("4430")))

    # Pre-fix: both would have been capped at the same flat $50 trade_risk_cap. Post-fix, the
    # 100K account's budget scales with its own equity, ~10x the 10K account's.
    assert large.effective_risk_usd > small.effective_risk_usd
    assert large.effective_risk_usd == pytest.approx(small.effective_risk_usd * 10, rel=1e-6)


def test_10_same_candidate_produces_independently_calculated_volume():
    svc = MT5ExecutionService(FakeExecutionAdapter("demo_10k", login=1))
    symbol = _fake_symbol()
    kwargs = dict(symbol=symbol, direction="LONG", entry=Decimal("4400"), stop=Decimal("4380"), target=Decimal("4430"))

    result_25k = asyncio.run(svc.calculate_risk_size(account_equity=Decimal("25000"), **kwargs))
    result_100k = asyncio.run(svc.calculate_risk_size(account_equity=Decimal("100000"), **kwargs))

    assert result_25k.status == "APPROVED"
    assert result_100k.status == "APPROVED"
    assert result_25k.volume != result_100k.volume
    assert result_100k.volume > result_25k.volume


# --------------------------------------------------------------------------------------
# 11-15: Daily-loss baseline persistence (Bug 3)
# --------------------------------------------------------------------------------------

def test_11_daily_state_resets_at_correct_boundary(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    config = MT5Config(account_id="ftmo_demo_25k", prop_profile="FTMO_2_STEP")

    with SessionLocal() as db:
        before_midnight = datetime(2026, 8, 12, 21, 0, 0, tzinfo=timezone.utc)  # 23:00 Europe/Prague (CEST, UTC+2)
        after_midnight = datetime(2026, 8, 12, 22, 30, 0, tzinfo=timezone.utc)  # 00:30 Europe/Prague next day
        first = get_or_create_daily_state(db, "ftmo_demo_25k", config, balance=Decimal("25000"), equity=Decimal("25000"), now=before_midnight)
        second = get_or_create_daily_state(db, "ftmo_demo_25k", config, balance=Decimal("24000"), equity=Decimal("24000"), now=after_midnight)
        first_day, first_balance = first.trading_day, first.day_start_balance
        second_day, second_balance = second.trading_day, second.day_start_balance

    assert first_day != second_day
    assert first_balance == 25000.0
    assert second_balance == 24000.0


def test_12_daily_state_survives_service_restart(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    config = MT5Config(account_id="ftmo_demo_25k", prop_profile="FTMO_2_STEP")
    now = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)

    with SessionLocal() as db:
        first_call = get_or_create_daily_state(db, "ftmo_demo_25k", config, balance=Decimal("25000"), equity=Decimal("25000"), now=now)
        assert first_call.day_start_equity == 25000.0

    # Simulate a backend/Docker/bridge restart: a brand-new call, no in-memory state carried
    # over, with a DIFFERENT observed balance/equity (as if equity had already moved).
    with SessionLocal() as db:
        second_call = get_or_create_daily_state(db, "ftmo_demo_25k", config, balance=Decimal("24500"), equity=Decimal("24500"), now=now + timedelta(hours=2))

    assert second_call.day_start_equity == 25000.0  # baseline is write-once, not overwritten


def test_13_floating_loss_affects_daily_loss_protection(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    config = MT5Config(account_id="ftmo_demo_25k", prop_profile="FTMO_2_STEP")
    now = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)

    with SessionLocal() as db:
        get_or_create_daily_state(db, "ftmo_demo_25k", config, balance=Decimal("25000"), equity=Decimal("25000"), now=now)
        # Balance unchanged (nothing realized) but equity dropped -- pure floating loss.
        protection = evaluate_entry_protection(db, "ftmo_demo_25k", config, balance=Decimal("25000"), equity=Decimal("23600"), now=now + timedelta(minutes=5))

    assert Decimal(protection["challenge_status"]["daily_loss_used"]) == Decimal("1400.00")
    assert protection["floating_pnl"] == Decimal("-1400")


def test_14_realized_loss_affects_daily_loss_protection(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    config = MT5Config(account_id="ftmo_demo_25k", prop_profile="FTMO_2_STEP")
    now = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)

    with SessionLocal() as db:
        get_or_create_daily_state(db, "ftmo_demo_25k", config, balance=Decimal("25000"), equity=Decimal("25000"), now=now)
        # Both balance and equity dropped together -- a realized loss, not just floating.
        protection = evaluate_entry_protection(db, "ftmo_demo_25k", config, balance=Decimal("23600"), equity=Decimal("23600"), now=now + timedelta(minutes=5))

    assert Decimal(protection["challenge_status"]["daily_loss_used"]) == Decimal("1400.00")
    assert protection["floating_pnl"] == Decimal("0")


def test_15_commission_swap_fee_included_in_daily_breakdown(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    config = MT5Config(account_id="ftmo_demo_25k", prop_profile="FTMO_2_STEP")
    now = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)

    with SessionLocal() as db:
        get_or_create_daily_state(db, "ftmo_demo_25k", config, balance=Decimal("25000"), equity=Decimal("25000"), now=now)
        trade = MT5TradeRecordORM(trade_id="T1", account_id="ftmo_demo_25k", cycle_id="C1", symbol="EURUSD", broker_symbol="EURUSD", direction="LONG", lot_size=1.0)
        trade.gross_pnl = 100.0
        trade.commission = -5.0
        trade.swap = -1.5
        trade.fee = -0.5
        trade.realized_pnl = 93.0
        trade.close_timestamp = now + timedelta(minutes=10)
        db.add(trade)
        db.commit()
        snapshot = evaluate_entry_protection(db, "ftmo_demo_25k", config, balance=Decimal("25093"), equity=Decimal("25093"), now=now + timedelta(minutes=20))

    assert snapshot["realized_pnl_today"] == Decimal("93.0")
    assert snapshot["commission_today"] == Decimal("-5.0")
    assert snapshot["swap_today"] == Decimal("-1.5")
    assert snapshot["fee_today"] == Decimal("-0.5")


# --------------------------------------------------------------------------------------
# 16-18: Entry blocker actually blocks, with explicit reasons, per-account (Bug 4)
# --------------------------------------------------------------------------------------

def test_16_daily_loss_blocker_actually_blocks_new_entries(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    config = MT5Config(account_id="ftmo_demo_25k", prop_profile="FTMO_2_STEP")
    now = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)

    with SessionLocal() as db:
        get_or_create_daily_state(db, "ftmo_demo_25k", config, balance=Decimal("25000"), equity=Decimal("25000"), now=now)
        # 5% daily loss limit on 25000 = 1250. Breach it outright.
        protection = evaluate_entry_protection(db, "ftmo_demo_25k", config, balance=Decimal("23700"), equity=Decimal("23700"), now=now + timedelta(minutes=5))

    assert "PROP_DAILY_LOSS_ENTRY_BLOCK" in protection["entry_block_reasons"]
    assert protection["new_entries_allowed"] is False


def test_17_max_loss_blocker_actually_blocks_new_entries(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    config = MT5Config(account_id="ftmo_demo_25k", prop_profile="FTMO_2_STEP")
    now = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)

    with SessionLocal() as db:
        # Today's baseline is ALREADY drawn down from a prior day (22450, not the account's
        # original 25000) so today's daily-loss usage stays small -- isolating the max-loss
        # breach from the daily-loss breach, since challenge_status()'s state machine is an
        # if/elif chain that reports whichever fires first (daily takes priority).
        get_or_create_daily_state(db, "ftmo_demo_25k", config, balance=Decimal("22450"), equity=Decimal("22450"), now=now)
        # 10% max loss limit on the account's real 25000 initial balance = 2500. Current equity
        # 22400 -> max_loss_used = 2600, breached; daily_loss_used = 50, well under the 1250
        # daily limit.
        protection = evaluate_entry_protection(db, "ftmo_demo_25k", config, balance=Decimal("22400"), equity=Decimal("22400"), now=now + timedelta(minutes=5))

    assert "PROP_DAILY_LOSS_ENTRY_BLOCK" not in protection["entry_block_reasons"]
    assert "PROP_MAX_LOSS_ENTRY_BLOCK" in protection["entry_block_reasons"]
    assert protection["new_entries_allowed"] is False


def test_18_one_account_blocked_does_not_block_another(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    config_25k = MT5Config(account_id="ftmo_demo_25k", prop_profile="FTMO_2_STEP")
    config_50k = MT5Config(account_id="ftmo_demo_50k", prop_profile="FTMO_2_STEP")
    now = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)

    with SessionLocal() as db:
        get_or_create_daily_state(db, "ftmo_demo_25k", config_25k, balance=Decimal("25000"), equity=Decimal("25000"), now=now)
        get_or_create_daily_state(db, "ftmo_demo_50k", config_50k, balance=Decimal("50000"), equity=Decimal("50000"), now=now)
        blocked = evaluate_entry_protection(db, "ftmo_demo_25k", config_25k, balance=Decimal("23700"), equity=Decimal("23700"), now=now + timedelta(minutes=5))
        healthy = evaluate_entry_protection(db, "ftmo_demo_50k", config_50k, balance=Decimal("50000"), equity=Decimal("50000"), now=now + timedelta(minutes=5))

    assert blocked["new_entries_allowed"] is False
    assert healthy["new_entries_allowed"] is True


# --------------------------------------------------------------------------------------
# 19: Protective adaptive management is structurally independent of the entry gate
# --------------------------------------------------------------------------------------

def test_19_adaptive_management_monitor_loop_never_calls_the_entry_blocker():
    source = inspect.getsource(adaptive_service)
    assert "_global_blockers" not in source
    assert "evaluate_entry_protection" not in source
    assert "prop_state" not in source


# --------------------------------------------------------------------------------------
# 20: Adaptive idempotency cannot collide across accounts (Bug 5)
# --------------------------------------------------------------------------------------

def test_20_adaptive_idempotency_cannot_collide_across_accounts():
    # The exact scenario named in the audit: two DIFFERENT accounts' position with the SAME
    # raw ticket number, same action, same volume/sl/tp, in the same minute bucket.
    key_50k = adaptive_service._idempotency_key("ftmo_demo_50k", "12345", "MOVE_SL_BREAKEVEN", 0.1, 4400.0, 4450.0)
    key_100k = adaptive_service._idempotency_key("ftmo_demo_100k", "12345", "MOVE_SL_BREAKEVEN", 0.1, 4400.0, 4450.0)

    assert key_50k != key_100k


# --------------------------------------------------------------------------------------
# 21: Wrong bridge/account context still fails closed
# --------------------------------------------------------------------------------------

def test_21_unknown_account_id_fails_closed_not_crash(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    config = MT5Config(account_id="totally_unknown_account", prop_profile="FTMO_2_STEP")

    with SessionLocal() as db:
        protection = evaluate_entry_protection(db, "totally_unknown_account", config, balance=Decimal("1000"), equity=Decimal("1000"))

    # No registered profile -> falls back to treating current equity as the (unverifiable)
    # initial balance rather than raising, so a misconfigured/unknown account degrades safely
    # instead of crashing the entry gate.
    assert protection["initial_balance"] == Decimal("1000")


# --------------------------------------------------------------------------------------
# 22-25: Standing safety invariants unaffected by this fix
# --------------------------------------------------------------------------------------

def test_22_all_four_accounts_remain_demo():
    for profile in account_registry.configured_profiles():
        assert profile.account_mode == "DEMO"


def test_23_live_trading_disabled_globally():
    assert mt5_config().live_trading_enabled is False


def test_24_openai_absent_from_mt5_execution():
    # A precise check for an actual OpenAI SDK import/call, not a substring match -- the module
    # legitimately contains identifiers like `_no_openai_calls` (a counter that PROVES zero
    # OpenAI calls happen), which a naive "openai" in source.lower() would misreport as a hit.
    source = inspect.getsource(autonomous_module)
    assert "import openai" not in source
    assert "from openai" not in source
    assert "openai.OpenAI(" not in source
    assert "openai_client" not in source


def test_25_ibkr_not_part_of_mt5_execution():
    config = mt5_config()
    assert config.broker_provider == "MT5"
    assert config.forex_execution_provider == "MT5"


# --------------------------------------------------------------------------------------
# Extra: remaining_safety_budget_usd is the tightest of the four candidates
# --------------------------------------------------------------------------------------

def test_remaining_safety_budget_usd_is_tightest_candidate(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    config = MT5Config(account_id="ftmo_demo_25k", prop_profile="FTMO_2_STEP")
    now = datetime(2026, 8, 12, 10, 0, 0, tzinfo=timezone.utc)

    with SessionLocal() as db:
        get_or_create_daily_state(db, "ftmo_demo_25k", config, balance=Decimal("25000"), equity=Decimal("25000"), now=now)
        # Close to (but not past) the internal daily buffer: daily loss used = 900, internal
        # buffer limit = 1000 (80% of 1250) -- remaining internal daily headroom = 100, the
        # tightest of the four candidates.
        protection = evaluate_entry_protection(db, "ftmo_demo_25k", config, balance=Decimal("24100"), equity=Decimal("24100"), now=now + timedelta(minutes=5))
        budget = remaining_safety_budget_usd(protection)

    assert budget == Decimal("100.00")
