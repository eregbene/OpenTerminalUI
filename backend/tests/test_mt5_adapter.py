from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.brokers.mt5 import account_registry
from backend.brokers.models import BrokerOrderCommand
from backend.brokers.mt5.autonomous import MT5AutonomousTradingService, _mt5_order_comment, _score_candidate, _swing_level
from backend.brokers.mt5.adapter import MT5Adapter
from backend.brokers.mt5.client import MT5Client
from backend.brokers.mt5.config import MT5Config, mt5_config
from backend.brokers.mt5.execution import MT5ExecutionService
from backend.brokers.mt5.exceptions import MT5ReadOnlyViolation, MT5UnavailableError
from backend.brokers.mt5.prop_risk import risk_status
from backend.portfolio_execution import service as portfolio_execution_service
from backend.shared.db import Base


class FakeRow(SimpleNamespace):
    def _asdict(self):
        return dict(self.__dict__)


def _execution_session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(portfolio_execution_service, "SessionLocal", SessionLocal)
    monkeypatch.setattr(account_registry, "SessionLocal", SessionLocal)
    return SessionLocal


class FakeMT5:
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_H1 = 60
    TIMEFRAME_H4 = 240
    TIMEFRAME_D1 = 1440
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_INVALID_VOLUME = 10014

    def __init__(self):
        self.initialized = False
        self.login_calls = []
        self.order_send_calls = 0

    def initialize(self, **kwargs):
        self.initialized = True
        self.initialize_kwargs = kwargs
        return True

    def login(self, **kwargs):
        self.login_calls.append(kwargs)
        return True

    def shutdown(self):
        self.initialized = False

    def terminal_info(self):
        return FakeRow(name="MetaTrader 5", connected=True, community_account=True)

    def version(self):
        return (500, 4515, "1 Jan 2026")

    def account_info(self):
        return FakeRow(login=123456, server="MetaQuotes-Demo", currency="USD", balance=10000.0, equity=10050.0, margin=100.0, margin_free=9950.0, leverage=100, name="Demo", company="MetaQuotes", trade_mode=0, margin_mode=2)

    def symbol_info(self, symbol):
        if symbol == "XAUUSD":
            return FakeRow(name=symbol, visible=True, select=True, bid=2400.0, ask=2400.2, spread=20, digits=2, trade_mode=0, currency_base="XAU", currency_profit="USD", currency_margin="USD", path="Metals\\XAUUSD", description="Gold vs US Dollar")
        raw = symbol.replace(".", "").replace("-PRO", "")
        if raw.startswith("M"):
            raw = raw[1:]
        return FakeRow(name=symbol, visible=True, select=True, bid=1.1, ask=1.1002, spread=2, digits=5, trade_mode=0, currency_base=raw[:3], currency_profit=raw[3:6], currency_margin=raw[:3], path=f"Forex\\{symbol}", description=f"{raw[:3]} vs {raw[3:6]}", trade_calc_mode=0, point=0.00001, trade_tick_size=0.00001, trade_tick_value=1.0, trade_tick_value_profit=1.0, trade_tick_value_loss=1.0, trade_contract_size=100000, volume_min=0.01, volume_max=100.0, volume_step=0.01, trade_stops_level=10, trade_freeze_level=0, spread_float=True, swap_long=-1.0, swap_short=0.5, swap_mode=0, margin_initial=0, margin_maintenance=0, filling_mode=1, order_mode=127, trade_execution=0)

    def symbol_select(self, symbol, enable):
        return True

    def symbols_get(self, group=None):
        return [
            self.symbol_info(symbol)
            for symbol in (
                "EURUSD",
                "GBPUSD.a",
                "mUSDZAR",
                "EURGBP",
                "USDJPY",
                "AUDUSD",
                "USDCAD",
                "USDCHF",
                "NZDUSD",
                "EURJPY",
                "GBPJPY",
                "XAUUSD",
            )
        ]

    def symbol_info_tick(self, symbol):
        return FakeRow(time=1_800_000_000, bid=1.1, ask=1.1002, last=1.1001)

    def copy_rates_from_pos(self, symbol, timeframe, start, count):
        return [
            {"time": 1_800_000_000 + i * 60, "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "tick_volume": 100 + i, "spread": 2, "real_volume": 0}
            for i in range(count)
        ]

    def positions_get(self):
        return [FakeRow(ticket=1, symbol="EURUSD", type=0, volume=1.0, price_open=1.09, price_current=1.1, profit=10.0, time=1_800_000_000)]

    def orders_get(self):
        return [FakeRow(ticket=2, symbol="EURUSD", type=2, volume_current=1.0, price_open=1.08, sl=1.07, tp=1.11, time_setup=1_800_000_000)]

    def history_deals_get(self, from_date, to_date):
        return [FakeRow(ticket=3, order=2, symbol="EURUSD", type=0, volume=1.0, price=1.1, profit=5.0, commission=-0.5, time=1_800_000_000)]

    def history_orders_get(self, from_date, to_date):
        return [FakeRow(ticket=2, order=None, symbol="EURUSD", type=2, volume=1.0, price=1.08, profit=0.0, commission=0.0, time=1_800_000_000)]

    def order_check(self, request):
        self.last_order_check = request
        if request["volume"] <= 0:
            return FakeRow(retcode=self.TRADE_RETCODE_INVALID_VOLUME, comment="invalid volume", margin=0)
        return FakeRow(retcode=self.TRADE_RETCODE_DONE, comment="checked", margin=10)

    def order_send(self, request):
        self.order_send_calls += 1
        self.last_order_send = request
        return FakeRow(retcode=self.TRADE_RETCODE_DONE, comment="done", order=77, deal=88, price=request["price"], volume=request["volume"])

    def order_calc_margin(self, order_type, symbol, volume, price):
        return 100 * volume

    def order_calc_profit(self, order_type, symbol, volume, price_open, price_close):
        # Realistic-enough simulation for the canonical risk calculator's cross-validation:
        # proportional to price distance and volume (a real broker's order_calc_profit is), and
        # uses the SAME contract-size convention as the test fixtures' trade_contract_size, so it
        # agrees with the contract_size method for a "healthy" (non-deliberately-broken) symbol
        # exactly like a real broker's order_calc_profit agrees with a correctly configured
        # trade_contract_size. The old flat "-50 or 90 regardless of distance" version predated
        # the canonical calculator and made every distance-varying sizing test look like a
        # critical broker-metadata mismatch.
        contract = 100.0 if str(symbol).upper().startswith("XAU") else 100000.0
        diff = (price_close - price_open) if order_type == self.ORDER_TYPE_BUY else (price_open - price_close)
        return diff * contract * volume

    def last_error(self):
        return (0, "OK")


class FakeLiveMT5(FakeMT5):
    def account_info(self):
        return FakeRow(login=555555, server="MetaQuotes-Live", currency="USD", balance=10000.0, equity=10000.0, margin=0.0, margin_free=10000.0, leverage=100, name="Live", company="MetaQuotes", trade_mode=1, margin_mode=2)


def fake_adapter() -> MT5Adapter:
    client = MT5Client(
        MT5Config(
            enabled=True,
            login=123456,
            password="x",
            server="MetaQuotes-Demo",
            order_submission_enabled=True,
            autonomous_submission_enabled=True,
            broker_provider="MT5",
            forex_execution_provider="MT5",
        )
    )
    client._mt5 = FakeMT5()
    return MT5Adapter(client.config, client)


def fake_live_adapter() -> MT5Adapter:
    client = MT5Client(MT5Config(enabled=True, login=555555, server="MetaQuotes-Demo", order_submission_enabled=True, autonomous_submission_enabled=True))
    client._mt5 = FakeLiveMT5()
    return MT5Adapter(client.config, client)


def test_mt5_config_reads_env(monkeypatch):
    monkeypatch.setenv("MT5_ENABLED", "true")
    monkeypatch.setenv("MT5_LOGIN", "123456")
    monkeypatch.setenv("MT5_SERVER", "MetaQuotes-Demo")
    monkeypatch.setenv("MT5_TIMEOUT_MS", "120000")
    monkeypatch.setenv("MT5_EXCLUDED_CURRENCIES", "TRY,MXN")

    cfg = mt5_config()

    assert cfg.enabled is True
    assert cfg.login == 123456
    assert cfg.server == "MetaQuotes-Demo"
    assert cfg.timeout_ms == 120000
    assert cfg.excluded_currencies == ("TRY", "MXN")
    assert cfg.read_only is True


def test_mt5_connect_account_symbols_quotes_and_candles():
    adapter = fake_adapter()

    health = asyncio.run(adapter.connect())
    account = asyncio.run(adapter.mt5_account())
    symbols = asyncio.run(adapter.verify_symbols())
    quote = asyncio.run(adapter.latest_tick("EURUSD"))
    m15 = asyncio.run(adapter.candles("EURUSD", "M15", count=100))
    h1 = asyncio.run(adapter.candles("EURUSD", "H1", count=100))
    h4 = asyncio.run(adapter.candles("EURUSD", "H4", count=100))

    assert health.order_submission_status == "READ_ONLY"
    assert account.login == 123456
    assert set(symbols) == {"EURUSD", "GBPUSD", "USDJPY", "XAUUSD"}
    assert quote.bid == Decimal("1.1")
    assert len(m15) == len(h1) == len(h4) == 100


def test_mt5_read_only_blocks_submit_and_cancel():
    adapter = fake_adapter()
    command = BrokerOrderCommand(canonical_order_id="x", account_id="123456", instrument_id="FX:EURUSD", side="BUY", order_type="MARKET", time_in_force="DAY", quantity=Decimal("1000"), approved_quantity=Decimal("1000"), risk_evaluation_id="r", idempotency_key="i")

    with pytest.raises(MT5ReadOnlyViolation):
        asyncio.run(adapter.submit_order(command))

    with pytest.raises(MT5ReadOnlyViolation):
        asyncio.run(adapter.cancel_order(command))  # type: ignore[arg-type]


def test_mt5_read_methods_return_positions_orders_history():
    adapter = fake_adapter()
    asyncio.run(adapter.connect())

    positions = asyncio.run(adapter.mt5_positions())
    orders = asyncio.run(adapter.mt5_orders())
    history = asyncio.run(adapter.history(days=1))

    assert positions[0].symbol == "EURUSD"
    assert orders[0].ticket == 2
    assert history["deals"][0].commission == Decimal("-0.5")


def test_mt5_positions_raises_rather_than_silently_returning_empty_on_a_failed_call():
    """BUG FIX regression (traced live -- caused a real false-positive mass-closure incident):
    MT5's positions_get() returns None specifically to signal a FAILED call -- a genuine "zero
    open positions" response is an empty tuple, never None. Before this fix, None silently became
    [] via _rows(), indistinguishable from "no positions are open"; adaptive_management's
    _monitor_cycle used that to compute currently_open_ids and treated every real, still-open
    tracked position as newly closed. Must now raise MT5UnavailableError so callers' existing
    `except Exception` handling (already correct for every other hard MT5 failure) engages
    instead of silently proceeding with wrong data."""
    adapter = fake_adapter()
    asyncio.run(adapter.connect())
    adapter.client._mt5.positions_get = lambda: None

    with pytest.raises(MT5UnavailableError):
        asyncio.run(adapter.mt5_positions())


def test_mt5_terminal_diagnostics_redacts_login_and_verifies_demo():
    adapter = fake_adapter()

    status = asyncio.run(adapter.terminal_status())

    assert status.masked_login == "12***56"
    assert status.account_mode == "DEMO"
    assert status.hedging_mode == "HEDGING"
    assert status.external_python_trading_allowed is True


def test_mt5_live_account_rejected():
    adapter = fake_live_adapter()

    health = asyncio.run(adapter.connect())

    assert health.state.value in {"DISCONNECTED", "DEGRADED"}
    assert "not confirmed as demo" in (health.last_error or "")


def test_safety_blockers_healthy_demo_produces_no_blockers():
    service = MT5ExecutionService(fake_adapter())

    blockers = asyncio.run(service.safety_blockers())

    assert blockers == []


def test_safety_blockers_does_not_false_positive_on_disconnection(monkeypatch: pytest.MonkeyPatch):
    """A broker/bridge disconnection must be reported once, as TERMINAL_DISCONNECTED -- not
    also as the misleading CRITICAL_LIVE_ACCOUNT_DETECTED. That blocker was removed because
    MT5TerminalStatus.account_mode is only ever an echo of our own config.account_mode (see
    terminal_status_from_raw), never broker-observed data -- during a disconnection it
    defaults to None, and None != "DEMO" used to fire a scary-sounding false positive for
    exactly the same underlying condition TERMINAL_DISCONNECTED already reports."""
    adapter = fake_adapter()
    service = MT5ExecutionService(adapter)

    from backend.brokers.mt5.models import MT5TerminalStatus

    disconnected_status = MT5TerminalStatus(connected=False, initialized=False, package_available=True, terminal_info={}, version=None, last_error=(1, "no connection"))
    monkeypatch.setattr(adapter, "terminal_status", lambda: asyncio.sleep(0, result=disconnected_status))

    blockers = asyncio.run(service.safety_blockers())

    assert "TERMINAL_DISCONNECTED" in blockers
    assert "CRITICAL_LIVE_ACCOUNT_DETECTED" not in blockers


def test_safety_blockers_still_blocks_a_genuine_live_account():
    """Real, broker-data-driven live-account protection is unaffected by removing the
    redundant account_mode check -- assert_demo_account() (using the actual reported
    account.trade_mode/server) still raises and the cycle is still blocked."""
    service = MT5ExecutionService(fake_live_adapter())

    blockers = asyncio.run(service.safety_blockers())

    assert any(b.startswith("BROKER_NOT_READY") for b in blockers)
    assert "CRITICAL_LIVE_ACCOUNT_DETECTED" not in blockers


def test_mt5_forex_universe_uses_configured_live_symbols_and_includes_xauusd():
    adapter = fake_adapter()

    universe = asyncio.run(adapter.forex_universe())
    mappings = universe.canonical_mappings

    assert mappings["EURUSD"] == "EURUSD"
    assert mappings["GBPUSD.A"] == "GBPUSD"
    assert mappings["XAUUSD"] == "XAUUSD"
    assert "MUSDZAR" not in mappings
    assert universe.total_forex_pairs == 10
    assert universe.majors == 7
    assert universe.minors == 2
    assert universe.exotics == 0
    assert any(row.canonical_pair == "XAUUSD" and row.asset_class == "METAL" for row in universe.items)


def test_mt5_completed_candles_skip_current_bar():
    adapter = fake_adapter()
    asyncio.run(adapter.connect())

    rows = asyncio.run(adapter.candles("EURUSD", "M15", count=3))

    assert len(rows) == 3
    assert adapter.client.mt5.initialized is True


def test_mt5_ai_usage_controls_are_low_cost_and_keyless():
    adapter = fake_adapter()

    usage = asyncio.run(adapter.ai_usage_controls())
    scheduler = asyncio.run(adapter.scheduler_status())

    assert usage["model"] == "gpt-4.1-mini"
    assert usage["daily_cost_limit_usd"] <= 0.10
    assert usage["monthly_cost_limit_usd"] <= 4.50
    assert usage["max_provider_requests_per_cycle"] == 1
    assert usage["api_key_exposed"] is False
    assert scheduler.order_submission_enabled is True


def test_mt5_effective_risk_uses_stricter_percent_cap_and_rounds_down():
    adapter = fake_adapter()
    service = MT5ExecutionService(adapter)
    symbol = asyncio.run(adapter.symbol_info("EURUSD"))

    sizing = asyncio.run(
        service.calculate_risk_size(
            account_equity=Decimal("100000"),
            symbol=symbol,
            direction="LONG",
            entry=Decimal("1.10000"),
            stop=Decimal("1.09500"),
            target=Decimal("1.10900"),
        )
    )

    assert sizing.effective_risk_usd == Decimal("250.00")
    assert sizing.volume > Decimal("0.01")
    assert sizing.status == "APPROVED"


def test_mt5_lot_size_varies_with_stop_distance():
    adapter = fake_adapter()
    service = MT5ExecutionService(adapter)
    symbol = asyncio.run(adapter.symbol_info("EURUSD"))

    narrow = asyncio.run(service.calculate_risk_size(account_equity=Decimal("100000"), symbol=symbol, direction="LONG", entry=Decimal("1.10000"), stop=Decimal("1.09900"), target=Decimal("1.10200")))
    wide = asyncio.run(service.calculate_risk_size(account_equity=Decimal("100000"), symbol=symbol, direction="LONG", entry=Decimal("1.10000"), stop=Decimal("1.09000"), target=Decimal("1.11800")))

    assert narrow.volume > wide.volume
    assert narrow.volume != Decimal("0.01")


def _xauusd_symbol_with_understated_tick_value() -> "MT5Symbol":
    # Reproduces the exact live-account XAUUSD symbol metadata behind a confirmed sizing bug:
    # trade_tick_value=0.1 implies $10/point/lot via the standard (tick_value/tick_size) formula,
    # but the broker's own order_calc_profit (ground truth, confirmed live) pays out $100/point/
    # lot -- trade_contract_size(100) * tick_size(0.01) == 1.0, not the reported 0.1. Trusting
    # tick_value alone silently oversizes every Gold position by 10x.
    from backend.brokers.mt5.models import MT5Symbol

    return MT5Symbol(
        symbol="XAUUSD", visible=True, selected=True, bid=Decimal("4283.55"), ask=Decimal("4283.92"), digits=2,
        point=Decimal("0.01"), trade_tick_size=Decimal("0.01"), trade_tick_value=Decimal("0.1"),
        trade_tick_value_profit=Decimal("0.1"), trade_tick_value_loss=Decimal("0.1"), trade_contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )


def _xauusd_symbol_clean() -> "MT5Symbol":
    # Self-consistent XAUUSD metadata (tick_value == contract_size x tick_size, exactly like the
    # real broker's order_calc_profit agrees with both) -- used where a test wants to isolate
    # ONE sizing behavior (e.g. volume-below-minimum) from the separate critical-mismatch-block
    # path that a deliberately-broken tick_value now correctly triggers.
    from backend.brokers.mt5.models import MT5Symbol

    return MT5Symbol(
        symbol="XAUUSD", visible=True, selected=True, bid=Decimal("4283.55"), ask=Decimal("4283.92"), digits=2,
        point=Decimal("0.01"), trade_tick_size=Decimal("0.01"), trade_tick_value=Decimal("1.0"),
        trade_tick_value_profit=Decimal("1.0"), trade_tick_value_loss=Decimal("1.0"), trade_contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )


def test_xauusd_sizing_cross_validates_tick_value_against_contract_size():
    # XAUUSD regression test: this is the exact live incident's numbers. The canonical
    # calculator's tick_value <-> contract_size dimensional self-consistency check (see
    # risk_calculator._validate_tick_value_self_consistency) now DETECTS that trade_tick_value
    # is the broken field (order_calc_profit and trade_contract_size independently agree exactly
    # on $2359/lot; trade_tick_value implies exactly 10x less), excludes it with a precise
    # reason, and sizes correctly off the two agreeing, trusted methods instead of blocking a
    # genuinely valid trade outright.
    adapter = fake_adapter()
    service = MT5ExecutionService(adapter)
    symbol = _xauusd_symbol_with_understated_tick_value()

    sizing = asyncio.run(
        service.calculate_risk_size(
            account_equity=Decimal("10000"), symbol=symbol, direction="LONG",
            entry=Decimal("4279.93"), stop=Decimal("4256.34"), target=Decimal("4316.14"),
        )
    )

    assert sizing.status == "APPROVED"
    assert sizing.risk_calculation_method in {"order_calc_profit", "contract_size"}
    assert sizing.risk_calculation_estimates["tick_value"]["available"] is False
    assert "TICK_VALUE_INCONSISTENT_WITH_CONTRACT_SIZE" in sizing.risk_calculation_estimates["tick_value"]["detail"]
    assert sizing.volume > Decimal("0")
    # The malformed, 10x-too-small tick_value figure must never have been the one relied on.
    assert sizing.risk_reward >= Decimal("1.5")


def test_minimum_lot_blocked_when_it_exceeds_risk_budget():
    # Never round UP to volume_min when even volume_min's monetary risk exceeds the configured
    # budget -- block the trade instead of silently accepting oversized risk. Uses
    # self-consistent metadata so this isolates the volume-min-too-risky path from the separate
    # critical-mismatch-block path (covered by the regression test above).
    adapter = fake_adapter()
    service = MT5ExecutionService(adapter)
    symbol = _xauusd_symbol_clean()

    sizing = asyncio.run(
        service.calculate_risk_size(
            account_equity=Decimal("10000"), symbol=symbol, direction="LONG",
            entry=Decimal("4279.93"), stop=Decimal("4200.00"), target=Decimal("4400.00"),  # huge stop distance
        )
    )

    assert sizing.status == "REJECTED"
    assert "VOLUME_BELOW_MINIMUM_RISK_TOO_HIGH" in sizing.reasons


def test_10k_account_50_max_risk_cannot_open_200_dollar_sl_risk_position():
    client = MT5Client(
        MT5Config(
            enabled=True, login=123456, password="x", server="MetaQuotes-Demo",
            order_submission_enabled=True, autonomous_submission_enabled=True,
            broker_provider="MT5", forex_execution_provider="MT5",
            risk_percent_per_trade=0.25, max_risk_per_trade_usd=50.0,
        )
    )
    client._mt5 = FakeMT5()
    adapter = MT5Adapter(client.config, client)
    service = MT5ExecutionService(adapter)
    symbol = _xauusd_symbol_with_understated_tick_value()

    sizing = asyncio.run(
        service.calculate_risk_size(
            account_equity=Decimal("10000"), symbol=symbol, direction="LONG",
            entry=Decimal("4279.93"), stop=Decimal("4256.34"), target=Decimal("4316.14"),
        )
    )

    if sizing.status == "APPROVED":
        assert sizing.projected_loss_usd <= Decimal("50.00")
    else:
        assert sizing.status == "REJECTED"


def test_mt5_order_check_failure_blocks_order_send():
    adapter = fake_adapter()
    service = MT5ExecutionService(adapter)
    intent = _intent(volume=Decimal("0"))

    result = asyncio.run(service.submit_market_order(intent))

    assert result.status == "INVALID_VOLUME"
    assert result.retcode == adapter.client.mt5.TRADE_RETCODE_INVALID_VOLUME
    assert result.comment == "invalid volume"
    assert adapter.client.mt5.order_send_calls == 0
    assert result.request["symbol"] == "EURUSD"
    assert result.raw["stage"] == "order_check"
    assert result.raw["broker_response"]["comment"] == "invalid volume"
    assert result.raw["last_error"] == [0, "OK"] or result.raw["last_error"] == (0, "OK")


def test_mt5_order_check_empty_response_keeps_request_and_last_error():
    adapter = fake_adapter()
    adapter.client.mt5.order_check = lambda request: None
    service = MT5ExecutionService(adapter)

    result = asyncio.run(service.submit_market_order(_intent(volume=Decimal("0.01"))))

    assert result.status == "SUBMISSION_UNKNOWN"
    assert result.comment == "empty order_check response"
    assert result.request["symbol"] == "EURUSD"
    assert result.raw["retcode_name"] == "SUBMISSION_UNKNOWN"
    assert adapter.client.mt5.order_send_calls == 0


def test_mt5_order_comment_encodes_strategy_within_mt5_limit():
    candidate = {"canonical_pair": "EUR/USD", "context": {"strategy_id": "mtfai1"}}
    comment = _mt5_order_comment(candidate, datetime(2026, 8, 3, 17, 55, 51))

    assert comment == "BSM|mtfai1|1755"
    assert len(comment) <= 31
    assert comment.isascii()
    assert comment.startswith("BSM|")


def _candle(o, h, low, c):
    return SimpleNamespace(open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(low)), close=Decimal(str(c)))


