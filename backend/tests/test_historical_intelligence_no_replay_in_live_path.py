"""Phase 27 (corpus-expansion directive): the live M5 cycle must never perform a historical
backtest or bulk replay -- enforced as a real, permanent test rather than left as a docstring
claim, so a future change that accidentally wires bulk_replay (or the point-in-time replay
engine) into the live decision path fails CI instead of silently degrading live latency."""
from __future__ import annotations

import ast
from pathlib import Path

_LIVE_PATH_MODULES = [
    "backend/brokers/mt5/autonomous.py",
    "backend/brokers/mt5/execution.py",
]

_FORBIDDEN_IMPORTS = {
    "backend.historical_intelligence.bulk_replay",
    "backend.historical_intelligence.replay",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_live_mt5_cycle_modules_never_import_replay_or_bulk_replay():
    repo_root = Path(__file__).resolve().parents[2]
    for rel_path in _LIVE_PATH_MODULES:
        path = repo_root / rel_path
        assert path.exists(), f"expected live-path module missing: {rel_path}"
        imported = _imported_modules(path)
        overlap = imported & _FORBIDDEN_IMPORTS
        assert not overlap, f"{rel_path} imports historical replay/backfill machinery directly in the live path: {overlap}"
        # Also catch a submodule import (`from backend.historical_intelligence import bulk_replay`)
        # that the module-name check above wouldn't see as a dotted forbidden name.
        for mod in imported:
            assert mod != "backend.historical_intelligence" or "bulk_replay" not in path.read_text(encoding="utf-8"), (
                f"{rel_path} appears to reference bulk_replay via a package-level import"
            )


def test_bulk_replay_module_itself_never_imports_live_broker_execution():
    """Sanity check in the other direction: the offline replay/backfill driver must never import
    the live order-submission path either -- it reads history and writes only to
    historical_intelligence tables, never touches broker execution."""
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "backend/historical_intelligence/bulk_replay.py"
    imported = _imported_modules(path)
    forbidden = {"backend.brokers.mt5.execution", "backend.brokers.mt5.autonomous"}
    overlap = imported & forbidden
    assert not overlap, f"bulk_replay.py imports live execution/autonomous modules: {overlap}"


def test_bulk_replay_never_imports_the_mt5_bridge_or_adapter():
    """Phase 10 (corpus-expansion-throughput directive): once historical bars are in Postgres,
    replay must never need the live MT5 bridge/broker adapter for each instant -- only Postgres
    (bars_as_of) and immutable revisions/canonical candles. replay.py's _replay_mtfai1 imports
    autonomous._score_candidate (a pure function, no broker I/O) at call time, which is why the
    module-level import check above targets `backend.brokers.mt5.autonomous` specifically rather
    than this broader bridge/adapter check -- this test confirms bulk_replay.py's OWN top-level
    imports never reach the bridge/adapter modules that actually perform network I/O to the
    broker, regardless of what a transitively-imported module does internally."""
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / "backend/historical_intelligence/bulk_replay.py"
    imported = _imported_modules(path)
    forbidden = {"backend.brokers.mt5.bridge", "backend.brokers.mt5.adapter", "backend.brokers.mt5.multi_account", "backend.brokers.mt5.client"}
    overlap = imported & forbidden
    assert not overlap, f"bulk_replay.py imports live MT5 bridge/adapter modules: {overlap}"
