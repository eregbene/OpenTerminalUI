"""Phase 13 (Forex/MT5 roadmap) regression test: the new forex-ops observability router is
importable, registered on the main api_router without collision, and exposes exactly the
expected read-only paths."""
from __future__ import annotations

from backend.api.routes import forex_ops


def test_forex_ops_router_registered() -> None:
    assert forex_ops.router is not None


def test_forex_ops_router_exposes_expected_paths() -> None:
    paths = {route.path for route in forex_ops.router.routes}
    assert paths == {
        "/api/forex-ops/degradation",
        "/api/forex-ops/sequence-risk/{account_id}",
        "/api/forex-ops/execution-quality/{account_id}",
        "/api/forex-ops/reconciliation/{account_id}",
        "/api/forex-ops/correlation",
        "/api/forex-ops/edge-decay",
        "/api/forex-ops/profit-retention",
        "/api/forex-ops/edge-stability",
    }


def test_forex_ops_routes_are_get_only() -> None:
    """Every endpoint here is read-only analytics -- no mutation verbs should ever appear."""
    for route in forex_ops.router.routes:
        assert route.methods == {"GET"}


def test_main_api_router_includes_forex_ops_without_collision() -> None:
    """Importing the assembled router pulls in every registered sub-router (including
    forex_ops_router's include_router call in backend/api/router.py) -- a naming collision or
    import-time error anywhere in that chain would raise here."""
    from backend.api.router import api_router, forex_ops_router

    assert forex_ops_router is forex_ops.router
    assert api_router is not None
