from __future__ import annotations

import os
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AUTH_MIDDLEWARE_ENABLED", "0")
os.environ.setdefault("E2E_DEV_AUTH", "1")
os.environ.setdefault("IBKR_SIMULATED", "1")
os.environ.setdefault("IBKR_ACCOUNT_ALLOW_LIST", "DU1234567")

from backend.auth.deps import get_current_user  # noqa: E402
from backend.main import app  # noqa: E402
from backend.models.user import UserRole  # noqa: E402
from backend.trading.models import DeploymentStatus, InstrumentReference, MarketReference, OrderIntent, OrderSide, PaperOrderType, TimeInForce  # noqa: E402
from backend.api.routes.trading import service  # noqa: E402
from backend.brokers.ibkr.orders import order_book  # noqa: E402


class DummyUser:
    id = "broker_user"
    role = UserRole.ADMIN


@pytest.fixture(autouse=True)
def broker_user_override():
    app.dependency_overrides[get_current_user] = lambda: DummyUser()
    order_book.orders.clear()
    order_book.executions.clear()
    yield
    app.dependency_overrides.pop(get_current_user, None)


def _client() -> TestClient:
    return TestClient(app)


def test_ibkr_read_only_connection_contract_data_and_account() -> None:
    client = _client()
    health = client.post("/api/brokers/ibkr/connect")
    assert health.status_code == 200
    assert health.json()["account_verification_status"] == "PAPER_VERIFIED"
    assert health.json()["order_submission_status"] == "ENABLED"

    contract = client.post("/api/brokers/ibkr/contracts/resolve", json={"instrument_id": "FX:EURUSD"})
    assert contract.status_code == 200
    assert contract.json()["security_type"] == "CASH"

    quote = client.get("/api/brokers/ibkr/quotes/FX:EURUSD")
    assert quote.status_code == 200
    assert quote.json()["mode"] == "DELAYED"

    bars = client.post("/api/brokers/ibkr/historical-bars", json={"instrument_id": "FX:EURUSD", "bar_size": "15 mins"})
    assert bars.status_code == 200
    assert len(bars.json()["items"]) == 64

    accounts = client.get("/api/brokers/ibkr/accounts")
    assert accounts.status_code == 200
    assert accounts.json()["items"][0]["paper_verified"] is True


def test_unsupported_contract_and_live_account_are_blocked(monkeypatch) -> None:
    client = _client()
    assert client.post("/api/brokers/ibkr/contracts/resolve", json={"instrument_id": "AMBIG:ABC"}).status_code == 422

    monkeypatch.setattr("backend.brokers.ibkr.client.ibkr_adapter.config.account_allow_list", ["U1234567"])
    health = client.post("/api/brokers/ibkr/connect")
    assert health.json()["account_verification_status"] == "BROKER_ENVIRONMENT_UNVERIFIED"
    monkeypatch.setattr("backend.brokers.ibkr.client.ibkr_adapter.config.account_allow_list", ["DU1234567"])


def test_oms_only_paper_order_submission_to_ibkr(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(service, "store", service.store.__class__(tmp_path / "trading"))
    client = _client()
    account = client.post("/api/trading/paper/accounts", json={"name": "IBKR Paper", "initial_cash": "100000", "base_currency": "USD"}).json()
    deployment = client.post(
        "/api/trading/paper/deployments",
        json={
            "account_id": account["account_id"],
            "candidate_id": "cand_1",
            "strategy_id": "strat_1",
            "strategy_version": "1.0.0",
            "instrument": {"instrument_id": "AAPL", "symbol": "AAPL", "asset_class": "EQUITY"},
        },
    ).json()
    client.post(f"/api/trading/paper/deployments/{deployment['deployment_id']}/approve", json={"approver": "user", "strategy_hash": "s", "candidate_hash": "c"})
    enabled = service.get_deployment(deployment["deployment_id"])
    enabled.status = DeploymentStatus.ENABLED
    service.store.upsert_deployment(enabled)
    intent = OrderIntent(
        account_id=account["account_id"],
        strategy_id="strat_1",
        strategy_version="1.0.0",
        deployment_id=deployment["deployment_id"],
        proposal_id="prop_1",
        instrument=InstrumentReference(instrument_id="AAPL", symbol="AAPL", asset_class="EQUITY"),
        side=OrderSide.BUY,
        order_type=PaperOrderType.MARKET,
        requested_quantity=Decimal("1"),
        reference_price=Decimal("100"),
        invalidation_price=Decimal("95"),
        time_in_force=TimeInForce.DAY,
        idempotency_key=f"idem_phase11_{uuid4().hex[:12]}",
    )
    submitted = client.post("/api/trading/paper/intents", json={"intent": intent.model_dump(mode="json"), "market": MarketReference(price=Decimal("100")).model_dump(mode="json")})
    assert submitted.status_code == 200
    order_id = submitted.json()["order"]["order_id"]
    broker = client.post(f"/api/trading/paper/orders/{order_id}/submit-to-broker", json={"broker": "ibkr", "broker_account_id": "DU1234567", "user_confirmed": True})
    assert broker.status_code == 200
    assert broker.json()["broker_receipt"]["order"]["state"] == "FILLED"
    status = client.get(f"/api/trading/paper/orders/{order_id}/broker-status")
    assert status.json()["broker_order"]["broker_order_id"]
