from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

_NON_NUMERIC_TOKENS = {"", "tentative", "all day", "n/a", "na", "-", "--"}
_SCALE_SUFFIXES = {"k": 1_000.0, "m": 1_000_000.0, "b": 1_000_000_000.0, "t": 1_000_000_000_000.0}
_NUMERIC_RE = re.compile(r"^([+-]?)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmMbBtT%]?)$")


@dataclass(frozen=True)
class ParsedValue:
    value: float | None
    unit: str | None
    scale: float
    confidence: float
    failure_reason: str | None


def parse_numeric_value(raw: Any) -> ParsedValue:
    """Safely parse Forex Factory style actual/forecast/previous values.

    Handles percentages, K/M/B/T suffixes, signed numbers, empty strings,
    "Tentative"/"All Day" placeholders, and locale punctuation. Never raises.
    """
    if raw is None:
        return ParsedValue(None, None, 1.0, 0.0, "missing_value")
    text = str(raw).strip()
    normalized = text.replace(",", "").replace("−", "-")
    lowered = normalized.strip().lower()
    if lowered in _NON_NUMERIC_TOKENS:
        return ParsedValue(None, None, 1.0, 0.0, "non_numeric_placeholder" if lowered else "missing_value")
    match = _NUMERIC_RE.match(normalized.strip())
    if not match:
        return ParsedValue(None, None, 1.0, 0.0, "unparseable_text")
    sign, digits, suffix = match.groups()
    try:
        base = float(digits)
    except Exception:
        return ParsedValue(None, None, 1.0, 0.0, "unparseable_text")
    if sign == "-":
        base = -base
    unit = None
    scale = 1.0
    suffix_lower = suffix.lower()
    if suffix_lower == "%":
        unit = "percent"
    elif suffix_lower in _SCALE_SUFFIXES:
        unit = suffix_lower.upper()
        scale = _SCALE_SUFFIXES[suffix_lower]
        base *= scale
    return ParsedValue(base, unit, scale, 1.0, None)


def normalize_timestamp_to_utc(value: Any, *, source_timezone: str | None = None) -> tuple[datetime | None, str | None]:
    """Normalize a raw timestamp to UTC, preserving the original source timezone label.

    Returns (utc_datetime_or_none, source_timezone_label). Never raises.
    """
    if value is None:
        return None, source_timezone
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc), source_timezone or "epoch"
        except Exception:
            return None, source_timezone
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)), source_timezone
    text = str(value).strip()
    if not text:
        return None, source_timezone
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)), source_timezone
        except Exception:
            continue
    return None, source_timezone


def missing_fields(row: dict[str, Any], required: tuple[str, ...]) -> list[str]:
    return [field for field in required if row.get(field) in (None, "")]
