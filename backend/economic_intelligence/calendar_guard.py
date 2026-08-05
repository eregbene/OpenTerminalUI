from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

from backend.economic_intelligence.config import EconomicIntelligenceConfig

DECISIONS = ("ALLOW", "BLOCK", "DELAY", "REDUCE_SIZE", "MANAGE_EXISTING_ONLY")
_PRECEDENCE = {"BLOCK": 4, "MANAGE_EXISTING_ONLY": 3, "DELAY": 2, "REDUCE_SIZE": 1, "ALLOW": 0}


class GuardResult(TypedDict):
    decision: str
    reason_codes: list[str]
    nearest_event: dict[str, Any] | None
    minutes_to_event: float | None
    recheck_at: str | None
    size_multiplier: float


def empty_result(decision: str = "ALLOW", reason_codes: list[str] | None = None) -> GuardResult:
    return {"decision": decision, "reason_codes": reason_codes or [], "nearest_event": None, "minutes_to_event": None, "recheck_at": None, "size_multiplier": 1.0}


_empty = empty_result


def _tier_for(event: dict[str, Any]) -> str:
    if event.get("is_central_bank_event"):
        return "central_bank"
    impact = str(event.get("impact") or "unknown").lower()
    if impact == "high":
        return "high"
    if impact == "medium":
        return "medium"
    return "low"


def evaluate(currencies: list[str], now: datetime, events: list[dict[str, Any]], config: EconomicIntelligenceConfig, *, spread_normalized: bool | None = None) -> GuardResult:
    """Deterministic ALLOW/BLOCK/DELAY/REDUCE_SIZE/MANAGE_EXISTING_ONLY for scheduled Forex Factory events.

    `events` must already be filtered/queried by caller (pure function, no I/O). Considers
    both currencies of the pair. Returns the single most restrictive outcome across all
    relevant events near `now`. `spread_normalized=False` extends a post-event DELAY for
    high-impact/central-bank events even after the configured delay window elapses (bounded
    to a 4x safety cap so a missing spread reading can't block forever).
    """
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    wanted = {c.upper() for c in currencies}
    relevant = [event for event in events if str(event.get("currency") or "").upper() in wanted]
    if not relevant:
        return _empty("ALLOW", [])

    best: GuardResult = _empty("ALLOW", [])
    for event in relevant:
        scheduled = event.get("scheduled_at_utc")
        if isinstance(scheduled, str):
            try:
                scheduled = datetime.fromisoformat(scheduled)
            except Exception:
                continue
        if not isinstance(scheduled, datetime):
            continue
        scheduled = scheduled if scheduled.tzinfo else scheduled.replace(tzinfo=timezone.utc)
        minutes_to_event = (scheduled - now).total_seconds() / 60
        tier = _tier_for(event)
        candidate = _evaluate_single(tier, minutes_to_event, event, config, spread_normalized=spread_normalized)
        if _PRECEDENCE[candidate["decision"]] > _PRECEDENCE[best["decision"]]:
            best = candidate
        elif _PRECEDENCE[candidate["decision"]] == _PRECEDENCE[best["decision"]] and candidate["decision"] != "ALLOW":
            best["reason_codes"] = sorted(set(best["reason_codes"]) | set(candidate["reason_codes"]))
    return best


