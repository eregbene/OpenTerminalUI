from __future__ import annotations

import re
from dataclasses import dataclass

from backend.brokers.mt5.models import MT5Symbol


ISO_CURRENCIES = {
    "USD",
    "EUR",
    "GBP",
    "JPY",
    "CHF",
    "CAD",
    "AUD",
    "NZD",
    "SEK",
    "NOK",
    "DKK",
    "ZAR",
    "MXN",
    "TRY",
    "CNH",
    "SGD",
    "HKD",
    "PLN",
    "HUF",
    "CZK",
    "ILS",
}

MAJORS = {"EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"}
MINORS = {
    "EURGBP",
    "EURJPY",
    "EURCHF",
    "EURCAD",
    "EURAUD",
    "EURNZD",
    "GBPJPY",
    "GBPCHF",
    "GBPCAD",
    "GBPAUD",
    "GBPNZD",
    "AUDJPY",
    "AUDCAD",
    "AUDCHF",
    "AUDNZD",
    "NZDJPY",
    "NZDCAD",
    "NZDCHF",
    "CADJPY",
    "CADCHF",
    "CHFJPY",
}
NON_FOREX_MARKERS = {"XAU", "XAG", "BTC", "ETH", "US30", "NAS", "SPX", "GER", "UKOIL", "WTI"}
METALS = {"XAUUSD", "XAGUSD"}


@dataclass(frozen=True)
class SymbolClassification:
    broker_symbol: str
    canonical_pair: str | None
    base_currency: str | None
    quote_currency: str | None
    asset_class: str
    is_forex: bool
    naming_pattern: str


def classify_symbol(symbol: MT5Symbol) -> SymbolClassification:
    name = symbol.symbol.upper()
    base = (symbol.currency_base or "").upper() or None
    quote = (symbol.currency_profit or "").upper() or None
    metal_pair = f"{base or ''}{quote or ''}"
    if metal_pair in METALS:
        return SymbolClassification(name, metal_pair, base, quote, "METAL", True, naming_pattern(name, metal_pair))
    if base in NON_FOREX_MARKERS or quote in NON_FOREX_MARKERS:
        return _not_forex(name, "non_fx_currency")
    if base in ISO_CURRENCIES and quote in ISO_CURRENCIES and base != quote:
        canonical = f"{base}{quote}"
        return SymbolClassification(name, canonical, base, quote, forex_class(canonical), True, naming_pattern(name, canonical))

    letters = re.sub(r"[^A-Z]", "", name)
    for start in range(0, max(0, len(letters) - 5)):
        candidate = letters[start : start + 6]
        candidate_base = candidate[:3]
        candidate_quote = candidate[3:]
        if candidate_base in ISO_CURRENCIES and candidate_quote in ISO_CURRENCIES and candidate_base != candidate_quote:
            return SymbolClassification(name, candidate, candidate_base, candidate_quote, forex_class(candidate), True, naming_pattern(name, candidate))
    for metal in METALS:
        if metal in letters:
            return SymbolClassification(name, metal, metal[:3], metal[3:], "METAL", True, naming_pattern(name, metal))
    return _not_forex(name, "unknown")


def forex_class(canonical_pair: str) -> str:
    pair = canonical_pair.upper()
    if pair in MAJORS:
        return "MAJOR"
    if pair in MINORS:
        return "MINOR"
    return "EXOTIC"


def naming_pattern(broker_symbol: str, canonical_pair: str) -> str:
    if broker_symbol == canonical_pair:
        return "EXACT"
    if broker_symbol.startswith(canonical_pair):
        return "SUFFIX"
    if broker_symbol.endswith(canonical_pair):
        return "PREFIX"
    return "EMBEDDED"


def _not_forex(name: str, pattern: str) -> SymbolClassification:
    return SymbolClassification(name, None, None, None, "UNKNOWN", False, pattern)
