from __future__ import annotations

from backend.trading.models import PaperAccount, ReconciliationResult


def reconcile_account(stored: PaperAccount, rebuilt: PaperAccount) -> ReconciliationResult:
    differences: list[str] = []
    for field in ["cash_balance", "equity", "gross_exposure", "net_exposure", "realized_pnl", "unrealized_pnl", "fees"]:
        if getattr(stored, field) != getattr(rebuilt, field):
            differences.append(f"{field}: stored={getattr(stored, field)} rebuilt={getattr(rebuilt, field)}")
    return ReconciliationResult(
        account_id=stored.account_id,
        status="PASS" if not differences else "DIFFERENCE",
        differences=differences,
        calculated_equity=rebuilt.equity,
        stored_equity=stored.equity,
    )
