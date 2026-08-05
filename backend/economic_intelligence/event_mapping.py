from __future__ import annotations

import re
from dataclasses import dataclass

from backend.decision_context.mapping import canonical_pair, symbol_parts

MAPPING_VERSION = "ff_currency_rules_v1"

SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"}
_KNOWN_CURRENCIES = SUPPORTED_CURRENCIES | {"XAU", "XAG"}

CENTRAL_BANK_TERMS: tuple[str, ...] = (
    "fed",
    "fomc",
    "federal funds rate",
    "ecb",
    "boe",
    "boj",
    "rba",
    "boc",
    "snb",
    "rbnz",
    "press conference",
    "rate statement",
    "monetary policy statement",
    "interest rate decision",
    "speaks",
    "testimony",
    "speech",
)

# higher_is_positive: True => an actual above forecast is generally currency-positive.
# False => an actual above forecast is generally currency-negative (e.g. unemployment).
# None => direction is not rule-based from the number alone (e.g. speeches).
EVENT_DIRECTION_RULES: dict[str, bool | None] = {
    "gdp": True,
    "employment change": True,
    "nonfarm payrolls": True,
    "non-farm employment change": True,
    "retail sales": True,
    "ism manufacturing pmi": True,
    "ism services pmi": True,
    "unemployment rate": False,
    "jobless claims": False,
    "initial jobless claims": False,
    "unemployment claims": False,
    "crude oil inventories": False,
    "trade balance": True,
    "cpi": None,
    "core cpi": None,
    "inflation": None,
    "speech": None,
    "speaks": None,
    "testimony": None,
    "press conference": None,
}


@dataclass(frozen=True)
class SymbolCurrencies:
    canonical_symbol: str
    base_currency: str
    quote_currency: str
    currencies: tuple[str, str]


def normalize_broker_symbol(symbol: str) -> SymbolCurrencies:
    """Strip broker suffixes/prefixes (e.g. EURUSD.a, EURUSDm, mEURUSD, EURUSD-pro) and return both currencies.

    `decision_context.mapping.canonical_pair()` only strips non-alpha characters and takes
    the first 6 letters, which handles trailing suffixes but not leading prefixes (e.g.
    'mEURUSD' -> 'MEURUS'). This scans every 6-letter window of the alpha-only uppercased
    symbol for the first one where both 3-letter halves are recognized currencies, so
    prefix noise is handled too; falls back to the shared decision_context heuristic when
    no window matches a known currency pair.
    """
    compact = re.sub(r"[^A-Za-z]", "", symbol).upper()
    for start in range(0, max(0, len(compact) - 5)):
        window = compact[start : start + 6]
        base, quote = window[:3], window[3:6]
        if base in _KNOWN_CURRENCIES and quote in _KNOWN_CURRENCIES and base != quote:
            return SymbolCurrencies(window, base, quote, (base, quote))
    parts = symbol_parts(symbol)
    return SymbolCurrencies(parts.canonical_symbol, parts.base_currency, parts.quote_currency, (parts.base_currency, parts.quote_currency))


def canonical_symbol(symbol: str) -> str:
    return canonical_pair(symbol)


def is_central_bank_event(event_name: str) -> bool:
    text = str(event_name or "").lower()
    return any(term in text for term in CENTRAL_BANK_TERMS)


def normalize_event_name(raw_name: str) -> str:
    text = re.sub(r"\s+", " ", str(raw_name or "")).strip().lower()
    return text


def higher_is_positive(normalized_name: str) -> bool | None:
    """Returns whether an actual-above-forecast surprise is currency-positive for this event type.

    None means direction is not rule-based purely from the number (e.g. speeches, or
    events like inflation whose interpretation depends on central-bank expectations).
    """
    for key, value in EVENT_DIRECTION_RULES.items():
        if key in normalized_name:
            return value
    return None
