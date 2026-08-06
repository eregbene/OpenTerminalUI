from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.brokers.mt5 import account_registry
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
    def __init__(self):
        self.mt5 = FakeMT5()
        self.client = FakeClient(self.mt5)

    async def terminal_status(self):
        return FakeTerminal()

    async def symbol_info(self, symbol):
        return FakeSymbol()

    async def latest_tick(self, symbol):
        return FakeQuote()

    async def mt5_account(self):
        return FakeAccount()


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


class FakeQuoteTick:
    def __init__(self, bid, ask):
        self.bid = Decimal(str(bid)) if bid is not None else None
        self.ask = Decimal(str(ask)) if ask is not None else None


def test_position_risk_converts_non_usd_quote_currency_to_usd():
    """EURJPY: raw (sl-entry)*volume*contract is JPY-denominated, not USD -- must be
    scaled by the USD/JPY conversion rate before it feeds into open_risk aggregation."""
    position = FakePosition("EURJPY", 0, 1.2, comment="BSM|MTFAI1|M15|EURJPY0951")
    position.price_open = Decimal("182.056")
    position.sl = Decimal("181.753")
    position.tp = Decimal("182.601")
    position.price_current = Decimal("182.013")

    unconverted = service._position_risk(position)  # default rate=1.0, the pre-fix behavior
    usdjpy_rate = 158.3
    converted = service._position_risk(position, usd_conversion_rate=1 / usdjpy_rate)

    assert unconverted["stop_loss_projection"] == pytest.approx(-36360.0, rel=1e-3)
    assert converted["stop_loss_projection"] == pytest.approx(-229.7, rel=1e-2)
    assert abs(converted["stop_loss_projection"]) < abs(unconverted["stop_loss_projection"])


def test_position_risk_usd_quote_pair_is_unaffected_by_rate():
    position = FakePosition("EURUSD", 0, 1.0)
    default = service._position_risk(position)
    explicit = service._position_risk(position, usd_conversion_rate=1.0)
    assert default == explicit


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
