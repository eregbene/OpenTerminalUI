from __future__ import annotations

from decimal import Decimal

from backend.trading.models import LedgerEntry, OrderSide, PaperAccount, PaperFill, Position, utcnow


class PortfolioLedger:
    def entries_for_fill(self, fill: PaperFill, currency: str) -> list[LedgerEntry]:
        signed_qty = fill.quantity if fill.side in {OrderSide.BUY, OrderSide.COVER} else -fill.quantity
        cash_delta = -(fill.quantity * fill.price + fill.fees) if fill.side in {OrderSide.BUY, OrderSide.COVER} else fill.quantity * fill.price - fill.fees
        return [
            LedgerEntry(
                account_id=fill.account_id,
                event_type="fill",
                instrument_id=fill.instrument_id,
                cash_delta=cash_delta,
                quantity_delta=signed_qty,
                price=fill.price,
                fees=fill.fees,
                currency=currency,
                causation_id=fill.fill_id,
            )
        ]

    def rebuild(
        self,
        *,
        account: PaperAccount,
        entries: list[LedgerEntry],
        marks: dict[str, Decimal] | None = None,
    ) -> tuple[PaperAccount, dict[str, Position]]:
        positions: dict[str, Position] = {}
        cash = Decimal("0")
        fees = Decimal("0")
        realized = Decimal("0")
        for entry in sorted(entries, key=lambda row: row.created_at):
            cash += entry.cash_delta
            fees += entry.fees
            realized += entry.realized_pnl
            if entry.instrument_id and entry.quantity_delta != 0 and entry.price is not None:
                pos = positions.get(entry.instrument_id)
                if pos is None:
                    pos = Position(account_id=account.account_id, instrument_id=entry.instrument_id, quantity=Decimal("0"), average_price=Decimal("0"), currency=entry.currency)
                    positions[entry.instrument_id] = pos
                old_qty = pos.quantity
                new_qty = old_qty + entry.quantity_delta
                if old_qty == 0 or (old_qty > 0 and entry.quantity_delta > 0) or (old_qty < 0 and entry.quantity_delta < 0):
                    total_cost = abs(old_qty) * pos.average_price + abs(entry.quantity_delta) * entry.price
                    pos.average_price = total_cost / abs(new_qty) if new_qty != 0 else Decimal("0")
                else:
                    closed_qty = min(abs(old_qty), abs(entry.quantity_delta))
                    if old_qty > 0:
                        pnl = (entry.price - pos.average_price) * closed_qty
                    else:
                        pnl = (pos.average_price - entry.price) * closed_qty
                    pos.realized_pnl += pnl - entry.fees
                    realized += pnl - entry.fees
                    if new_qty == 0:
                        pos.average_price = Decimal("0")
                pos.quantity = new_qty
                pos.updated_at = entry.created_at

        mark_map = marks or {}
        unrealized = Decimal("0")
        gross = Decimal("0")
        net = Decimal("0")
        for instrument_id, pos in positions.items():
            mark = mark_map.get(instrument_id, pos.average_price)
            pos.unrealized_pnl = (mark - pos.average_price) * pos.quantity
            notional = mark * pos.quantity
            unrealized += pos.unrealized_pnl
            gross += abs(notional)
            net += notional
        account.cash_balance = cash
        account.fees = fees
        account.realized_pnl = realized
        account.unrealized_pnl = unrealized
        account.gross_exposure = gross
        account.net_exposure = net
        account.equity = cash + gross + unrealized if any(pos.quantity < 0 for pos in positions.values()) else cash + gross
        account.buying_power = max(Decimal("0"), account.equity - account.reserved_cash)
        account.high_water_mark = max(account.high_water_mark, account.equity)
        account.updated_at = utcnow()
        account.version += 1
        return account, positions