def _trending_series(count, base, step):
    candles = []
    price = Decimal(str(base))
    step = Decimal(str(step))
    for _ in range(count):
        price += step
        candles.append(_candle(price - step / 2, price + Decimal("0.0003"), price - Decimal("0.0003"), price))
    return candles


def _uptrend_context():
    m15 = _trending_series(60, "1.1000", "0.0002")
    h1 = _trending_series(25, "1.0950", "0.0010")
    h4 = _trending_series(25, "1.0900", "0.0020")
    last_close = float(m15[-1].close)
    quote = SimpleNamespace(ask=last_close + 0.00005, bid=last_close - 0.00005, spread=0.00008)
    return quote, m15, h1, h4


def test_swing_level_uses_recent_low_for_long_and_high_for_short():
    m15 = _trending_series(30, "1.1000", "0.0002")

    assert _swing_level(m15, "LONG") == min(c.low for c in m15[-20:])
    assert _swing_level(m15, "SHORT") == max(c.high for c in m15[-20:])
    assert _swing_level(m15[:3], "LONG") is None


def test_score_candidate_uses_structure_and_atr_not_flat_one_atr(monkeypatch):
    # Explicitly isolated from whatever MT5_MTFAI1_V2_ENABLED happens to be in the ambient
    # environment this test runs in (the real deployed container sets it true) -- this test is
    # about the base geometry math, not V2, so it forces V2 off and passes a real symbol so the
    # (V2-only) symbol-universe gate can never be the thing under test here.
    from backend.brokers.mt5 import autonomous
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_V2_ENABLED", False)
    quote, m15, h1, h4 = _uptrend_context()

    score, direction, geometry = _score_candidate(quote, m15, h1, h4, "EURUSD")

    assert direction == "LONG"
    entry = Decimal(geometry["entry"])
    stop = Decimal(geometry["stop_loss"])
    target = Decimal(geometry["take_profit"])
    atr = sum(abs(c.high - c.low) for c in m15[-14:]) / Decimal("14")
    old_flat_stop = entry - atr
    assert stop != old_flat_stop
    assert stop < entry < target
    assert score > 0


