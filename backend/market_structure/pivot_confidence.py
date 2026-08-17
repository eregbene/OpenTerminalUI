"""Pivot-confidence scoring -- independently implemented from the PUBLIC methodology description
behind LuxAlgo's kNN Market Architecture's pivot-validation half ("relative-ATR + relative-volume
kNN signature matching" -- confirmed via published description only; no Pine source is publicly
viewable for this indicator, see docs/EXTERNAL_INDICATOR_REDUNDANCY_AUDIT.md's licensing table).
Independently designed distance metric and labeling scheme below -- not a port of anything.

Concept: Bensim's existing swing detection (market_structure/swings.py) is purely rule-based
(fractal pattern + ATR-scaled novelty filter) -- it has no notion of "how reliable is THIS
particular pivot compared to historically similar pivots." This module adds that: each swing gets
a (relative_atr, relative_volume) signature at its own bar, and a confidence score = the fraction
of the k nearest PRIOR swings (same swing_type, same simple Euclidean distance over the two
already-comparable ratios -- no Lorentzian log-compression needed here, unlike the 4 differently-
scaled oscillators Lorentzian Classification uses) whose own outcome was "held" rather than
"failed".

Point-in-time safety: a swing's HELD/FAILED label can only be known once its own `hold_horizon_
bars`-bar lookforward window has fully elapsed -- so the kNN training pool for a query at bar i
only ever includes EARLIER swings old enough to already have a known label, never a swing whose
fate is still undetermined as of i. See `pivot_confidence_series`'s docstring for the exact
cutoff.

THIS MODULE IS NOT YET WIRED INTO ANY STRATEGY OR LIVE DECISION.
"""
from __future__ import annotations

from typing import NamedTuple

from backend.market_structure.bar_utils import StructureBar, average_true_range
from backend.market_structure.models import SwingPoint

_DEFAULT_REF_WINDOW = 20   # trailing window for computing the "average" ATR/volume a pivot's own values are compared against
_DEFAULT_HOLD_HORIZON = 20  # bars forward a pivot must survive un-violated to count as HELD
_DEFAULT_K = 8


class PivotSignature(NamedTuple):
    swing_id: str
    swing_type: str
    bar_index: int
    relative_atr: float
    relative_volume: float
    held: bool | None  # None until hold_horizon_bars has elapsed


def _relative_series(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
            continue
        ref = values[i + 1 - window : i + 1]
        avg = sum(ref) / window
        out.append(values[i] / avg if avg > 0 else None)
    return out


def compute_signatures(
    bars: list[StructureBar], swings: list[SwingPoint], *,
    ref_window: int = _DEFAULT_REF_WINDOW, hold_horizon_bars: int = _DEFAULT_HOLD_HORIZON,
) -> list[PivotSignature]:
    atrs = average_true_range(bars, 14)
    atr_floats = [float(a) if a is not None else None for a in atrs]
    volumes = [float(b.volume) if b.volume is not None else 0.0 for b in bars]
    rel_atr = _relative_series([a if a is not None else 0.0 for a in atr_floats], ref_window)
    rel_vol = _relative_series(volumes, ref_window)

    out: list[PivotSignature] = []
    n = len(bars)
    for swing in swings:
        idx = swing.bar_index
        if idx >= n or rel_atr[idx] is None or rel_vol[idx] is None:
            continue
        level = float(swing.price)
        held: bool | None = None
        horizon_end = idx + hold_horizon_bars
        if horizon_end < n:
            if swing.swing_type == "high":
                held = not any(float(b.close) > level for b in bars[idx + 1 : horizon_end + 1])
            else:
                held = not any(float(b.close) < level for b in bars[idx + 1 : horizon_end + 1])
        out.append(PivotSignature(
            swing_id=swing.id, swing_type=swing.swing_type, bar_index=idx,
            relative_atr=rel_atr[idx], relative_volume=rel_vol[idx], held=held,
        ))
    return out


def _distance(a: PivotSignature, b: PivotSignature) -> float:
    return ((a.relative_atr - b.relative_atr) ** 2 + (a.relative_volume - b.relative_volume) ** 2) ** 0.5


def pivot_confidence_series(signatures: list[PivotSignature], *, k: int = _DEFAULT_K, hold_horizon_bars: int = _DEFAULT_HOLD_HORIZON) -> dict[str, float | None]:
    """For every signature whose own bar_index is late enough that ENOUGH earlier same-type
    swings already have a KNOWN (non-None) held label, returns a confidence score in [0, 1] --
    the fraction of the k nearest such earlier swings (by Euclidean distance over the two
    relative-ATR/relative-volume ratios) that held. Swings with insufficient labeled history
    (early in the series) map to None -- never a fabricated score.

    No-lookahead is enforced HERE, not by processing order alone: a pool candidate's `held` label
    was determined using bars up to `candidate.bar_index + hold_horizon_bars` (see
    compute_signatures), so it only becomes safe to use as evidence for a query once the query's
    OWN bar_index is >= that same cutoff -- never merely because the candidate was iterated
    earlier. Two same-type swings can be closer together than hold_horizon_bars (typical swing
    spacing is a handful of bars; hold_horizon_bars defaults to 20), so naive append-after-
    processing would let a query see a still-undetermined-at-the-time label."""
    ordered = sorted(signatures, key=lambda s: s.bar_index)
    labeled_pool: dict[str, list[PivotSignature]] = {"high": [], "low": []}
    pending: dict[str, list[PivotSignature]] = {"high": [], "low": []}
    result: dict[str, float | None] = {}
    for sig in ordered:
        # Promote any pending same-type candidate whose hold-horizon has now genuinely elapsed
        # relative to THIS query's own bar_index -- not relative to iteration order.
        for swing_type in ("high", "low"):
            still_pending = []
            for cand in pending[swing_type]:
                if cand.bar_index + hold_horizon_bars <= sig.bar_index:
                    labeled_pool[swing_type].append(cand)
                else:
                    still_pending.append(cand)
            pending[swing_type] = still_pending

        pool = labeled_pool[sig.swing_type]
        if len(pool) < k:
            result[sig.swing_id] = None
        else:
            nearest = sorted(pool, key=lambda p: _distance(sig, p))[:k]
            result[sig.swing_id] = sum(1 for p in nearest if p.held) / k
        if sig.held is not None:
            pending[sig.swing_type].append(sig)
    return result
