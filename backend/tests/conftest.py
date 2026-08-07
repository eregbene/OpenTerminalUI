from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


# Ensure `import backend...` works even when pytest is launched from `backend/`.
REPO_ROOT = Path(__file__).resolve().parents[2]
repo_root_str = str(REPO_ROOT)
if repo_root_str not in sys.path:
    sys.path.insert(0, repo_root_str)


def _sanitize_db_url(url_str: str) -> str:
    from sqlalchemy.engine import make_url

    try:
        u = make_url(url_str)
    except Exception:
        return "<unparseable>"
    return f"driver={u.drivername} host={u.host or ''} port={u.port or ''} db={u.database or ''}"


def pytest_configure(config: pytest.Config) -> None:
    """Print the sanitized (no password) resolved DB target at session start, and
    abort the ENTIRE session immediately -- before any test collection or fixture
    execution -- if TEST_DATABASE_URL is set but misconfigured to point at a
    protected database or a database with no explicit test marker.

    Added after the 2026-08-07 incident (Base.metadata.drop_all(bind=engine)
    against the shared dev database from inside pytest fixtures -- see
    backend/shared/test_db_safety.py and migration 0036_db_incident_boundary /
    the db_incidents table). This is a belt-and-suspenders session-level check;
    the per-call guard in assert_safe_test_database() is the primary defense and
    fires regardless of whether this hook ever runs.
    """
    database_url = os.environ.get("DATABASE_URL")
    test_database_url = os.environ.get("TEST_DATABASE_URL")

    if database_url:
        print(f"[pytest] Resolved DATABASE_URL target: {_sanitize_db_url(database_url)}")
    if not test_database_url:
        print("[pytest] TEST_DATABASE_URL is not set -- any test requiring a real, "
              "non-sqlite destructive/integration database will skip or fail safely "
              "(see backend/shared/test_db_safety.py).")
        return

    print(f"[pytest] Resolved TEST_DATABASE_URL target: {_sanitize_db_url(test_database_url)}")

    from sqlalchemy.engine import make_url

    from backend.shared.test_db_safety import (
        _PROTECTED_DB_NAME_FRAGMENTS,
        _TEST_DB_NAME_MARKERS,
        _urls_equivalent,
    )

    try:
        parsed = make_url(test_database_url)
    except Exception:
        pytest.exit(f"REFUSING TO RUN: TEST_DATABASE_URL is not a valid URL: {test_database_url!r}", returncode=1)
        return

    db_name = (parsed.database or "").lower()

    if database_url and _urls_equivalent(test_database_url, database_url):
        pytest.exit(
            "REFUSING TO RUN DESTRUCTIVE/INTEGRATION TEST SESSION: TEST_DATABASE_URL is identical to "
            "DATABASE_URL (the shared application database). Aborting before any test runs.",
            returncode=1,
        )
        return

    hit = next((frag for frag in _PROTECTED_DB_NAME_FRAGMENTS if frag in db_name), None)
    if hit is not None:
        pytest.exit(
            f"REFUSING TO RUN DESTRUCTIVE/INTEGRATION TEST SESSION AGAINST PROTECTED DATABASE "
            f"'{parsed.database}' (matched protected fragment '{hit}'). Aborting before any test runs.",
            returncode=1,
        )
        return

    if not any(marker in db_name for marker in _TEST_DB_NAME_MARKERS):
        pytest.exit(
            f"REFUSING TO RUN DESTRUCTIVE/INTEGRATION TEST SESSION: TEST_DATABASE_URL database "
            f"'{parsed.database}' has no explicit test marker (expected one of {_TEST_DB_NAME_MARKERS}). "
            f"Aborting before any test runs.",
            returncode=1,
        )


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def mock_adapter():
    from backend.adapters.mock import MockDataAdapter

    return MockDataAdapter(seed=42)


@pytest.fixture
def mock_adapter_registry(monkeypatch, mock_adapter):
    from backend.adapters import registry as registry_module
    from backend.adapters.registry import AdapterRegistry

    class _MockOnlyRegistry(AdapterRegistry):
        def __init__(self) -> None:
            super().__init__()
            self._factory["mock"] = lambda: mock_adapter

    test_registry = _MockOnlyRegistry()
    monkeypatch.setattr(registry_module, "_registry", test_registry, raising=False)
    monkeypatch.setattr(registry_module, "get_adapter_registry", lambda: test_registry)
    return test_registry


@pytest.fixture(autouse=True)
def ensure_mock_adapter_registered():
    from backend.adapters.mock import MockDataAdapter
    from backend.adapters.registry import get_adapter_registry

    registry = get_adapter_registry()
    if "mock" not in registry._factory:  # noqa: SLF001
        registry._factory["mock"] = lambda: MockDataAdapter(seed=42)  # type: ignore[assignment] # noqa: SLF001