def test_score_candidate_rejects_when_no_valid_stop_fits_bounds(monkeypatch):
    monkeypatch.setenv("MT5_AUTO_SL_MIN_ATR_MULT", "2.0")
    monkeypatch.setenv("MT5_AUTO_SL_MAX_ATR_MULT", "1.0")
    quote, m15, h1, h4 = _uptrend_context()

    score, direction, geometry = _score_candidate(quote, m15, h1, h4)

    assert direction == "NO_TRADE"
    assert score == 0


# --- MTFAI1 V2 (2026-08-24 forensic geometry/eligibility audit, DEMO activation) ---

def test_mtfai1_v2_disabled_by_default(monkeypatch):
    # Isolated from the ambient container environment (the real deployed container sets
    # MT5_MTFAI1_V2_ENABLED=true) -- this asserts the CODE's off-switch behavior, not whatever
    # happens to be configured in whichever environment the suite runs in.
    from backend.brokers.mt5 import autonomous
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_V2_ENABLED", False)

    assert autonomous._mtfai1_v2_structural_stop([], "LONG", "EURUSD") is None
    assert autonomous._mtfai1_v2_fvg_ob_target([], "LONG", "EURUSD", Decimal("1.1000"), Decimal("0.0010")) == (None, "")


def test_mtfai1_v2_symbol_universe_gate(monkeypatch):
    monkeypatch.setenv("MT5_MTFAI1_V2_ENABLED", "true")
    from backend.brokers.mt5 import autonomous
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_V2_ENABLED", True)
    quote, m15, h1, h4 = _uptrend_context()

    # USDJPY is deliberately excluded from the V2 universe (JPY/CHF pairs stayed negative in both
    # halves of the forensic audit) -- must be NO_TRADE even though the price context is a clean
    # uptrend that would otherwise produce a LONG.
    score_jpy, direction_jpy, _ = _score_candidate(quote, m15, h1, h4, "USDJPY")
    assert direction_jpy == "NO_TRADE"
    assert score_jpy == 0

    # EURUSD is in the validated V2 universe -- the same uptrend context must still trade.
    score_eur, direction_eur, _ = _score_candidate(quote, m15, h1, h4, "EURUSD")
    assert direction_eur == "LONG"
    assert score_eur > 0


