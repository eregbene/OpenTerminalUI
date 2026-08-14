"""Regression tests for the replay candle-selection determinism fix (regime-determinism
directive): bars_as_of()'s revision/canonical candle queries previously ordered only by
bar_timestamp_utc.desc()/timestamp.desc() with no secondary key -- combined with a LIMIT, ties
gave Postgres no guarantee of stable row order across identical repeated queries, so two runs of
the exact same historical replay could select a different subset of rows for a bar sitting at
the truncation boundary, silently changing that bar's resolved OHLC (and, downstream, ATR/
trend/regime classification) between otherwise-identical replay runs.

Fixed by adding a deterministic secondary sort key (the row's own primary key) to both queries.
These tests lock in that fix by asserting the ORDER BY clauses are actually present -- a real
regression here would have to be caught by direct SQL comparison, not by seeding enough
tied-timestamp fixture rows to trigger Postgres's own nondeterminism under sqlite/pytest (which
does not reliably reproduce ordering nondeterminism at all), so this checks the query
construction itself rather than trying to force a flaky repro.
"""
from __future__ import annotations

import inspect

from backend.historical_intelligence import replay


def test_revision_query_orders_by_bar_timestamp_then_primary_key():
    """Direct check on the actual query construction: bar_timestamp_utc.desc() must be paired
    with MT5CandleRevisionORM.revision_id (the table's own primary key, always unique) as an
    explicit tiebreaker."""
    source = inspect.getsource(replay.bars_as_of)
    revision_order_by = [line for line in source.splitlines() if "MT5CandleRevisionORM.bar_timestamp_utc.desc()" in line]
    assert revision_order_by, "expected an order_by on MT5CandleRevisionORM.bar_timestamp_utc.desc()"
    assert any("revision_id" in line for line in revision_order_by), "revision query must break ties on the primary key (revision_id), not rely on unordered DB tie-breaking"


def test_canonical_query_orders_by_timestamp_then_primary_key():
    source = inspect.getsource(replay.bars_as_of)
    canonical_order_by = [line for line in source.splitlines() if "MT5CanonicalCandleORM.timestamp.desc()" in line]
    assert canonical_order_by, "expected an order_by on MT5CanonicalCandleORM.timestamp.desc()"
    assert any("candle_id" in line for line in canonical_order_by), "canonical query must break ties on the primary key (candle_id), not rely on unordered DB tie-breaking"


def test_snapshot_tier_replay_never_queries_candle_tables_at_all():
    """The control-group path (replay_from_snapshot) reads exact rows captured at decision time
    directly off MT5DecisionSnapshotORM -- it must never touch bars_as_of's revision-selection
    logic, which is exactly why it was unaffected by the ordering bug and serves as a valid
    control group for the real reproduction this fix was verified against."""
    source = inspect.getsource(replay.replay_from_snapshot)
    assert "await bars_as_of(" not in source  # a docstring mention of the function name is fine; an actual call is not
