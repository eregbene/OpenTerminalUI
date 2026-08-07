from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine

from backend.shared.test_db_safety import UnsafeTestDatabaseError, assert_safe_test_database


def _pg_engine(url: str):
    # A real connection is never attempted -- assert_safe_test_database only
    # inspects engine.url, and create_engine() does not connect eagerly.
    return create_engine(url)


def _sqlite_engine():
    return create_engine("sqlite://")


# 1. drop_all-style destructive call against the real 'openterminalui' database is rejected.
def test_protected_openterminalui_database_rejected():
    engine = _pg_engine("postgresql+psycopg://user:pw@host:5432/openterminalui")
    with pytest.raises(UnsafeTestDatabaseError, match="PROTECTED DATABASE"):
        assert_safe_test_database(engine)


# 2. A destructive fixture built from plain DATABASE_URL (even if the name looks
#    test-ish) is rejected -- DATABASE_URL is never an acceptable source.
def test_engine_matching_database_url_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pw@host:5432/openterminalui_test")
    engine = _pg_engine("postgresql+psycopg://user:pw@host:5432/openterminalui_test")
    with pytest.raises(UnsafeTestDatabaseError, match="DATABASE_URL"):
        assert_safe_test_database(engine)


# 3. Missing TEST_DATABASE_URL: a non-sqlite destructive fixture is rejected/fails safely.
def test_missing_test_database_url_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    engine = _pg_engine("postgresql+psycopg://user:pw@host:5432/openterminalui_test")
    with pytest.raises(UnsafeTestDatabaseError, match="TEST_DATABASE_URL is not set"):
        assert_safe_test_database(engine)


# 4. openterminalui_test (explicit test marker, not a protected name) is accepted.
def test_openterminalui_test_accepted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    url = "postgresql+psycopg://user:pw@host:5432/openterminalui_test"
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    engine = _pg_engine(url)
    assert_safe_test_database(engine)  # must not raise


# 5. A CI-style ephemeral DB name is accepted.
def test_ci_ephemeral_database_accepted(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    url = "postgresql+psycopg://user:pw@ci-runner:5432/ci_ephemeral_run_48213"
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    engine = _pg_engine(url)
    assert_safe_test_database(engine)  # must not raise


# 6. Protected production database rejected.
def test_protected_production_database_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    url = "postgresql+psycopg://user:pw@host:5432/production"
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    engine = _pg_engine(url)
    with pytest.raises(UnsafeTestDatabaseError, match="PROTECTED DATABASE"):
        assert_safe_test_database(engine)


# 7. Protected staging database rejected.
def test_protected_staging_database_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    url = "postgresql+psycopg://user:pw@host:5432/staging"
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    engine = _pg_engine(url)
    with pytest.raises(UnsafeTestDatabaseError, match="PROTECTED DATABASE"):
        assert_safe_test_database(engine)


# 8. Mixed-case protected name rejected -- matching is case-insensitive.
def test_mixed_case_protected_name_rejected(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    url = "postgresql+psycopg://user:pw@host:5432/OpenTerminalUI"
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    engine = _pg_engine(url)
    with pytest.raises(UnsafeTestDatabaseError, match="PROTECTED DATABASE"):
        assert_safe_test_database(engine)


# 9. localhost doesn't make an unsafe (protected-name) DB automatically safe.
def test_localhost_does_not_make_protected_db_safe(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    url = "postgresql+psycopg://user:pw@localhost:5432/openterminalui"
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    engine = _pg_engine(url)
    with pytest.raises(UnsafeTestDatabaseError, match="PROTECTED DATABASE"):
        assert_safe_test_database(engine)


# 10. A docker-compose service hostname doesn't make an unsafe DB safe either.
def test_docker_hostname_does_not_make_unsafe_db_safe(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    url = "postgresql+psycopg://user:pw@openterminalui-postgres-1:5432/openterminalui"
    monkeypatch.setenv("TEST_DATABASE_URL", url)
    engine = _pg_engine(url)
    with pytest.raises(UnsafeTestDatabaseError, match="PROTECTED DATABASE"):
        assert_safe_test_database(engine)


# 11. Not running under pytest: refused regardless of how safe the database name looks.
def test_refused_outside_pytest(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("PYTEST_VERSION", raising=False)
    engine = _pg_engine("postgresql+psycopg://user:pw@host:5432/openterminalui_test")
    with pytest.raises(UnsafeTestDatabaseError, match="not running under pytest"):
        assert_safe_test_database(engine)


# 12. Isolated in-memory sqlite is always safe by construction (no db name to check).
def test_isolated_sqlite_engine_always_accepted():
    engine = _sqlite_engine()
    assert_safe_test_database(engine)  # must not raise


# 13. Non-destructive unit tests (plain assertions, no DB engine at all) are
#     completely unaffected by this guard -- it only fires when explicitly called.
def test_guard_is_opt_in_and_does_not_affect_unrelated_tests():
    assert 1 + 1 == 2