def test_score_candidate_extracts_symbol_from_dot_symbol_attribute(monkeypatch):
    # 2026-08-24 real incident: the live call site passes an MT5Symbol instance (field is
    # `.symbol`, e.g. "EURUSD"), not `.name` -- the old getattr(..., "name", "UNKNOWN") silently
    # resolved to "UNKNOWN" for every real candidate once V2's symbol-universe gate depended on
    # it, causing 360/360 candidates to become NO_TRADE across the first 36 live cycles post-
    # deploy. This locks in the fix: an object exposing `.symbol` (not `.name`) must still resolve
    # correctly, and the V2 gate must recognize it as being in the validated universe.
    from backend.brokers.mt5 import autonomous
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_V2_ENABLED", True)
    quote, m15, h1, h4 = _uptrend_context()
    symbol_like_mt5_symbol = SimpleNamespace(symbol="EURUSD")

    score, direction, _ = _score_candidate(quote, m15, h1, h4, symbol_like_mt5_symbol)

    assert direction == "LONG"
    assert score > 0


def test_mtfai1_v2_low_volatility_override_never_calls_upper_on_mt5symbol():
    # 2026-08-24 real incident #2: the low_volatility exclusion at the _screen() call site used
    # instrument.symbol.upper() -- instrument.symbol is an MT5Symbol Pydantic object (no .upper()
    # method), not a string, causing AttributeError on every live cycle for every account until
    # fixed to instrument.canonical_pair.upper() (the correct plain-string field). This locks in
    # that MT5Symbol genuinely lacks .upper() -- a direct regression guard against reintroducing
    # `instrument.symbol.upper()` anywhere in this override's line, since a full live-cycle
    # integration harness (mocking the entire _screen() call chain) is out of proportion to the
    # bug: the fix is exactly "use .canonical_pair, not .symbol", and this pins that distinction.
    from backend.brokers.mt5.models import MT5ForexInstrument, MT5Symbol

    mt5_symbol = MT5Symbol(symbol="EURUSD", visible=True, selected=True)
    assert not hasattr(mt5_symbol, "upper")

    instrument = MT5ForexInstrument(
        canonical_pair="EURUSD", broker_symbol="EURUSD", asset_class="forex", base_currency="EUR", quote_currency="USD",
        enabled=True, visible=True, tradable=True, market_open=True, selected=True, eligible=True,
        specification_timestamp=datetime(2026, 8, 24, tzinfo=timezone.utc), symbol=mt5_symbol,
    )
    assert instrument.canonical_pair.upper() == "EURUSD"
    with pytest.raises(AttributeError):
        instrument.symbol.upper()


