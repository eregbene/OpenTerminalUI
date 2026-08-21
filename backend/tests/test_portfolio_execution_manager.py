from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.brokers.mt5 import account_registry
from backend.brokers.mt5.config import MT5Config
from backend.portfolio_execution import service
from backend.portfolio_execution.orm import ExecutionOrderORM, ExecutionStateTransitionORM
from backend.portfolio_execution.service import execution_manager, portfolio_manager
from backend.shared.db import Base


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


class FakeTerminal:
    account_mode = "DEMO"
    trade_allowed = True
    external_python_trading_allowed = True


class FakeSymbol:
    trade_mode = 0
    volume_min = Decimal("0.01")


class FakeQuote:
    spread = Decimal("1")
    bid = Decimal("1.10095")
    ask = Decimal("1.10115")


class FakeMT5:
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010
    TRADE_RETCODE_PLACED = 10008

    def __init__(self):
        self.calls = 0

    def order_send(self, request):
        self.calls += 1

        class Result:
            def _asdict(self):
                return {"retcode": 10009, "order": 101, "deal": 202, "volume": request["volume"], "price": request.get("price"), "comment": "Request executed"}

        return Result()


class FakeClient:
    def __init__(self, mt5):
        self.mt5 = mt5

    def ensure_ready(self):
        return self.mt5


class FakeAccount:
    login = 123456
    server = "Bensim-Demo"
    company = "Bensim"
    currency = "USD"


class FakeAdapter:
    def __init__(self, account_id="unit_test", login=123456, server="Bensim-Demo"):
        self.mt5 = FakeMT5()
        self.client = FakeClient(self.mt5)
        self.config = MT5Config(account_id=account_id, account_mode="DEMO")
        self.account = type("ScopedFakeAccount", (), {"login": login, "server": server, "company": "Bensim", "currency": "USD", "trade_mode": 0})()

    async def terminal_status(self):
        return FakeTerminal()

    async def symbol_info(self, symbol):
        return FakeSymbol()

    async def latest_tick(self, symbol):
        return FakeQuote()

    async def mt5_account(self):
        return self.account


def _session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(service, "SessionLocal", SessionLocal)
    monkeypatch.setattr(account_registry, "SessionLocal", SessionLocal)
    return SessionLocal


def test_currency_exposure_offsets_crosses():
    positions = [
        FakePosition("EURUSD", 0, 1.0),
        FakePosition("GBPUSD", 0, 2.0),
        FakePosition("EURJPY", 1, 0.5),
    ]

    exposure = service._exposure(positions)

    assert exposure["currency"]["EUR"]["net"] == 0.5
    assert exposure["currency"]["USD"]["net"] == -3.0
    assert exposure["currency"]["JPY"]["net"] == 0.5


def _risk_row(stop_loss_projection: float) -> dict:
    return {"stop_loss_projection": stop_loss_projection}


# --- Priority 5.5: dollar-risk-weighted currency exposure (the hard MAX_CORRELATED_EXPOSURE
# blocker's fix) -- was comparing raw lot-signed exposure against an unconfigured, effectively-
# infinite default, making it permanently dormant. -----------------------------------------


def test_currency_risk_exposure_weights_by_dollar_risk_not_lots():
    # A tiny-lot, wide-stop position can carry MORE real dollar risk than a large-lot,
    # tight-stop one -- the OLD lot-based check couldn't tell these apart at all.
    positions = [FakePosition("EURUSD", 0, 0.1), FakePosition("GBPUSD", 0, 5.0)]
    risk_rows = [_risk_row(-500.0), _risk_row(-50.0)]

    exposure = service._currency_risk_exposure(positions, risk_rows)

    assert exposure["USD"] == pytest.approx(-550.0)
    assert exposure["EUR"] == pytest.approx(500.0)
    assert exposure["GBP"] == pytest.approx(50.0)


def test_currency_risk_exposure_offsetting_directions_net_down():
    positions = [FakePosition("EURUSD", 0, 1.0), FakePosition("EURUSD", 1, 1.0)]
    risk_rows = [_risk_row(-100.0), _risk_row(-100.0)]

    exposure = service._currency_risk_exposure(positions, risk_rows)

    assert exposure["EUR"] == pytest.approx(0.0)
    assert exposure["USD"] == pytest.approx(0.0)


