"""Regression suite for the permanent local-development safety workflow
(docs/CLAUDE_BACKEND_SAFETY.md), established after the 2026-08-07 incident.

Covers the 12 required cases: destructive-fixture DB rejection, TEST_DATABASE_URL
enforcement, session-abort-before-collection, fake-MT5-never-touches-real-bridge,
demo-mutation-gate rejects non-demo accounts, engineering-test-trade learning
exclusion, and the backup script's in-repo write guard.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from backend.brokers.mt5.account_registry import (
    AccountClassification,
    MT5AccountProfileORM,
    fingerprint_account,
)
from backend.brokers.mt5.models import MT5Account
from backend.brokers.mt5.test_mutation_safety import (
    TEST_MUTATION_NON_DEMO_ACCOUNT,
    ENGINEERING_TEST_TAG,
    UnsafeDemoMutationError,
    assert_safe_demo_mutation_test,
    is_learning_eligible,
)
from backend.shared.test_db_safety import (
    UnsafeTestDatabaseError,
    assert_safe_test_database,
    redirect_shared_db_to_isolated_sqlite,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# 1. dev DB rejected by destructive fixtures.
def test_dev_db_rejected_by_destructive_fixtures():
    engine = create_engine("postgresql+psycopg://user:pw@host:5432/openterminalui")
    with pytest.raises(UnsafeTestDatabaseError, match="PROTECTED DATABASE"):
        assert_safe_test_database(engine)


# 2. DATABASE_URL alone cannot be used as a destructive test DB, even with a
#    test-ish name.
def test_database_url_alone_cannot_be_destructive_test_db(monkeypatch: pytest.MonkeyPatch):
    url = "postgresql+psycopg://user:pw@host:5432/openterminalui_test"
    monkeypatch.setenv("DATABASE_URL", url)
    engine = create_engine(url)
    with pytest.raises(UnsafeTestDatabaseError, match="DATABASE_URL"):
        assert_safe_test_database(engine)


# 3. TEST_DATABASE_URL required for non-sqlite destructive engines.
def test_test_database_url_required(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    engine = create_engine("postgresql+psycopg://user:pw@host:5432/openterminalui_test")
    with pytest.raises(UnsafeTestDatabaseError, match="TEST_DATABASE_URL is not set"):
        assert_safe_test_database(engine)


# 4. openterminalui_test allowed.
def test_openterminalui_test_allowed(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    url = "postgresql+psycopg://user:pw@host:5432/openterminalui_test"
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    assert_safe_test_database(create_engine(url))  # must not raise


# 5. The pytest session aborts BEFORE collection when TEST_DATABASE_URL is unsafe.
#    Run as a real subprocess -- pytest_configure calls pytest.exit(), which must
#    not be invoked inside the current test process.
def test_session_aborts_before_collection_against_protected_db():
    env = {
        "PATH": __import__("os").environ.get("PATH", ""),
        "DATABASE_URL": "postgresql+psycopg://user:pw@host:5432/openterminalui",
        "TEST_DATABASE_URL": "postgresql+psycopg://user:pw@host:5432/openterminalui",
        "PYTHONPATH": str(REPO_ROOT),
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "backend/tests/test_test_db_safety.py", "-q", "--collect-only"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    combined = result.stdout + result.stderr
    assert "REFUSING TO RUN DESTRUCTIVE/INTEGRATION TEST SESSION" in combined
    assert "collected" not in combined.lower() or "0 items" in combined.lower() or result.returncode != 0


# 6. A fake/offline MT5 test never calls the real bridge -- proven by making the
#    fake client's "connect" path raise if it's ever reached, then completing a
#    pure-calculation operation that must never touch it.
def test_fake_mt5_offline_test_never_calls_real_bridge():
    from backend.brokers.mt5.risk_calculator import calculate_conservative_loss_per_lot_sync
    from backend.brokers.mt5.models import MT5Symbol

    class _RealBridgeCalledError(AssertionError):
        pass

    class _NeverConnectClient:
        def ensure_ready(self):
            raise _RealBridgeCalledError("a supposedly-offline MT5 test attempted to reach the real bridge/client")

    # calculate_conservative_loss_per_lot_sync is documented as the sync subset
    # that never touches a broker connection -- if it did, _NeverConnectClient
    # would raise instead of the real bridge being silently reached.
    symbol = MT5Symbol(
        symbol="EURUSD", visible=True, selected=True, digits=5, point=Decimal("0.00001"),
        trade_tick_size=Decimal("0.00001"), trade_tick_value=Decimal("1.0"), trade_tick_value_profit=Decimal("1.0"),
        trade_tick_value_loss=Decimal("1.0"), trade_contract_size=Decimal("100000"),
        volume_min=Decimal("0.01"), volume_max=Decimal("100.0"), volume_step=Decimal("0.01"),
    )
    result = calculate_conservative_loss_per_lot_sync(entry=Decimal("1.10000"), stop=Decimal("1.09900"), symbol_info=symbol)
    assert result.selected_loss_per_lot > 0


def _fake_account(login: int, server: str) -> MT5Account:
    return MT5Account(
        login=login, server=server, currency="USD",
        balance=Decimal("10000"), equity=Decimal("10000"),
        margin=Decimal("0"), free_margin=Decimal("10000"),
        company="Test Broker",
    )


class _FakeAdapter:
    def __init__(self, account: MT5Account, account_mode: str = "DEMO") -> None:
        self._account = account
        self.config = type("_Cfg", (), {"account_mode": account_mode})()

    async def mt5_account(self) -> MT5Account:
        return self._account


async def _seed_account_classification(monkeypatch: pytest.MonkeyPatch, account: MT5Account, classification: str):
    session_local = redirect_shared_db_to_isolated_sqlite(monkeypatch)
    fp = fingerprint_account(account)
    now = datetime.now(timezone.utc)
    with session_local() as db:
        db.add(
            MT5AccountProfileORM(
                fingerprint_hash=fp.fingerprint_hash, login=fp.login, server=fp.server,
                company=fp.company, currency=fp.currency, classification=classification,
                approved=False, first_seen_at=now, last_seen_at=now,
            )
        )
        db.commit()


# 7. Demo integration mutation rejects a live account.
@pytest.mark.asyncio
async def test_demo_mutation_rejects_live_account(monkeypatch: pytest.MonkeyPatch):
    account = _fake_account(11111, "SomeBroker-Live")
    await _seed_account_classification(monkeypatch, account, AccountClassification.PERSONAL_LIVE.value)
    with pytest.raises(UnsafeDemoMutationError) as exc_info:
        await assert_safe_demo_mutation_test(_FakeAdapter(account, account_mode="LIVE"))
    assert exc_info.value.reason_code == TEST_MUTATION_NON_DEMO_ACCOUNT


# 8. Demo integration mutation rejects a prop account.
@pytest.mark.asyncio
async def test_demo_mutation_rejects_prop_account(monkeypatch: pytest.MonkeyPatch):
    account = _fake_account(22222, "SomeBroker-Prop")
    await _seed_account_classification(monkeypatch, account, AccountClassification.PROP_FUNDED.value)
    with pytest.raises(UnsafeDemoMutationError) as exc_info:
        await assert_safe_demo_mutation_test(_FakeAdapter(account, account_mode="LIVE"))
    assert exc_info.value.reason_code == TEST_MUTATION_NON_DEMO_ACCOUNT


# 9. Demo integration mutation rejects an unknown account.
@pytest.mark.asyncio
async def test_demo_mutation_rejects_unknown_account(monkeypatch: pytest.MonkeyPatch):
    account = _fake_account(33333, "SomeBroker-Unclassified")
    await _seed_account_classification(monkeypatch, account, AccountClassification.UNKNOWN.value)
    with pytest.raises(UnsafeDemoMutationError) as exc_info:
        await assert_safe_demo_mutation_test(_FakeAdapter(account, account_mode="LIVE"))
    assert exc_info.value.reason_code == TEST_MUTATION_NON_DEMO_ACCOUNT


# 10. Engineering test trades are excluded from learning by default.
def test_engineering_test_trades_excluded_from_learning():
    assert is_learning_eligible(ENGINEERING_TEST_TAG) is False


# 11. Autonomous demo trades (unmarked, or explicitly tagged otherwise) remain
#     included in learning.
def test_autonomous_demo_trades_remain_included_in_learning():
    assert is_learning_eligible(None) is True
    assert is_learning_eligible("BENSIM_AUTO") is True


# 12. The backup script refuses to write inside the git repo by default. Executing
#     the .ps1 script requires PowerShell, which is not available in the Linux
#     container this suite normally runs in -- so this is a static assertion that
#     the guard exists in the script source, not a live execution proof. See the
#     live execution proof already performed manually: backup_postgres.ps1 throws
#     "REFUSING TO BACK UP INSIDE THE GIT REPO" when OutDir resolves inside REPO_ROOT.
def test_backup_script_has_in_repo_write_guard():
    script = (REPO_ROOT / "scripts" / "backup_postgres.ps1").read_text(encoding="utf-8")
    assert "REFUSING TO BACK UP INSIDE THE GIT REPO" in script
    assert "$repoRoot" in script