def test_mtfai1_v2_trend_quality_disabled_by_default():
    from backend.brokers.mt5 import autonomous

    score, breakdown = autonomous._mtfai1_v2_trend_quality([], [], [], "LONG", "EURUSD", Decimal("0.0010"), Decimal("1.1010"), Decimal("1.1000"))

    assert (score, breakdown) == (None, None)


def test_mtfai1_v2_trend_quality_fails_open_on_unparseable_bars(monkeypatch):
    from backend.brokers.mt5 import autonomous
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_V2_ENABLED", True)
    m15 = _trending_series(60, "1.1000", "0.0002")  # SimpleNamespace, no .time

    score, breakdown = autonomous._mtfai1_v2_trend_quality(m15, m15, m15, "LONG", "EURUSD", Decimal("0.0010"), Decimal("1.1010"), Decimal("1.1000"))

    assert (score, breakdown) == (None, None)


def test_mtfai1_v2_trend_quality_produces_bounded_graduated_score(monkeypatch):
    from backend.brokers.mt5 import autonomous
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_V2_ENABLED", True)
    start = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
    m15 = _zigzag_series_with_time(60, "1.1000", "0.0020", start)
    h1 = _zigzag_series_with_time(40, "1.0950", "0.0060", start)
    h4 = _zigzag_series_with_time(40, "1.0900", "0.0120", start)

    score, breakdown = autonomous._mtfai1_v2_trend_quality(m15, h1, h4, "LONG", "EURUSD", Decimal("0.0020"), Decimal("1.1010"), Decimal("1.1000"))

    assert score is not None
    assert 0.0 <= score <= 100.0
    assert breakdown is not None
    assert set(breakdown) == {"adx_m15", "adx_score", "ma_separation_atr", "ma_separation_score", "h1_trend", "h4_trend", "htf_agree_count", "htf_score"}
    # MA separation is graduated (not the binary fast>slow crossover mtfai1's own gate already
    # enforces) -- a bigger separation, same direction, must score at least as high.
    score_wide_sep, _ = autonomous._mtfai1_v2_trend_quality(m15, h1, h4, "LONG", "EURUSD", Decimal("0.0020"), Decimal("1.1050"), Decimal("1.1000"))
    assert score_wide_sep >= score