def test_portfolio_protection_blocks_eurusd_long_plus_gbpusd_long_concentration(monkeypatch):
    _session_factory(monkeypatch)
    positions = [FakePosition("EURUSD", 0, 1.0), FakePosition("GBPUSD", 0, 1.0)]
    risk_rows = [_risk_row(-100.0), _risk_row(-100.0)]  # both LONG -> both short $200 net USD
    exposure = service._exposure(positions)

    state = portfolio_manager.protection_from_values(positions=positions, account={"equity": 10000, "margin": 100, "balance": 10000, "free_margin": 9900}, risk_rows=risk_rows, exposure=exposure)

    # cap = 10,000 * 1.125% = $112.50; net USD risk = -$200 exceeds it.
    assert "MAX_CORRELATED_EXPOSURE" in state["blockers"]
    assert state["new_entries_allowed"] is False


def test_portfolio_protection_blocks_eurusd_long_plus_usdchf_short_concentration(monkeypatch):
    _session_factory(monkeypatch)
    # EURUSD LONG (+EUR,-USD) and USDCHF SHORT (-USD,+CHF) are BOTH short-USD bets -- the
    # user's own explicit example of a same-currency-factor concentration that must not slip
    # through just because the two symbols don't share a literal ticker.
    positions = [FakePosition("EURUSD", 0, 1.0), FakePosition("USDCHF", 1, 1.0)]
    risk_rows = [_risk_row(-100.0), _risk_row(-100.0)]
    exposure = service._exposure(positions)

    state = portfolio_manager.protection_from_values(positions=positions, account={"equity": 10000, "margin": 100, "balance": 10000, "free_margin": 9900}, risk_rows=risk_rows, exposure=exposure)

    assert "MAX_CORRELATED_EXPOSURE" in state["blockers"]


def test_portfolio_protection_blocks_multiple_jpy_cross_same_factor_exposure(monkeypatch):
    _session_factory(monkeypatch)
    # EURJPY LONG + GBPJPY LONG: both short-JPY bets via the shared quote currency.
    positions = [FakePosition("EURJPY", 0, 1.0), FakePosition("GBPJPY", 0, 1.0)]
    risk_rows = [_risk_row(-100.0), _risk_row(-100.0)]
    exposure = service._exposure(positions)

    state = portfolio_manager.protection_from_values(positions=positions, account={"equity": 10000, "margin": 100, "balance": 10000, "free_margin": 9900}, risk_rows=risk_rows, exposure=exposure)

    assert "MAX_CORRELATED_EXPOSURE" in state["blockers"]


def test_portfolio_protection_allows_diversified_currency_risk_under_cap(monkeypatch):
    _session_factory(monkeypatch)
    # Different currencies entirely, modest risk each -- must NOT be blocked just because
    # multiple positions exist.
    positions = [FakePosition("EURUSD", 0, 1.0), FakePosition("USDJPY", 1, 1.0)]
    risk_rows = [_risk_row(-25.0), _risk_row(-25.0)]
    exposure = service._exposure(positions)

    state = portfolio_manager.protection_from_values(positions=positions, account={"equity": 10000, "margin": 100, "balance": 10000, "free_margin": 9900}, risk_rows=risk_rows, exposure=exposure)

    assert "MAX_CORRELATED_EXPOSURE" not in state["blockers"]
    assert state["new_entries_allowed"] is True


def test_portfolio_protection_correlated_cap_scales_with_equity(monkeypatch):
    # Same $200 net-USD risk that blocked a $10,000 account must NOT block a $100,000 one --
    # the whole point of the equity-scaled fix (mirrors the pre-existing MAX_TOTAL_OPEN_RISK fix).
    _session_factory(monkeypatch)
    positions = [FakePosition("EURUSD", 0, 1.0), FakePosition("GBPUSD", 0, 1.0)]
    risk_rows = [_risk_row(-100.0), _risk_row(-100.0)]
    exposure = service._exposure(positions)

    state = portfolio_manager.protection_from_values(positions=positions, account={"equity": 100000, "margin": 1000, "balance": 100000, "free_margin": 99000}, risk_rows=risk_rows, exposure=exposure)

    assert "MAX_CORRELATED_EXPOSURE" not in state["blockers"]


# --- Priority 5.5: cross-account race-condition mitigation (refresh_account) ----------------


def test_refresh_account_routes_to_the_correct_per_account_service(monkeypatch):
    calls = []

    class _FakeService:
        async def refresh(self):
            calls.append("refreshed")
            return {"status": "ok"}

    fake_service = _FakeService()
    monkeypatch.setattr(portfolio_manager, "_enabled_services", lambda: {"ftmo_demo_25k": fake_service})

    result = service.asyncio.run(portfolio_manager.refresh_account("ftmo_demo_25k"))

    assert calls == ["refreshed"]
    assert result == {"status": "ok"}