def _evaluate_single(tier: str, minutes_to_event: float, event: dict[str, Any], config: EconomicIntelligenceConfig, *, spread_normalized: bool | None = None) -> GuardResult:
    if tier == "central_bank":
        block_before, delay_after = config.ff_central_bank_block_before_minutes, config.ff_central_bank_delay_after_minutes
        reason_prefix = "CENTRAL_BANK_EVENT"
    elif tier == "high":
        block_before, delay_after = config.ff_high_impact_block_before_minutes, config.ff_high_impact_delay_after_minutes
        reason_prefix = "HIGH_IMPACT_EVENT"
    elif tier == "medium":
        block_before, delay_after = config.ff_medium_impact_block_before_minutes, config.ff_medium_impact_delay_after_minutes
        reason_prefix = "MEDIUM_IMPACT_EVENT"
    else:
        return _empty("ALLOW", [])

    extended_delay_after = delay_after
    if config.ff_require_spread_normalization and spread_normalized is False and tier in {"high", "central_bank"}:
        extended_delay_after = delay_after * 4

    if -extended_delay_after <= minutes_to_event <= block_before:
        if minutes_to_event >= 0:
            decision = "BLOCK" if tier != "medium" else "REDUCE_SIZE"
            reason = f"{reason_prefix}_PRE_BLOCK" if decision == "BLOCK" else f"{reason_prefix}_PRE_REDUCE"
        elif minutes_to_event < -delay_after:
            decision = "DELAY"
            reason = "SPREAD_NOT_NORMALIZED"
        else:
            decision = "DELAY"
            reason = f"{reason_prefix}_POST_DELAY"
        recheck_at = (datetime.now(timezone.utc) + timedelta(minutes=max(1, block_before if minutes_to_event >= 0 else extended_delay_after + minutes_to_event * -1))).isoformat()
        return {
            "decision": decision,
            "reason_codes": [reason],
            "nearest_event": event,
            "minutes_to_event": minutes_to_event,
            "recheck_at": recheck_at,
            "size_multiplier": 0.5 if decision == "REDUCE_SIZE" else 1.0,
        }
    if tier != "central_bank" and 0 <= minutes_to_event <= block_before * 2:
        return {"decision": "ALLOW", "reason_codes": [f"{reason_prefix}_APPROACHING_MONITOR"], "nearest_event": event, "minutes_to_event": minutes_to_event, "recheck_at": None, "size_multiplier": 1.0}
    return _empty("ALLOW", [])


def combine(*results: GuardResult) -> GuardResult:
    """Precedence reducer: BLOCK > MANAGE_EXISTING_ONLY > DELAY > REDUCE_SIZE > ALLOW.

    OpenAI (or any other advisory) is folded in through this same reducer and can never
    move the combined result toward ALLOW.
    """
    best = _empty("ALLOW", [])
    for result in results:
        if not result:
            continue
        if _PRECEDENCE[result["decision"]] > _PRECEDENCE[best["decision"]]:
            best = dict(result)  # type: ignore[assignment]
        elif result["decision"] == best["decision"] and result["decision"] != "ALLOW":
            best["reason_codes"] = sorted(set(best.get("reason_codes") or []) | set(result.get("reason_codes") or []))
            if result.get("size_multiplier", 1.0) < best.get("size_multiplier", 1.0):
                best["size_multiplier"] = result["size_multiplier"]
    return best  # type: ignore[return-value]


GUARD_MODES = ("disabled", "shadow", "enforce")


def apply_guard_mode(combined: GuardResult, mode: str) -> tuple[GuardResult, bool]:
    """Decouples the *evaluated* economic decision from its *effect* on order approval.

    - enforce: today's behavior -- the evaluated decision is returned unchanged.
    - shadow: evaluate and persist the true decision (the caller's `combined` is what's
      recorded as the shadow decision), but always return ALLOW so nothing about order
      approval or adaptive-manager suppression changes.
    - disabled: same neutral ALLOW return; the provider layer may still ingest.

    Returns (effective_guard, execution_changed_by_economic) where the second element is
    True only when the *returned* guard actually restricted something (decision != ALLOW) --
    which is by construction always False in shadow/disabled mode, and mirrors `combined`'s
    restrictiveness in enforce mode.
    """
    if mode not in GUARD_MODES:
        mode = "enforce"
    if mode == "enforce":
        return combined, combined["decision"] != "ALLOW"
    reason = "ECONOMIC_GUARD_DISABLED" if mode == "disabled" else "ECONOMIC_GUARD_SHADOW_MODE"
    return empty_result("ALLOW", [reason]), False


def from_blockers(blockers: list[str]) -> GuardResult:
    """Adapts a plain blocker-string list (e.g. decision_context's context_blockers) into a GuardResult
    so it can participate in the same precedence combine() as the new economic guard."""
    if not blockers:
        return _empty("ALLOW", [])
    return _empty("BLOCK", list(blockers))
