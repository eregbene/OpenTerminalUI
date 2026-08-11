from __future__ import annotations

import re
from dataclasses import dataclass

from backend.decision_context.mapping import canonical_pair, symbol_parts

MAPPING_VERSION = "ff_currency_rules_v1"

SUPPORTED_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"}
_KNOWN_CURRENCIES = SUPPORTED_CURRENCIES | {"XAU", "XAG"}

# Regional Federal Reserve Bank branches. Their research/survey publications must NEVER be
# promoted to CENTRAL_BANK_EVENT_PRE_BLOCK merely because "fed" appears in the title -- e.g.
# "Cleveland Fed Inflation Expectations" is a regional research release, not a monetary-policy
# decision. Checked BEFORE the institution match below, as a hard veto.
REGIONAL_FED_MARKERS: tuple[str, ...] = (
    "boston fed",
    "new york fed",
    "ny fed",
    "philadelphia fed",
    "philly fed",
    "cleveland fed",
    "richmond fed",
    "atlanta fed",
    "chicago fed",
    "st louis fed",
    "st. louis fed",
    "minneapolis fed",
    "kansas city fed",
    "kansas fed",
    "dallas fed",
    "san francisco fed",
    "empire state",
    "empire manufacturing",
)

# National/supranational monetary-policy authorities. Bare "fed" (Federal Reserve/FOMC
# shorthand, e.g. "Fed Chair Powell Testimony") also counts as an institution match, but only
# once REGIONAL_FED_MARKERS above has already been checked and ruled out.
POLICY_INSTITUTIONS: tuple[str, ...] = (
    "fomc",
    "federal reserve",
    "ecb",
    "european central bank",
    "boe",
    "bank of england",
    "boj",
    "bank of japan",
    "boc",
    "bank of canada",
    "rba",
    "reserve bank of australia",
    "rbnz",
    "reserve bank of new zealand",
    "snb",
    "swiss national bank",
)

# Genuine monetary-policy EVENT TYPES. Must co-occur with a POLICY_INSTITUTIONS match (or bare
# "fed") to count -- the institution alone is never sufficient (that is the bug this replaces:
# a regional research release or a generic "Fed" mention is not a policy decision).
POLICY_EVENT_TYPES: tuple[str, ...] = (
    "interest rate decision",
    "rate decision",
    "rate statement",
    "monetary policy statement",
    "monetary policy summary",
    "policy statement",
    "statement",
    "press conference",
    "minutes",
    "federal funds rate",
    "policy rate",
    "cash rate",
    "official cash rate",
)

# Explicitly scheduled major Chair/Governor/President testimony or monetary-policy speeches --
# requires an institution match, a leadership title, AND a speech/testimony term together, so
# an ordinary policymaker's routine remarks ("Fed Speaks") don't get promoted to critical.
LEADERSHIP_TITLES: tuple[str, ...] = ("chair", "chairman", "chairwoman", "governor", "president")
SPEECH_TERMS: tuple[str, ...] = ("testimony", "speaks", "speech")

# Currently-serving central-bank chiefs whose calendar entries are sometimes titled with just
# a surname (e.g. "Powell Speaks", with no "Fed"/"Chair" in the title at all). A surname match
# plus a SPEECH_TERMS match is treated as equivalent to an institution+leadership-title match.
# Maintenance note: this list goes stale on a leadership change and must be updated then --
# it is deliberately small and explicit rather than a broad name-guessing heuristic.
KNOWN_POLICYMAKER_SURNAMES: tuple[str, ...] = ("powell", "lagarde", "bailey", "ueda")

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
    """True only for genuine monetary-policy events -- FOMC/ECB/BoE/BoJ/BoC/RBA/RBNZ/SNB rate
    decisions, policy statements, minutes, press conferences, and explicitly scheduled major
    Chair/Governor/President testimony or monetary-policy speeches.

    Deliberately NOT triggered by a bare institution mention (e.g. "Fed", "central bank") or a
    regional Federal Reserve Bank publication (e.g. "Cleveland Fed Inflation Expectations",
    "Philly Fed Manufacturing Index") -- those get their protection window from the provider's
    own impact classification instead (see calendar_guard._tier_for), not this central-bank
    tier. A source containing "Federal Reserve" is not, by itself, evidence of a policy event;
    the event TYPE must also be present.
    """
    text = str(event_name or "").lower()
    if any(marker in text for marker in REGIONAL_FED_MARKERS):
        return False
    has_speech_term = any(term in text for term in SPEECH_TERMS)
    if has_speech_term and any(surname in text for surname in KNOWN_POLICYMAKER_SURNAMES):
        return True
    has_institution = any(institution in text for institution in POLICY_INSTITUTIONS) or "fed" in text
    if not has_institution:
        return False
    if any(event_type in text for event_type in POLICY_EVENT_TYPES):
        return True
    if has_speech_term and any(title in text for title in LEADERSHIP_TITLES):
        return True
    return False


def severity_label_for_event(event: dict) -> tuple[str, str]:
    """(normalized_internal_severity, classification_reason) for structured block logging --
    see backend/economic_intelligence/service.py's use at the point a BLOCK/DELAY/REDUCE_SIZE
    decision is logged. Reads the event's ALREADY-persisted is_central_bank_event/impact
    fields (set once at ingestion by is_central_bank_event() above) rather than reclassifying,
    so the logged reason always matches the decision that was actually made."""
    if event.get("is_central_bank_event"):
        return "CENTRAL_BANK_POLICY_CRITICAL", "matched a monetary-policy event (institution + policy-event-type, or leadership testimony/speech)"
    impact = str(event.get("impact") or "unknown").lower()
    if impact == "high":
        return "MACRO_DATA_HIGH", "provider-classified high-impact economic release"
    if impact == "medium":
        return "MACRO_DATA_MEDIUM", "provider-classified medium-impact economic release"
    if impact == "low":
        return "MACRO_DATA_LOW", "provider-classified low-impact release"
    return "INFORMATIONAL", "no provider impact classification available; treated as informational"


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
