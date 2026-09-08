from __future__ import annotations

import hashlib
from typing import Any

BENSIM_COMMENT_PREFIXES = ("BENSIM_AUTO", "BSM|")


def _get_value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def is_bensim_comment(comment: Any) -> bool:
    text = str(comment or "")
    return text.startswith(BENSIM_COMMENT_PREFIXES)


def is_bensim_owned_record(record: Any, *, bensim_magic: int | None = None) -> bool:
    magic = _get_value(record, "magic")
    if bensim_magic is not None:
        try:
            if int(magic) == int(bensim_magic):
                return True
        except Exception:
            pass
    return is_bensim_comment(_get_value(record, "comment"))


def is_bensim_owned_position(position: Any, *, bensim_magic: int | None = None) -> bool:
    return is_bensim_owned_record(position, bensim_magic=bensim_magic)


def is_bensim_owned_order(order: Any, *, bensim_magic: int | None = None) -> bool:
    return is_bensim_owned_record(order, bensim_magic=bensim_magic)


def bsi_v3_evidence(candidate: dict[str, Any]) -> dict[str, Any]:
    context = candidate.get("context") or {}
    evidence = context.get("strategy_evidence") or {}
    return evidence if isinstance(evidence, dict) else {}


def bsi_v3_identity_from_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    evidence = bsi_v3_evidence(candidate)
    return {
        "market_thesis_id": evidence.get("bsi_v3_market_thesis_id"),
        "poi_id": evidence.get("bsi_v3_poi_id"),
        "entry_opportunity_id": evidence.get("bsi_v3_entry_opportunity_id"),
        "confirmation_id": evidence.get("bsi_v3_confirmation_id"),
        "confluence_plan_ids": evidence.get("v3_confluence_plan_ids") or [],
        "strategy_id": evidence.get("v3_strategy_id") or (candidate.get("context") or {}).get("strategy_id"),
        "symbol": str(candidate.get("canonical_pair") or candidate.get("broker_symbol") or "").upper(),
        "broker_symbol": str(candidate.get("broker_symbol") or candidate.get("canonical_pair") or "").upper(),
        "direction": str(candidate.get("direction") or "").upper(),
    }


def bsi_v3_execution_id(account_id: str, candidate: dict[str, Any]) -> str:
    identity = bsi_v3_identity_from_candidate(candidate)
    raw = "|".join(
        str(identity.get(key) or "")
        for key in ("entry_opportunity_id", "confirmation_id", "symbol", "direction")
    )
    digest = hashlib.sha256(f"{account_id}|{raw}".encode("utf-8")).hexdigest()[:16]
    return f"bsi_v3_exec:{account_id}:{digest}"


def position_direction(position: Any) -> str:
    value = _get_value(position, "type")
    try:
        return "LONG" if int(value) in {0, 2, 4, 6} else "SHORT"
    except Exception:
        return "UNKNOWN"