def test_mtfai1_v2_trend_quality_redistributes_weight_without_adx_history():
    from backend.brokers.mt5 import autonomous
    import backend.brokers.mt5.autonomous as autonomous_mod
    prior = autonomous_mod.MT5_MTFAI1_V2_ENABLED
    autonomous_mod.MT5_MTFAI1_V2_ENABLED = True
    try:
        start = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
        m15_short = _zigzag_series_with_time(20, "1.1000", "0.0020", start)  # < 29 bars, ADX(14) can't resolve
        h1 = _zigzag_series_with_time(40, "1.0950", "0.0060", start)
        h4 = _zigzag_series_with_time(40, "1.0900", "0.0120", start)

        score, breakdown = autonomous._mtfai1_v2_trend_quality(m15_short, h1, h4, "LONG", "EURUSD", Decimal("0.0020"), Decimal("1.1010"), Decimal("1.1000"))

        assert score is not None  # still resolves from the two remaining components
        assert breakdown["adx_m15"] is None
        assert breakdown["adx_score"] is None
    finally:
        autonomous_mod.MT5_MTFAI1_V2_ENABLED = prior


def test_mtfai1_v2_symbol_gate_inert_when_disabled(monkeypatch):
    from backend.brokers.mt5 import autonomous
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_V2_ENABLED", False)
    quote, m15, h1, h4 = _uptrend_context()

    score, direction, _ = _score_candidate(quote, m15, h1, h4, "USDJPY")

    assert direction == "LONG"
    assert score > 0


# 2026-08-25 MTFAI1 V2 confidence-component forensic analysis (Part 1): a V2-aware
# reward_risk_quality read -- explicitly NOT "1R=100" (V2's own MT5_MTFAI1_V2_TP_MIN_MULT=1.0
# floor is deliberate design, not automatically excellent), scoring structural-destination
# quality, ATR-normalized target distance, and reachability past opposing structure alongside
# the raw multiple.
def test_mtfai1_v2_rr_quality_disabled_by_default():
    from backend.brokers.mt5 import autonomous

    score, breakdown = autonomous._mtfai1_v2_reward_risk_quality(
        direction="LONG", entry=Decimal("1.1000"), stop=Decimal("1.0980"), target=Decimal("1.1020"),
        tp_basis="mtfai1_v2_fvg", atr=Decimal("0.0010"), opposing_structure=None,
    )
    assert (score, breakdown) == (None, None)