def test_refresh_account_returns_none_for_an_account_with_no_service(monkeypatch):
    monkeypatch.setattr(portfolio_manager, "_enabled_services", lambda: {})
    monkeypatch.setattr(portfolio_manager, "_services", {})

    result = service.asyncio.run(portfolio_manager.refresh_account("unknown_account"))

    assert result is None


def test_portfolio_protection_blocks_new_entries_on_open_position_limit(monkeypatch):
    _session_factory(monkeypatch)
    monkeypatch.setenv("MT5_MAX_OPEN_POSITIONS", "1")
    positions = [FakePosition("EURUSD", 0, 1.0)]
    exposure = service._exposure(positions)

    state = portfolio_manager.protection_from_values(positions=positions, account={"equity": 100000, "margin": 1000, "balance": 100000, "free_margin": 99000}, risk_rows=[], exposure=exposure)

    assert "MAX_OPEN_POSITIONS" in state["blockers"]
    assert state["new_entries_allowed"] is False


def test_execution_manager_journals_state_and_prevents_duplicates(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setenv("MT5_LIVE_TRADING_ENABLED", "false")
    adapter = FakeAdapter()
    request = {"action": 1, "symbol": "EURUSD", "volume": 1.0, "price": 1.1}

    first = service.asyncio.run(execution_manager.submit_mt5_request(adapter=adapter, request=request, idempotency_key="KEY", source="adaptive_trade_manager", expected_price=1.1))
    second = service.asyncio.run(execution_manager.submit_mt5_request(adapter=adapter, request=request, idempotency_key="KEY", source="adaptive_trade_manager", expected_price=1.1))

    with SessionLocal() as db:
        orders = db.query(ExecutionOrderORM).all()
        transitions = db.query(ExecutionStateTransitionORM).all()
    assert first["retcode"] == 10009
    assert second["duplicate"] is True
    assert adapter.mt5.calls == 1
    assert len(orders) == 1
    assert {row.to_state for row in transitions} >= {"PENDING", "SUBMITTED", "ACCEPTED"}


def test_spread_paid_captured_from_fresh_tick_at_submission(monkeypatch):
    """Phase 22 (corpus-expansion-throughput directive): spread_paid used to be read from
    request.get("spread_paid") -- request is the raw MT5 order_send payload, which no caller
    ever populates with that key, so the column was always NULL. Now a fresh tick is fetched via
    adapter.latest_tick() immediately before submission and the real bid/ask spread is
    persisted."""
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setenv("MT5_LIVE_TRADING_ENABLED", "false")
    adapter = FakeAdapter()
    request = {"action": 1, "symbol": "EURUSD", "volume": 1.0, "price": 1.1}

    service.asyncio.run(execution_manager.submit_mt5_request(adapter=adapter, request=request, idempotency_key="SPREAD_KEY", source="adaptive_trade_manager", expected_price=1.1))

    with SessionLocal() as db:
        row = db.query(ExecutionOrderORM).filter(ExecutionOrderORM.idempotency_key == "SPREAD_KEY").one()
    assert row.spread_paid is not None
    assert row.spread_paid == pytest.approx(float(FakeQuote.ask - FakeQuote.bid))


def test_spread_paid_stays_null_on_tick_fetch_failure_never_fabricated(monkeypatch):
    """If the fresh tick fetch specifically for spread_paid fails, spread_paid must stay None --
    never fabricated from a cached/historical value or a guess. _preflight() ALSO calls
    latest_tick() (a pre-existing, unrelated spread-sanity check) -- that first call must keep
    succeeding, or the order gets rejected before ever reaching order_send at all; only the
    SECOND call (this fix's own fresh-tick fetch, immediately before submission) fails here, to
    isolate the property under test."""
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setenv("MT5_LIVE_TRADING_ENABLED", "false")
    adapter = FakeAdapter()

    call_count = {"n": 0}
    real_tick = adapter.latest_tick

    async def _tick_fails_on_second_call(symbol):
        call_count["n"] += 1
        if call_count["n"] >= 2:
            raise RuntimeError("tick unavailable")
        return await real_tick(symbol)

    monkeypatch.setattr(adapter, "latest_tick", _tick_fails_on_second_call)
    request = {"action": 1, "symbol": "EURUSD", "volume": 1.0, "price": 1.1}

    result = service.asyncio.run(execution_manager.submit_mt5_request(adapter=adapter, request=request, idempotency_key="SPREAD_FAIL_KEY", source="adaptive_trade_manager", expected_price=1.1))

    assert result["retcode"] == 10009  # order submission itself is unaffected by the second tick-fetch failure
    assert call_count["n"] >= 2  # confirms the spread-capture call site was actually reached
    with SessionLocal() as db:
        row = db.query(ExecutionOrderORM).filter(ExecutionOrderORM.idempotency_key == "SPREAD_FAIL_KEY").one()
    assert row.spread_paid is None


class FakeQuoteTick:
    def __init__(self, bid, ask):
        self.bid = Decimal(str(bid)) if bid is not None else None
        self.ask = Decimal(str(ask)) if ask is not None else None


def test_position_risk_converts_non_usd_quote_currency_to_usd():
    """EURJPY: raw (sl-entry)*volume*contract is JPY-denominated, not USD -- must be
    scaled by the USD/JPY conversion rate before it feeds into open_risk aggregation.
    No symbol_info passed -- exercises the legacy-fallback money-per-point path (PART 3's
    _money_per_point), which preserves the exact historical 100_000-contract numbers this test
    was written against when no live broker metadata is available."""
    position = FakePosition("EURJPY", 0, 1.2, comment="BSM|MTFAI1|M15|EURJPY0951")
    position.price_open = Decimal("182.056")
    position.sl = Decimal("181.753")
    position.tp = Decimal("182.601")
    position.price_current = Decimal("182.013")

    unconverted = service.asyncio.run(service._position_risk(position))  # default rate=1.0, the pre-fix behavior
    usdjpy_rate = 158.3
    converted = service.asyncio.run(service._position_risk(position, usd_conversion_rate=1 / usdjpy_rate))

    assert unconverted["stop_loss_projection"] == pytest.approx(-36360.0, rel=1e-3)
    assert converted["stop_loss_projection"] == pytest.approx(-229.7, rel=1e-2)
    assert abs(converted["stop_loss_projection"]) < abs(unconverted["stop_loss_projection"])


def test_position_risk_usd_quote_pair_is_unaffected_by_rate():
    position = FakePosition("EURUSD", 0, 1.0)
    default = service.asyncio.run(service._position_risk(position))
    explicit = service.asyncio.run(service._position_risk(position, usd_conversion_rate=1.0))
    assert default == explicit


def test_position_risk_uses_canonical_calculator_when_symbol_info_available():
    # With real symbol_info, _position_risk must use the SAME canonical calculator every other
    # MT5 monetary-risk call site uses (not the legacy hardcoded-contract fallback) -- for a
    # well-formed XAUUSD symbol (contract_size=100, matching tick_value) this must agree with
    # the true $100/point/lot economics, not the old contract=100 guess (which happens to be
    # correct here, but for the WRONG reason -- verified against a broker-native order_calc_profit
    # fake so a future metadata-only regression would be caught).
    from backend.brokers.mt5.models import MT5Symbol

    class _FakeMT5Native:
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1

        def order_calc_profit(self, order_type, symbol, volume, price_open, price_close):
            diff = (price_close - price_open) if order_type == self.ORDER_TYPE_BUY else (price_open - price_close)
            return diff * 100.0 * volume

    symbol_info = MT5Symbol(
        symbol="XAUUSD", visible=True, selected=True, digits=2, point=Decimal("0.01"),
        trade_tick_size=Decimal("0.01"), trade_tick_value=Decimal("1.0"), trade_tick_value_profit=Decimal("1.0"),
        trade_tick_value_loss=Decimal("1.0"), trade_contract_size=Decimal("100.0"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )
    position = FakePosition("XAUUSD", 0, 0.10)
    position.price_open = Decimal("4279.93")
    position.sl = Decimal("4256.34")
    position.tp = Decimal("4316.14")
    position.price_current = Decimal("4283.55")

    result = service.asyncio.run(service._position_risk(position, symbol_info=symbol_info, mt5_client=_FakeMT5Native()))

    # Real broker-confirmed economics for this exact trade: -$235.90 at 0.10 lot.
    assert result["stop_loss_projection"] == pytest.approx(-235.90, rel=1e-2)


async def _rate(currency, quotes):
    async def fake_latest_tick(symbol):
        return quotes.get(symbol, FakeQuoteTick(None, None))

    import backend.portfolio_execution.service as svc

    original = svc.mt5_adapter.latest_tick
    svc.mt5_adapter.latest_tick = fake_latest_tick
    try:
        return await svc._fx_conversion_rate(currency)
    finally:
        svc.mt5_adapter.latest_tick = original


def test_fx_conversion_rate_usd_is_a_noop():
    assert service.asyncio.run(_rate("USD", {})) == 1.0


def test_fx_conversion_rate_prefers_direct_quote_pair():
    rate = service.asyncio.run(_rate("GBP", {"GBPUSD": FakeQuoteTick("1.2700", "1.2702")}))
    assert rate == pytest.approx(1.2701, rel=1e-3)


def test_fx_conversion_rate_falls_back_to_inverse_pair():
    rate = service.asyncio.run(_rate("JPY", {"USDJPY": FakeQuoteTick("158.20", "158.30")}))
    assert rate == pytest.approx(1 / 158.25, rel=1e-3)


def test_fx_conversion_rate_defaults_to_one_when_unresolvable():
    rate = service.asyncio.run(_rate("ZZZ", {}))
    assert rate == 1.0


def test_live_trading_is_blocked(monkeypatch):
    _session_factory(monkeypatch)
    monkeypatch.setenv("MT5_LIVE_TRADING_ENABLED", "true")
    adapter = FakeAdapter()

    result = service.asyncio.run(execution_manager.submit_mt5_request(adapter=adapter, request={"symbol": "EURUSD", "volume": 1}, idempotency_key="LIVE", source="adaptive_trade_manager"))

    assert result["status"] == "REJECTED"
    assert result["comment"] == "LIVE_TRADING_BLOCKED"
    assert adapter.mt5.calls == 0


def test_execution_idempotency_is_account_scoped(monkeypatch):
    SessionLocal = _session_factory(monkeypatch)
    monkeypatch.setenv("MT5_LIVE_TRADING_ENABLED", "false")
    request = {"action": 1, "symbol": "EURUSD", "volume": 1.0, "price": 1.1}
    first_adapter = FakeAdapter(account_id="ftmo_demo_25k", login=250001, server="FTMO-Demo")
    second_adapter = FakeAdapter(account_id="ftmo_demo_50k", login=500001, server="FTMO-Demo")
    monkeypatch.setenv("MT5_ACCOUNT_25K_LOGIN", "250001")
    monkeypatch.setenv("MT5_ACCOUNT_25K_SERVER", "FTMO-Demo")
    monkeypatch.setenv("MT5_ACCOUNT_50K_LOGIN", "500001")
    monkeypatch.setenv("MT5_ACCOUNT_50K_SERVER", "FTMO-Demo")

    first = service.asyncio.run(execution_manager.submit_mt5_request(adapter=first_adapter, request=request, idempotency_key="SAME", source="adaptive_trade_manager"))
    second = service.asyncio.run(execution_manager.submit_mt5_request(adapter=second_adapter, request=request, idempotency_key="SAME", source="adaptive_trade_manager"))

    with SessionLocal() as db:
        orders = db.query(ExecutionOrderORM).order_by(ExecutionOrderORM.account_id).all()
    assert first["retcode"] == 10009
    assert second["retcode"] == 10009
    assert [(row.account_id, row.idempotency_key) for row in orders] == [("ftmo_demo_25k", "SAME"), ("ftmo_demo_50k", "SAME")]
    assert first_adapter.mt5.calls == 1
    assert second_adapter.mt5.calls == 1


def test_execution_revalidates_account_before_order_send(monkeypatch):
    _session_factory(monkeypatch)
    monkeypatch.setenv("MT5_ACCOUNT_25K_LOGIN", "250001")
    monkeypatch.setenv("MT5_ACCOUNT_25K_SERVER", "FTMO-Demo")
    adapter = FakeAdapter(account_id="ftmo_demo_25k", login=999999, server="FTMO-Demo")

    result = service.asyncio.run(execution_manager.submit_mt5_request(adapter=adapter, request={"symbol": "EURUSD", "volume": 1}, idempotency_key="WRONG", source="adaptive_trade_manager"))

    assert result["status"] == "REJECTED"
    assert "ACCOUNT_EXECUTION_CONTEXT_MISMATCH" in result["comment"]
    assert adapter.mt5.calls == 0


def test_redis_execution_lock_key_includes_account(monkeypatch):
    calls = []

    class _Redis:
        async def set(self, key, owner, nx=True, ex=30):
            calls.append(key)
            return True

    monkeypatch.setattr(service.redis_layer, "get_client", lambda: _Redis())
    service.asyncio.run(service.redis_layer.try_execution_lock("SAME", account_id="ftmo_demo_25k"))
    service.asyncio.run(service.redis_layer.try_execution_lock("SAME", account_id="ftmo_demo_50k"))

    assert calls == [
        "mt5:account:ftmo_demo_25k:execution-lock:SAME",
        "mt5:account:ftmo_demo_50k:execution-lock:SAME",
    ]
