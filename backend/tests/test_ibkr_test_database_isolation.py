from __future__ import annotations

import pytest

from backend.brokers.ibkr.test_database_safety import assert_test_database_isolated


def test_test_database_isolation_passes_when_test_database_is_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://app/dev")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://app/test")
    assert_test_database_isolated()


def test_test_database_isolation_fails_when_test_database_equals_development(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://app/dev")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://app/dev")
    with pytest.raises(RuntimeError, match="TEST_DATABASE_URL_MUST_DIFFER_FROM_DATABASE_URL"):
        assert_test_database_isolated()


def test_test_database_isolation_allows_unconfigured_non_destructive_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://app/dev")
    assert_test_database_isolated()
