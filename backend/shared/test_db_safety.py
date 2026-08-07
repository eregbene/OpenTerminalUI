"""Canonical safety guard for destructive database test operations.

Created after the 2026-08-07 incident (see migration 0036 / db_incidents table):
Base.metadata.drop_all(bind=engine) was called against backend.shared.db.engine --
the SAME engine the live application uses via DATABASE_URL -- from inside pytest
fixtures, wiping the shared development database.

Every destructive database test operation (drop_all, a raw DROP/TRUNCATE, or any
create_all against a non-ephemeral database) MUST call assert_safe_test_database()
with the engine it is about to operate on, before executing anything. There is no
scenario in this codebase where a destructive fixture should ever bind to
DATABASE_URL directly.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

if TYPE_CHECKING:
    import pytest

# Names that must never be treated as safe to run destructive operations against,
# regardless of what other markers appear in the same string.
_PROTECTED_DB_NAME_FRAGMENTS = (
    "openterminalui",
    "production",
    "prod",
    "staging",
    "development",
    "bensim",
)

# A database name must contain at least one of these to be considered an explicit,
# intentional test database.
_TEST_DB_NAME_MARKERS = (
    "test_",
    "_test",
    "pytest",
    "ci_",
    "ephemeral",
)


class UnsafeTestDatabaseError(RuntimeError):
    """Raised when a destructive test operation would target a non-test database."""


def _running_under_pytest() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "PYTEST_VERSION" in os.environ


def _urls_equivalent(a: str, b: str) -> bool:
    try:
        ua, ub = make_url(a), make_url(b)
    except Exception:
        return a == b
    normalize = lambda u: (  # noqa: E731
        (u.drivername or "").split("+")[0],
        (u.host or "").lower(),
        u.port,
        (u.database or "").lower(),
        (u.username or "").lower(),
    )
    return normalize(ua) == normalize(ub)


def assert_safe_test_database(engine: Engine) -> None:
    """Raise UnsafeTestDatabaseError unless every safety condition holds.

    Conditions (all must pass):
    1. Running inside pytest.
    2. The engine is not bound to DATABASE_URL (the shared application database).
    3. sqlite engines (in particular isolated in-memory `sqlite://`, which is the
       standard pattern in this codebase and has no "database name" at all) are
       safe by construction once (1) and (2) hold -- an in-memory sqlite DB cannot
       be, and does not need to look like, a named test database.
    4. For every other backend, the database name must contain an explicit test
       marker and must not match any protected name.
    5. For every other backend, TEST_DATABASE_URL must be set and the engine must
       match it exactly -- the shared DATABASE_URL is never an acceptable substitute.
    """
    if not _running_under_pytest():
        raise UnsafeTestDatabaseError(
            "REFUSING destructive database operation: not running under pytest "
            "(PYTEST_CURRENT_TEST/PYTEST_VERSION not set)."
        )

    engine_url_str = engine.url.render_as_string(hide_password=False)
    database_url = os.environ.get("DATABASE_URL")
    if database_url and _urls_equivalent(engine_url_str, database_url):
        raise UnsafeTestDatabaseError(
            "REFUSING destructive database operation: engine is bound to DATABASE_URL "
            "(the shared application database). Destructive fixtures must never use it."
        )

    if engine.url.drivername.startswith("sqlite"):
        return

    db_name = (engine.url.database or "").strip()
    db_name_lower = db_name.lower()

    if not db_name:
        raise UnsafeTestDatabaseError(
            "REFUSING destructive database operation: engine has no database name."
        )

    # A protected name that is the WHOLE database name (case-insensitive) is always
    # rejected outright, e.g. 'openterminalui', 'PRODUCTION' -- no test marker can
    # rescue that. This must be checked before the marker check because a name like
    # 'openterminalui_test' legitimately contains the protected fragment
    # 'openterminalui' as a substring while still being a perfectly safe, explicitly
    # test-marked database -- an exact-match check (rather than substring) is what
    # correctly distinguishes the two.
    if db_name_lower in _PROTECTED_DB_NAME_FRAGMENTS:
        raise UnsafeTestDatabaseError(
            f"REFUSING TO RUN DESTRUCTIVE OPERATION AGAINST PROTECTED DATABASE '{db_name}'."
        )

    if any(marker in db_name_lower for marker in _TEST_DB_NAME_MARKERS):
        pass  # explicit test marker present -- proceed to the TEST_DATABASE_URL check below
    else:
        # No test marker: fall back to a substring check so names like
        # 'openterminalui_prod_backup' (protected fragment, no test marker) are still
        # caught, then report whichever problem applies.
        hit = next((frag for frag in _PROTECTED_DB_NAME_FRAGMENTS if frag in db_name_lower), None)
        if hit is not None:
            raise UnsafeTestDatabaseError(
                f"REFUSING TO RUN DESTRUCTIVE OPERATION AGAINST PROTECTED DATABASE '{db_name}' "
                f"(matched protected fragment '{hit}')."
            )
        raise UnsafeTestDatabaseError(
            f"REFUSING destructive database operation: database name '{db_name}' has no "
            f"explicit test marker (expected one of {_TEST_DB_NAME_MARKERS})."
        )

    test_database_url = os.environ.get("TEST_DATABASE_URL")
    if not test_database_url:
        raise UnsafeTestDatabaseError(
            "REFUSING destructive database operation: TEST_DATABASE_URL is not set. "
            "Non-sqlite destructive fixtures must be explicitly configured via "
            "TEST_DATABASE_URL -- DATABASE_URL is never an acceptable fallback."
        )

    if not _urls_equivalent(engine_url_str, test_database_url):
        raise UnsafeTestDatabaseError(
            "REFUSING destructive database operation: engine does not match "
            "TEST_DATABASE_URL."
        )


# Modules across the codebase that did `from backend.shared.db import SessionLocal`
# (a name binding captured at import time, not an attribute lookup) and therefore
# need to be individually monkeypatched -- patching backend.shared.db.SessionLocal
# alone would not reach them. Extend this list if a new module adopts that import
# style and needs to be redirected by a destructive test fixture.
SESSION_LOCAL_IMPORT_SITES = (
    "backend.shared.db",
    "backend.api.deps",
    "backend.alerts.service",
)


def redirect_shared_db_to_isolated_sqlite(
    monkeypatch: "pytest.MonkeyPatch",
    extra_engine_sites: tuple[str, ...] = (),
) -> sessionmaker:
    """Point the ENTIRE application at a fresh isolated in-memory sqlite database
    for the duration of one test, via monkeypatch (auto-reverted at test teardown).

    Rather than chasing every `from backend.shared.db import SessionLocal` call site
    across the codebase (dozens of modules, including route handlers, middleware,
    and background services -- an open-ended and easy-to-miss list), this reconfigures
    the ORIGINAL backend.shared.db.SessionLocal sessionmaker OBJECT in place via its
    `.kw["bind"]`. Every module that imported that name holds a reference to the SAME
    object, so every one of them picks up the isolated engine automatically -- there
    is nothing to enumerate and nothing to miss.

    `extra_engine_sites` is only for modules that use `from backend.shared.db import
    engine` directly (inspect(engine), engine.begin(), raw DDL) instead of going
    through SessionLocal -- those still need individual patching since an Engine
    object can't be reconfigured in place the way a sessionmaker can.

    Use this instead of touching backend.shared.db.engine/SessionLocal directly in
    a test. Returns the isolated sessionmaker so the test can query/assert with it.
    """
    from backend.shared.db import Base, SessionLocal as shared_session_local

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    assert_safe_test_database(engine)

    from backend.db import models  # noqa: F401  (register all ORM tables, mirrors backend.shared.db.init_db())

    Base.metadata.create_all(bind=engine)

    # In-place reconfiguration of the shared singleton -- reaches every module that
    # did `from backend.shared.db import SessionLocal`, not just the ones listed here.
    monkeypatch.setitem(shared_session_local.kw, "bind", engine)

    for module_path in ("backend.shared.db",) + extra_engine_sites:
        monkeypatch.setattr(f"{module_path}.engine", engine, raising=False)

    return shared_session_local
