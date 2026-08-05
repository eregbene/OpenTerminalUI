from __future__ import annotations

from decimal import Decimal

from backend.trading.models import ExecutionModel, MarketReference, OrderSide, OrderStatus, PaperFill, PaperOrder, PaperOrderType


class PaperExecutionSimulator:
    def acknowledge(self, order: PaperOrder) -> OrderStatus:
        return OrderStatus.ACKNOWLEDGED

    def simulate_next_event_fill(
        self,
        *,
        order: PaperOrder,
        market: MarketReference,
        execution_model: ExecutionModel,
        sequence: int = 1,
    ) -> PaperFill | None:
        if order.status not in {OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED}:
            return None
        price = market.price
        if order.order_type == PaperOrderType.LIMIT:
            if order.side in {OrderSide.BUY, OrderSide.COVER} and (order.limit_price is None or market.price > order.limit_price):
                return None
            if order.side in {OrderSide.SELL, OrderSide.SHORT} and (order.limit_price is None or market.price < order.limit_price):
                return None
            price = order.limit_price or market.price
        elif order.order_type in {PaperOrderType.STOP, PaperOrderType.STOP_LIMIT}:
            if order.side in {OrderSide.BUY, OrderSide.COVER} and (order.stop_price is None or market.price < order.stop_price):
                return None
            if order.side in {OrderSide.SELL, OrderSide.SHORT} and (order.stop_price is None or market.price > order.stop_price):
                return None
            price = market.price
            if order.order_type == PaperOrderType.STOP_LIMIT and order.limit_price is not None:
                if order.side in {OrderSide.BUY, OrderSide.COVER} and price > order.limit_price:
                    return None
                if order.side in {OrderSide.SELL, OrderSide.SHORT} and price < order.limit_price:
                    return None

        direction = Decimal("1") if order.side in {OrderSide.BUY, OrderSide.COVER} else Decimal("-1")
        slippage = price * (execution_model.slippage_bps / Decimal("10000")) * direction
        spread = price * (execution_model.spread_bps / Decimal("10000")) * direction
        fill_price = max(Decimal("0"), price + slippage + spread)
        requested = order.remaining_quantity
        fill_qty = requested
        reason = "full_fill"
        if execution_model.liquidity_max_quantity is not None and execution_model.liquidity_max_quantity < requested:
            fill_qty = execution_model.liquidity_max_quantity
            reason = "partial_fill_liquidity_cap"
        if fill_qty <= 0:
            return None
        fees = fill_qty * fill_price * Decimal("0.0005")
        return PaperFill(
            order_id=order.order_id,
            account_id=order.account_id,
            deployment_id=order.deployment_id,
            instrument_id=order.instrument_id,
            side=order.side,
            quantity=fill_qty,
            price=fill_price,
            fees=fees,
            sequence=sequence,
            execution_model_id=execution_model.execution_model_id,
            execution_model_version=execution_model.version,
            liquidity_reason=reason,
        )
