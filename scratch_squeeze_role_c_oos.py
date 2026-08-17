"""Addendum to scratch_squeeze_momentum_validation.py: the first pass reported squeeze_state's
association with outcome_r pooled across the whole corpus but never checked it chronologically --
an unsplit correlation is not evidence of a stable, reusable signal. This re-runs just that one
check (squeeze_state -> outcome_r, and RELEASING vs OFF specifically) with a 60/40 chronological
split, reusing the exact same loading/join logic as the main script."""
from __future__ import annotations

import sys

sys.path.insert(0, "/app")
from scratch_squeeze_momentum_validation import (
    SQUEEZE_OFF, SQUEEZE_ON, SQUEEZE_RELEASING,
    _split, _stats, load_bar_series, load_candidates, point_in_time_index,
)

bars, states, momentum, atrs, close_times = load_bar_series()
candidates = load_candidates()

joined = []
for row in candidates:
    idx = point_in_time_index(close_times, row.entry_time)
    if idx is None:
        continue
    joined.append((row, states[idx]))

joined.sort(key=lambda t: t[0].entry_time)

print("\nsqueeze_state -> outcome_r, chronological 60/40 split (all strategies pooled):")
for state in (SQUEEZE_ON, SQUEEZE_RELEASING, SQUEEZE_OFF):
    subset = [row for row, s in joined if s == state]
    train, oos = _split(subset)
    train_rs = [row.outcome_r for row in train if row.outcome_r is not None]
    oos_rs = [row.outcome_r for row in oos if row.outcome_r is not None]
    all_rs = [row.outcome_r for row in subset if row.outcome_r is not None]
    print(f"  {state:10s} all={_stats(all_rs)}")
    print(f"  {'':10s} train60={_stats(train_rs)}  oos40={_stats(oos_rs)}")
