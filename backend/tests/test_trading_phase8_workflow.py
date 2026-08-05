from __future__ import annotations

from decimal import Decimal

from backend.trading.models import InstrumentReference, MarketReference, OrderIntent, OrderSide, OrderStatus, PaperOrderType
from backend.trading.oms.state_machine import assert_transition
from backend.trading.persistence import TradingStore
from backend.trading.services import TradingControlService


def _service(tmp_path):
    return TradingControlService(store=TradingStore(tmp_path / "trading"))


def _instrument() -> InstrumentReference:
    return InstrumentReference(
        instrument_id="NSE:RELIANCE",
        symbol="RELIANCE",
        asset_class="EQUITY",
        quote_currency="USD",
        minimum_tick=Decimal("0.01"),
        lot_size=Decimal("1"),
        contract_multiplier=Decimal("1"),
        market_open=True,
    )


def _market(price: str = "100") -> MarketReference:
    return MarketReference(price=Decimal(price), provider="pytest", quality_score=Decimal("1"), is_simulated=True)


def _intent(account_id: str, deployment_id: str, key: str = "idem-1") -> OrderIntent:
    return OrderIntent(
        account_id=account_id,
        strategy_id="strategy_1",
        strategy_version="1.0.0",
        deployment_id=deployment_id,
        proposal_id="proposal_1",
        instrument=_instrument(),
        side=OrderSide.BUY,
        order_type=PaperOrderType.MARKET,
        requested_quantity=Decimal("10"),
        sizing_intent={"type": "fixed_units", "quantity": "10"},
        reference_price=Decimal("100"),
        invalidation_price=Decimal("95"),
        idempotency_key=key,
    )


def test_phase8_requires_human_approval_before_order(tmp_path):
    service = _service(tmp_path)
    account = service.create_account("Test", Decimal("100000"))
    deployment = service.create_deployment(
        account_id=account.account_id,
        candidate_id="cand_1",
        strategy_id="strategy_1",
        strategy_version="1.0.0",
        instrument=_instrument(),
    )

    blocked = service.submit_intent(_intent(account.account_id, deployment.deployment_id), _market())
    assert blocked["order"] is None
    assert blocked["risk_evaluation"].decision in {"REJECTED", "BLOCKED"}

    approved = service.approve_deployment(deployment.deployment_id, "human", "ok", "strategy_hash", "candidate_hash")
    result = service.submit_intent(_intent(account.account_id, approved.deployment_id), _market())
    assert result["risk_evaluation"].decision == "APPROVED"
    assert result["order"] is not None


def test_phase8_duplicate_intent_does_not_create_second_order(tmp_path):
    service = _service(tmp_path)
    account = service.create_account("Test", Decimal("100000"))
    deployment = service.create_deployment(account_id=account.account_id, candidate_id="cand_1", strategy_id="strategy_1", strategy_version="1.0.0", instrument=_instrument())
    service.approve_deployment(deployment.deployment_id, "human", "ok", "strategy_hash", "candidate_hash")

    first = service.submit_intent(_intent(account.account_id, deployment.deployment_id), _market())
    second = service.submit_intent(_intent(account.account_id, deployment.deployment_id), _market())

    assert first["order"] is not None
    assert second["order"].order_id == first["order"].order_id
    assert len(service.list_orders(account.account_id)) == 1


def test_phase8_simulated_fill_updates_ledger_snapshot_and_reconciliation(tmp_path):
    service = _service(tmp_path)
    account = service.create_account("Test", Decimal("100000"))
    deployment = service.create_deployment(account_id=account.account_id, candidate_id="cand_1", strategy_id="strategy_1", strategy_version="1.0.0", instrument=_instrument())
    service.approve_deployment(deployment.deployment_id, "human", "ok", "strategy_hash", "candidate_hash")
    result = service.submit_intent(_intent(account.account_id, deployment.deployment_id), _market("100"))

    simulated = service.simulate_order(result["order"].order_id, _market("101"))
    assert simulated["fill"] is not None
    assert simulated["order"].status == "FILLED"

    snapshot = service.snapshot(account.account_id)
    assert snapshot.positions[0].quantity == Decimal("10")
    assert snapshot.account.cash_balance < Decimal("100000")
    assert service.reconcile(account.account_id).status == "PASS"


def test_phase8_emergency_and_stale_deployment_block_new_entries(tmp_path):
    service = _service(tmp_path)
    account = service.create_account("Test", Decimal("100000"))
    deployment = service.create_deployment(account_id=account.account_id, candidate_id="cand_1", strategy_id="strategy_1", strategy_version="1.0.0", instrument=_instrument())
    service.approve_deployment(deployment.deployment_id, "human", "ok", "strategy_hash", "candidate_hash")

    service.set_emergency_disable(account.account_id, True, "test")
    blocked = service.submit_intent(_intent(account.account_id, deployment.deployment_id, "emergency"), _market())
    assert blocked["order"] is None
    assert "Emergency disable is enabled" in blocked["risk_evaluation"].blocking_reasons

    service.set_emergency_disable(account.account_id, False, "clear")
    service.mark_deployment_stale(deployment.deployment_id, ["candidate stale"])
    stale = service.submit_intent(_intent(account.account_id, deployment.deployment_id, "stale"), _market())
    assert stale["order"] is None
    assert any("stale" in reason.lower() for reason in stale["risk_evaluation"].blocking_reasons)


def test_phase8_invalid_oms_transition_fails_explicitly():
    try:
        assert_transition(OrderStatus.FILLED, OrderStatus.APPROVED)
    except Exception as exc:
        assert "Invalid order transition" in str(exc)
    else:
        raise AssertionError("invalid transition was accepted")
