from __future__ import annotations

import os


def assert_test_database_isolated() -> None:
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    test_database_url = (os.getenv("TEST_DATABASE_URL") or "").strip()
    if test_database_url and database_url and test_database_url == database_url:
        raise RuntimeError("TEST_DATABASE_URL_MUST_DIFFER_FROM_DATABASE_URL")
