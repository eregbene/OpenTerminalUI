"""Per-strategy Historical Intelligence trust gating (Part 6).

DELIBERATELY SEPARATE from pattern-level statistical reliability (statistics.py's
`reliability_label`, which answers "is THIS PEER GROUP's sample big enough/consistent"). This
module answers a different question: "can we trust point-in-time REPLAY ITSELF for this
strategy at all" -- i.e. does reconstructed/snapshot replay actually reproduce what the strategy
does live, independent of whether any particular pattern within it has enough samples yet. BOTH
gates (trust_gating.trust_state == HIST_INTEL_ACTIVE AND statistics.reliability_label in
{USEFUL, STRONG}) must pass before Historical Intelligence may influence a live decision for a
given strategy+pattern -- see entry_intelligence.py.

Trust states are NEVER hardcoded per-strategy in code -- recompute_trust() derives them fresh
from real HistoricalReplayParityCheckORM rows (the durable evidence backing every parity claim
made in this session) every time it runs. Re-running it after more parity checks accumulate can
move a strategy between states in either direction.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.historical_intelligence.orm import HistoricalReplayParityCheckORM, StrategyReplayTrustORM
from backend.historical_intelligence.replay import STRATEGY_REPLAY_VERSION
from backend.shared.db import SessionLocal

HIST_INTEL_ACTIVE = "HIST_INTEL_ACTIVE"
HIST_INTEL_OBSERVE_ONLY = "HIST_INTEL_OBSERVE_ONLY"
HIST_INTEL_UNTRUSTED_REPLAY = "HIST_INTEL_UNTRUSTED_REPLAY"

# Policy thresholds (a judgment call, documented and reviewable -- NOT the trust verdicts
# themselves, which are always computed fresh from real evidence, never hardcoded per strategy).
_MIN_SAMPLE_FOR_ANY_TRUST = 5
_MIN_SAMPLE_FOR_ACTIVE = 20
_ACTIVE_EXACT_MATCH_PCT = 50.0
_ACTIVE_COMBINED_PCT = 70.0  # exact_match_pct + direction_match_pct
_OBSERVE_COMBINED_PCT = 20.0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _classify(*, sample_count: int, exact_pct: float, direction_pct: float) -> tuple[str, str]:
    combined = exact_pct + direction_pct
    if sample_count < _MIN_SAMPLE_FOR_ANY_TRUST:
        return HIST_INTEL_UNTRUSTED_REPLAY, f"sample_count={sample_count} below minimum ({_MIN_SAMPLE_FOR_ANY_TRUST}) for any trust"
    if sample_count >= _MIN_SAMPLE_FOR_ACTIVE and (exact_pct >= _ACTIVE_EXACT_MATCH_PCT or combined >= _ACTIVE_COMBINED_PCT):
        return HIST_INTEL_ACTIVE, f"exact_match_pct={exact_pct:.1f} direction_or_better_pct={combined:.1f} over n={sample_count} clears ACTIVE thresholds (exact>={_ACTIVE_EXACT_MATCH_PCT} or combined>={_ACTIVE_COMBINED_PCT})"
    if combined >= _OBSERVE_COMBINED_PCT:
        return HIST_INTEL_OBSERVE_ONLY, f"direction_or_better_pct={combined:.1f} over n={sample_count} shows real but insufficient signal for ACTIVE (needs n>={_MIN_SAMPLE_FOR_ACTIVE} and exact>={_ACTIVE_EXACT_MATCH_PCT} or combined>={_ACTIVE_COMBINED_PCT})"
    return HIST_INTEL_UNTRUSTED_REPLAY, f"direction_or_better_pct={combined:.1f} over n={sample_count} below the {_OBSERVE_COMBINED_PCT} floor -- replay does not currently reproduce this strategy's live behavior"


def recompute_trust(*, strategy_version: str | None = None) -> dict[str, dict[str, Any]]:
    """Recomputes and PERSISTS (upserts, one row per strategy_id) trust state for every strategy
    with at least one parity check on record, scoped to `strategy_version` (defaults to the
    CURRENT replay.STRATEGY_REPLAY_VERSION -- parity evidence from an older strategy version
    must never silently justify trust for the current one)."""
    version = strategy_version or STRATEGY_REPLAY_VERSION
    with SessionLocal() as db:
        checks = db.query(HistoricalReplayParityCheckORM).order_by(HistoricalReplayParityCheckORM.created_at.asc()).all()

    # Dedupe by live_evaluation_id, keeping only the MOST RECENT check -- verify_parity() never
    # upserts (every run inserts a fresh row), so re-running a parity batch after a code fix
    # (as happened in this session: pre- and post- broker-UTC-offset-fix batches against the
    # SAME real evaluation_ids) otherwise pollutes this aggregate with stale, superseded
    # verdicts. Trust must always reflect what the CURRENTLY deployed replay code actually does.
    latest_by_evaluation: dict[str, HistoricalReplayParityCheckORM] = {}
    for check in checks:
        latest_by_evaluation[check.live_evaluation_id] = check  # ascending order -> last write wins

    by_strategy: dict[str, list[HistoricalReplayParityCheckORM]] = {}
    for check in latest_by_evaluation.values():
        by_strategy.setdefault(check.strategy_id or "unknown", []).append(check)

    results: dict[str, dict[str, Any]] = {}
    now = utcnow()
    with SessionLocal() as db:
        for strategy_id, group in by_strategy.items():
            n = len(group)
            exact = sum(1 for c in group if c.verdict == "EXACT_MATCH")
            direction = sum(1 for c in group if c.verdict == "DIRECTION_MATCH")
            strategy_present = sum(1 for c in group if c.verdict in {"EXACT_MATCH", "DIRECTION_MATCH", "GEOMETRY_MATCH", "REGIME_MATCH", "STRATEGY_PRESENT"})
            regime_match = sum(1 for c in group if (c.diff_detail or {}).get("regime_match"))
            snapshot_tier = sum(1 for c in group if (c.diff_detail or {}).get("replay_source") == "SNAPSHOT")

            exact_pct = round(100.0 * exact / n, 1) if n else 0.0
            direction_pct = round(100.0 * direction / n, 1) if n else 0.0
            strategy_presence_pct = round(100.0 * strategy_present / n, 1) if n else 0.0
            regime_match_pct = round(100.0 * regime_match / n, 1) if n else 0.0
            snapshot_tier_pct = round(100.0 * snapshot_tier / n, 1) if n else 0.0

            trust_state, reason = _classify(sample_count=n, exact_pct=exact_pct, direction_pct=direction_pct)

            row = db.get(StrategyReplayTrustORM, strategy_id)
            if row is None:
                row = StrategyReplayTrustORM(strategy_id=strategy_id)
                db.add(row)
            row.trust_state = trust_state
            row.reason = reason
            row.sample_count = n
            row.exact_match_pct = exact_pct
            row.direction_match_pct = direction_pct
            row.strategy_presence_pct = strategy_presence_pct
            row.regime_match_pct = regime_match_pct
            row.snapshot_tier_pct = snapshot_tier_pct
            row.snapshot_tier_sample_count = snapshot_tier
            row.strategy_version = version
            row.computed_at = now

            results[strategy_id] = {
                "trust_state": trust_state, "reason": reason, "sample_count": n,
                "exact_match_pct": exact_pct, "direction_match_pct": direction_pct,
                "strategy_presence_pct": strategy_presence_pct, "regime_match_pct": regime_match_pct,
                "snapshot_tier_pct": snapshot_tier_pct, "snapshot_tier_sample_count": snapshot_tier,
            }
        db.commit()
    return results


def get_trust_state(strategy_id: str) -> dict[str, Any]:
    """Read-only lookup of the LAST computed trust state (does not recompute -- call
    recompute_trust() on whatever cadence makes sense, e.g. after each new parity batch).
    Returns UNTRUSTED with an explicit reason when no trust row exists yet at all (never
    silently defaults to trusted)."""
    with SessionLocal() as db:
        row = db.get(StrategyReplayTrustORM, strategy_id)
    if row is None:
        return {"trust_state": HIST_INTEL_UNTRUSTED_REPLAY, "reason": "no parity evidence recorded for this strategy yet", "sample_count": 0}
    return {
        "trust_state": row.trust_state, "reason": row.reason, "sample_count": row.sample_count,
        "exact_match_pct": row.exact_match_pct, "direction_match_pct": row.direction_match_pct,
        "strategy_presence_pct": row.strategy_presence_pct, "regime_match_pct": row.regime_match_pct,
        "snapshot_tier_pct": row.snapshot_tier_pct, "snapshot_tier_sample_count": row.snapshot_tier_sample_count,
        "strategy_version": row.strategy_version, "computed_at": row.computed_at.isoformat() if row.computed_at else None,
    }


def all_trust_states() -> dict[str, dict[str, Any]]:
    with SessionLocal() as db:
        rows = db.query(StrategyReplayTrustORM).all()
    return {row.strategy_id: get_trust_state(row.strategy_id) for row in rows}