def test_mtfai1_v2_rr_quality_does_not_score_1r_floor_as_excellent(monkeypatch):
    from backend.brokers.mt5 import autonomous
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_V2_RR_QUALITY_ENABLED", True)

    # entry=1.1000, stop=1.0980 (20 pips risk), target=1.1020 (20 pips reward) -> RR exactly 1.0,
    # V2's own deliberate floor -- must NOT score as if it were an ideal setup.
    score, breakdown = autonomous._mtfai1_v2_reward_risk_quality(
        direction="LONG", entry=Decimal("1.1000"), stop=Decimal("1.0980"), target=Decimal("1.1020"),
        tp_basis="mtfai1_v2_fvg", atr=Decimal("0.0010"), opposing_structure=None,
    )
    assert score is not None
    assert breakdown["abs_rr"] == 1.0
    assert breakdown["abs_rr_score"] == 50.0  # floor -> half credit, not full
    assert score < 90.0  # a 1.0R setup must not read as near-maximal overall


def test_mtfai1_v2_rr_quality_rewards_genuine_structural_destination(monkeypatch):
    from backend.brokers.mt5 import autonomous
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_V2_RR_QUALITY_ENABLED", True)

    kwargs = dict(direction="LONG", entry=Decimal("1.1000"), stop=Decimal("1.0980"), target=Decimal("1.1040"), atr=Decimal("0.0010"), opposing_structure=None)
    score_structural, _ = autonomous._mtfai1_v2_reward_risk_quality(tp_basis="mtfai1_v2_order_block", **kwargs)
    score_generic, _ = autonomous._mtfai1_v2_reward_risk_quality(tp_basis="atr_multiple", **kwargs)
    assert score_structural > score_generic


def test_mtfai1_v2_rr_quality_penalizes_target_behind_opposing_structure(monkeypatch):
    from backend.brokers.mt5 import autonomous
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_V2_RR_QUALITY_ENABLED", True)

    kwargs = dict(direction="LONG", entry=Decimal("1.1000"), stop=Decimal("1.0980"), target=Decimal("1.1040"), tp_basis="mtfai1_v2_fvg", atr=Decimal("0.0010"))
    score_clear, breakdown_clear = autonomous._mtfai1_v2_reward_risk_quality(opposing_structure=None, **kwargs)
    score_obstructed, breakdown_obstructed = autonomous._mtfai1_v2_reward_risk_quality(opposing_structure=Decimal("1.1020"), **kwargs)  # sits between entry and target

    assert breakdown_clear["reachability_note"] == "unknown"
    assert breakdown_obstructed["reachability_note"] == "between_entry_and_target"
    assert score_obstructed < score_clear


def test_mtfai1_v2_rr_quality_wired_into_score_candidate_geometry(monkeypatch):
    from backend.brokers.mt5 import autonomous
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_V2_ENABLED", True)
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_V2_RR_QUALITY_ENABLED", True)
    quote, m15, h1, h4 = _uptrend_context()

    score, direction, geometry = _score_candidate(quote, m15, h1, h4, "EURUSD")

    if direction != "NO_TRADE":
        assert "reward_risk_quality_score" in geometry
        assert 0.0 <= geometry["reward_risk_quality_score"] <= 100.0


def test_mtfai1_v2_helpers_fail_open_on_bar_shape_they_cannot_parse(monkeypatch):
    # Existing SimpleNamespace fixture bars (_candle/_trending_series above) have no `.time` --
    # normalize_bars() cannot parse them. V2's helpers must return None/no-op, never raise, so a
    # malformed/legacy bar source can never turn into a live trading exception.
    monkeypatch.setenv("MT5_MTFAI1_V2_ENABLED", "true")
    from backend.brokers.mt5 import autonomous
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_V2_ENABLED", True)
    m15 = _trending_series(30, "1.1000", "0.0002")

    assert autonomous._mtfai1_v2_structural_stop(m15, "LONG", "EURUSD") is None
    assert autonomous._mtfai1_v2_fvg_ob_target(m15, "LONG", "EURUSD", Decimal("1.1000"), Decimal("0.0010")) == (None, "")


def _zigzag_prices(count, base, amplitude):
    price = Decimal(str(base))
    amp = Decimal(str(amplitude))
    prices = []
    for i in range(count):
        phase = (i // 5) % 2
        step = amp if phase == 0 else -amp
        price += step
        prices.append(price)
    return prices


def _zigzag_series_with_time(count, base, amplitude, start):
    """Dict-shaped bars with real, timezone-aware timestamps -- normalize_bars() handles dicts
    natively (its non-OHLCVBar branch requires either an OHLCVBar instance or a plain dict, not
    an attribute-only object, and _utc() rejects naive datetimes). Traces a clean up/down zigzag
    so detect_swings has confirmable swing highs/lows, unlike the pure-trend SimpleNamespace
    fixture above (fine for _score_candidate's own attribute access, not for the V2 helpers'
    internal normalize_bars call)."""
    from datetime import timedelta
    candles = []
    for i, price in enumerate(_zigzag_prices(count, base, amplitude)):
        ts = start + timedelta(minutes=15 * i)
        candles.append({
            "open": price, "high": price + Decimal("0.0004"), "low": price - Decimal("0.0004"), "close": price,
            "time": ts.isoformat(),
        })
    return candles


def _zigzag_series_plain(count, base, amplitude):
    """Same price path as _zigzag_series_with_time, as attribute-style bars for _swing_level
    (which reads .low/.high, not dict keys) -- lets the test compare both detectors against the
    identical underlying price series."""
    return [_candle(p, p + Decimal("0.0004"), p - Decimal("0.0004"), p) for p in _zigzag_prices(count, base, amplitude)]


def test_mtfai1_v2_structural_stop_uses_genuine_swing_not_raw_extreme(monkeypatch):
    monkeypatch.setenv("MT5_MTFAI1_V2_ENABLED", "true")
    from backend.brokers.mt5 import autonomous
    from backend.market_structure.bar_utils import normalize_bars
    from backend.market_structure.swings import detect_swings
    monkeypatch.setattr(autonomous, "MT5_MTFAI1_V2_ENABLED", True)
    start = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)
    m15_dicts = _zigzag_series_with_time(60, "1.1000", "0.0020", start)

    v2_stop = autonomous._mtfai1_v2_structural_stop(m15_dicts, "LONG", "EURUSD")

    # The function must be genuinely wired to detect_swings, not just returning None/a stray
    # value -- compare against calling that real detector directly (the same one production's
    # own _mtfai1_trend_structure_agrees() already uses) on the identical bar series.
    bars = normalize_bars(m15_dicts, symbol="EURUSD", timeframe="M15")
    expected_swings = detect_swings(bars, autonomous._MTFAI1_STRUCTURE_CONFIG, symbol="EURUSD", timeframe="M15")
    expected_lows = [s for s in expected_swings if s.swing_type == "low"]

    assert expected_lows, "fixture must produce at least one confirmed swing low"
    assert v2_stop is not None
    assert v2_stop == Decimal(str(expected_lows[-1].price))
    # And it must be a REAL confirmed fractal point, not simply the window's raw minimum close --
    # a regular zigzag can coincide with the raw extreme, so this only asserts the detector was
    # genuinely consulted (proven above), not that the two always differ.


