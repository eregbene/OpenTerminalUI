from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.brokers.models import BrokerOrderCommand
from backend.brokers.mt5.autonomous import MT5AutonomousTradingService, _mt5_order_comment, _parse_decision, _score_candidate, _swing_level
from backend.brokers.mt5.adapter import MT5Adapter
from backend.brokers.mt5.client import MT5Client
from backend.brokers.mt5.config import MT5Config, mt5_config
from backend.brokers.mt5.execution import MT5ExecutionService
from backend.brokers.mt5.exceptions import MT5ReadOnlyViolation
from backend.brokers.mt5.prop_risk import risk_status


class FakeRow(SimpleNamespace):
    def _asdict(self):
        return dict(self.__dict__)


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
        return -50 * volume / 0.1 if price_close < price_open else 90 * volume / 0.1

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


def test_mt5_order_comment_encodes_strategy_and_timeframe_within_mt5_limit():
    comment = _mt5_order_comment("EUR/USD", datetime(2026, 8, 3, 17, 55, 51))

    assert comment == "BSM|MTFAI1|M15|EURUSD1755"
    assert len(comment) <= 31
    assert comment.isascii()
    assert comment.startswith("BSM|MTFAI1|M15|")


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


def test_score_candidate_uses_structure_and_atr_not_flat_one_atr():
    quote, m15, h1, h4 = _uptrend_context()

    score, direction, geometry = _score_candidate(quote, m15, h1, h4)

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
    assert geometry["stop_loss"] == geometry["entry"]


def test_mt5_order_send_called_exactly_once_for_approved_intent():
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
    monkeypatch.setattr(service, "_screen", lambda items: asyncio.sleep(0, result=[]))

    result = asyncio.run(service.run_cycle(owner="local"))

    assert result["status"] in {"SKIPPED_NO_CANDIDATE", "SKIPPED_DUPLICATE_CANDLE"}
    assert adapter.client.mt5.order_send_calls == 0


def test_mt5_ai_decision_parser_accepts_fenced_json():
    decision, confidence = _parse_decision('```json\n{"decision":"SHORT","confidence":0.87}\n```')

    assert decision == "SHORT"
    assert confidence == 0.87


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
