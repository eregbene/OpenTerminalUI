from __future__ import annotations

import re
from dataclasses import dataclass


MAPPING_VERSION = "currency_rules_v1"

CURRENCY_TERMS: dict[str, tuple[str, ...]] = {
    "USD": ("federal reserve", "fed", "fomc", "powell", "us cpi", "us inflation", "nonfarm payrolls", "payrolls", "us employment", "unemployment", "us gdp", "treasury yields", "retail sales", "ism", "us sanctions", "dollar"),
    "EUR": ("european central bank", "ecb", "lagarde", "eurozone inflation", "eurozone cpi", "eurozone gdp", "eurozone employment", "german inflation", "german gdp", "european pmi", "euro"),
    "GBP": ("bank of england", "boe", "uk cpi", "uk inflation", "uk gdp", "uk employment", "uk retail sales", "british economy", "sterling", "pound"),
    "JPY": ("bank of japan", "boj", "yen intervention", "japan ministry of finance", "japan cpi", "japanese inflation", "japanese wages", "japan gdp", "yen"),
    "AUD": ("reserve bank of australia", "rba", "australian cpi", "australian employment", "australian gdp", "china demand", "iron ore", "aussie"),
    "CAD": ("bank of canada", "boc", "canadian cpi", "canadian employment", "canadian gdp", "oil prices", "crude oil", "loonie"),
    "CHF": ("swiss national bank", "snb", "swiss inflation", "swiss cpi", "franc intervention", "franc"),
    "NZD": ("reserve bank of new zealand", "rbnz", "new zealand cpi", "new zealand employment", "new zealand gdp", "dairy prices", "kiwi"),
}

HIGH_IMPACT_TERMS = ("emergency", "intervention", "war", "sanction", "default", "bank crisis", "financial instability", "invasion", "missile", "shock")
ELEVATED_TERMS = ("central bank", "inflation", "interest rate", "employment", "gdp", "election", "tariff", "trade dispute", "policy")


@dataclass(frozen=True)
class SymbolParts:
    canonical_symbol: str
    base_currency: str
    quote_currency: str
    supported: bool


def canonical_pair(symbol: str) -> str:
    compact = re.sub(r"[^A-Za-z]", "", symbol).upper()
    if len(compact) >= 6:
        return compact[:6]
    return compact


def symbol_parts(symbol: str) -> SymbolParts:
    pair = canonical_pair(symbol)
    base = pair[:3]
    quote = pair[3:6]
    return SymbolParts(pair, base, quote, base in CURRENCY_TERMS and quote in CURRENCY_TERMS)


def affected_currencies(text: str, candidates: set[str] | None = None) -> dict[str, list[str]]:
    haystack = text.lower()
    allowed = candidates or set(CURRENCY_TERMS)
    matches: dict[str, list[str]] = {}
    for currency in allowed:
        terms = [term for term in CURRENCY_TERMS.get(currency, ()) if term in haystack]
        if terms:
            matches[currency] = terms
    return matches


def affected_symbols(currencies: set[str], symbols: list[SymbolParts]) -> list[str]:
    return sorted({row.canonical_symbol for row in symbols if row.base_currency in currencies or row.quote_currency in currencies})


def headline_impact(title: str) -> str:
    text = title.lower()
    if any(term in text for term in HIGH_IMPACT_TERMS):
        return "high"
    if any(term in text for term in ELEVATED_TERMS):
        return "medium"
    return "low"


def pair_relative_sentiment(base: str, quote: str, currency_sentiment: dict[str, float]) -> dict[str, object]:
    base_score = float(currency_sentiment.get(base) or 0)
    quote_score = float(currency_sentiment.get(quote) or 0)
    relative = base_score - quote_score
    return {
        "base": base,
        "quote": quote,
        "base_score": base_score,
        "quote_score": quote_score,
        "relative_score": relative,
        "bias": "supports_pair" if relative > 0.15 else "weakens_pair" if relative < -0.15 else "neutral",
    }