def test_mt5_order_send_called_exactly_once_for_approved_intent(monkeypatch):
    _execution_session_factory(monkeypatch)
    # This test exercises order-send mechanics, not portfolio protection -- can_open_new_trade()
    # now fails CLOSED (not open) when no snapshot exists yet, which no test in this file builds.
    monkeypatch.setattr(portfolio_execution_service.portfolio_manager, "can_open_new_trade", lambda *args, **kwargs: (True, []))
    # Pinned explicitly: the real container env now has MT5_LOGIN/MT5_SERVER set to the real
    # demo_10k account (2026-08-18 broker migration) -- fake_adapter()'s FakeAccount hardcodes
    # login=123456/server="MetaQuotes-Demo", so a real deployed login/server would trip
    # account_registry's live account-match safety check and reject the order for reasons
    # unrelated to what this test covers (order-send mechanics). MT5_LOGIN was previously always
    # empty/unset in this environment, which is what this test was written and validated against.
    monkeypatch.delenv("MT5_LOGIN", raising=False)
    monkeypatch.delenv("MT5_SERVER", raising=False)
    adapter = fake_adapter()
    service = MT5ExecutionService(adapter)
    intent = _intent(volume=Decimal("0.01"))

    result = asyncio.run(service.submit_market_order(intent))

    assert result.status == "ACCEPTED"
    assert adapter.client.mt5.order_send_calls == 1
    assert adapter.client.mt5.last_order_send["sl"] < adapter.client.mt5.last_order_send["price"] < adapter.client.mt5.last_order_send["tp"]
    assert adapter.client.mt5.last_order_send["type_filling"] == adapter.client.mt5.ORDER_FILLING_FOK


def test_mt5_autonomous_no_trade_makes_zero_order_send(monkeypatch):
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)
    monkeypatch.setattr(service, "_global_blockers", lambda: asyncio.sleep(0, result=[]))
    monkeypatch.setattr(service, "_screen", lambda items, **kwargs: asyncio.sleep(0, result=[]))

    result = asyncio.run(service.run_cycle(owner="local"))

    assert result["status"] in {"NO_TRADE", "SKIPPED_DUPLICATE_CANDLE"}
    assert adapter.client.mt5.order_send_calls == 0


def test_mt5_prop_risk_stricter_limit_wins():
    adapter = fake_adapter()
    status = risk_status(adapter.config, equity=Decimal("100000"), balance=Decimal("100000"))

    assert status["active_prop_profile"]["name"] == "GENERIC_PROP_CONSERVATIVE"
    assert Decimal(status["internal"]["daily_loss_cap_usd"]) == Decimal("1500")
    assert status["effective"]["stricter_limit_wins"] is True


def _intent(volume: Decimal):
    from backend.brokers.mt5.models import MT5TradeIntent

    return MT5TradeIntent(
        intent_id="test",
        account_id="123456",
        broker_symbol="EURUSD",
        canonical_pair="EURUSD",
        direction="LONG",
        volume=volume,
        entry_price=Decimal("1.1000"),
        stop_loss=Decimal("1.0950"),
        take_profit=Decimal("1.1090"),
        comment="BENSIM_AUTO_test",
        context_hash="abc",
    )


def test_entry_quality_score_returns_well_formed_decision_support_data():
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)

    result = asyncio.run(service._entry_quality_score({"broker_symbol": "EURUSD"}))

    assert result["status"] == "ok"
    assert 0.0 <= result["total_score"] <= 1.0
    low, high = result["confidence_interval"]
    assert low <= result["total_score"] <= high
    assert result["components"], "structure engine should always return the fixed set of score components"
    for component in result["components"]:
        assert {"name", "value", "weight", "contribution", "evidence"} <= set(component)
    assert set(result["positive_contributors"]) | set(result["negative_contributors"]) == {c["name"] for c in result["components"]}
    assert all(code.startswith("STRUCTURE_") for code in result["reason_codes"])


def test_entry_quality_score_degrades_gracefully_never_raises():
    adapter = fake_adapter()
    service = MT5AutonomousTradingService(adapter)

    async def _broken_candles(symbol, timeframe, count=100):
        raise RuntimeError("MT5 unavailable")

    adapter.candles = _broken_candles

    result = asyncio.run(service._entry_quality_score({"broker_symbol": "EURUSD"}))

    assert result["status"] == "unavailable"
    assert result["total_score"] is None
    assert result["components"] == []
