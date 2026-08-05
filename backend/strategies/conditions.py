from __future__ import annotations

ALLOWED_OPERATORS = {
    "eq",
    "neq",
    "gt",
    "gte",
    "lt",
    "lte",
    "between",
    "in",
    "not_in",
    "exists",
    "is_true",
    "is_false",
}

FEATURE_PREFIXES = (
    "price.",
    "indicator.",
    "structure.",
    "liquidity.",
    "fvg.",
    "order_block.",
    "dealing_range.",
    "session.",
    "market_data.",
)


def is_known_feature(feature: str) -> bool:
    return feature.startswith(FEATURE_PREFIXES)
