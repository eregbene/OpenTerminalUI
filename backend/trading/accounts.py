from __future__ import annotations

from decimal import Decimal

from backend.trading.models import AuditRecord, LedgerEntry, PaperAccount


def create_paper_account(name: str, initial_cash: Decimal, base_currency: str = "USD") -> tuple[PaperAccount, LedgerEntry, AuditRecord]:
    account = PaperAccount(
        name=name,
        base_currency=base_currency,
        initial_cash=initial_cash,
        cash_balance=initial_cash,
        equity=initial_cash,
        buying_power=initial_cash,
        high_water_mark=initial_cash,
    )
    ledger = LedgerEntry(account_id=account.account_id, event_type="cash_deposit", cash_delta=initial_cash, currency=base_currency)
    audit = AuditRecord(
        event_type="paper_account_created",
        entity_type="paper_account",
        entity_id=account.account_id,
        account_id=account.account_id,
        payload={"name": name, "initial_cash": str(initial_cash), "base_currency": base_currency},
    )
    return account, ledger, audit
