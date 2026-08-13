"""Adaptive Manager second-stage historical fingerprint (Part 15).

Deliberately built ENTIRELY from data the Adaptive Trade Manager's OWN validation layer already
produces and persists -- AdaptiveManagementEventORM (one row per position per management cycle:
current_r/max_achieved_r/min_achieved_r/action_type/market_regime/atr, append-only, never read
back into _select_action) and AdaptivePositionBaselineORM (the immutable original-setup facts).
No new replay, no new outcome-tracking mechanism -- this module only adds PEER-GROUPING (a
fingerprint over an existing state snapshot) on top of data that already exists and is already
trustworthy (that data's own correctness is a pre-existing, separately-validated concern of the
Adaptive Trade Manager itself, not something this module re-derives).

A "trade-evolution state" fingerprint answers a DIFFERENT question than the entry-side
HistoricalPatternFingerprintORM: not "what did this setup look like at entry" but "what does
this OPEN TRADE look like right now, given how far it's already traveled" -- the peer group is
therefore keyed on strategy + symbol + direction + CURRENT regime + a bucketed current-R/MFE/
elapsed-time state, not on entry-time SMC structure.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from backend.historical_intelligence.fingerprint import time_of_day_bucket


def _bucket_r(r: float | None) -> str | None:
    if r is None:
        return None
    if r < 0:
        return "NEGATIVE"
    if r < 0.5:
        return "0_TO_0.5R"
    if r < 1.0:
        return "0.5_TO_1R"
    if r < 1.5:
        return "1_TO_1.5R"
    if r < 2.0:
        return "1.5_TO_2R"
    return "2R_PLUS"


def _bucket_elapsed(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    minutes = seconds / 60.0
    if minutes < 15:
        return "UNDER_15M"
    if minutes < 60:
        return "15_TO_60M"
    if minutes < 240:
        return "1_TO_4H"
    return "OVER_4H"


def state_peer_group_hash(fields: dict[str, Any]) -> str:
    payload = json.dumps(fields, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def build_state_fingerprint(
    *,
    strategy: str | None,
    symbol: str,
    direction: str,
    original_regime: str | None,
    current_regime: str | None,
    current_r: float | None,
    max_achieved_r: float | None,
    min_achieved_r: float | None,
    elapsed_seconds: float | None,
    is_at_or_beyond_breakeven: bool,
    is_trailing_action: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Pure function -- no DB access. `strategy` may be None (position adopted or missing a
    linked candidate evaluation); such rows still fingerprint (symbol/direction/state alone
    still form a meaningful peer group), just with a coarser "strategy unknown" grouping."""
    now = now or datetime.now(timezone.utc)
    peer_fields = {
        "strategy": strategy or "unknown",
        "symbol": symbol.upper(),
        "direction": direction.upper(),
        "original_regime": original_regime,
        "current_regime": current_regime,
        "current_r_bucket": _bucket_r(current_r),
        "mfe_bucket": _bucket_r(max_achieved_r),
        "elapsed_bucket": _bucket_elapsed(elapsed_seconds),
        "be_state": bool(is_at_or_beyond_breakeven),
        "session": time_of_day_bucket(now),
    }
    fields = {
        **peer_fields,
        "current_r": current_r,
        "max_achieved_r": max_achieved_r,
        "min_achieved_r": min_achieved_r,
        "elapsed_seconds": elapsed_seconds,
        "is_trailing_action": bool(is_trailing_action),
        "peer_group_hash": state_peer_group_hash(peer_fields),
    }
    return fields
