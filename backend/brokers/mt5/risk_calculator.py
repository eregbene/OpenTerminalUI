from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from backend.brokers.mt5.models import MT5Symbol
from backend.brokers.mt5.orm import MT5RiskMetadataMismatchORM
from backend.shared.db import SessionLocal

logger = logging.getLogger(__name__)

# The single authoritative definition of "what does 1.0 lot of this MT5 symbol lose if price
# moves from entry to stop" in this codebase. Exists because a real incident (XAUUSD ticket
# 57873187767) proved that trusting ONE broker-reported field in isolation is unsafe: this
# account's trade_tick_value for XAUUSD (0.1) implied $10/point/lot, while the broker's own
# order_calc_profit (ground truth) and trade_contract_size both agreed on $100/point/lot -- a
# silent 10x under-estimate that oversized a position 10x against its risk budget. No module in
# this codebase should compute MT5 monetary risk any other way.
#
# Cross-currency + XAUUSD hardening (this revision): a second, DIFFERENT incident-shaped bug was
# found live on this account for EURJPY/GBPJPY-style crosses (contract_size silently computed a
# quote-currency figure and compared it to account-currency figures -- fixed by either converting
# it via a live broker quote or excluding it, never comparing mismatched currencies) and for
# XAUUSD again (trade_tick_value=0.1 vs a metadata-derived correct value of 1.0 -- a genuine 10x
# broker-metadata error, confirmed live: order_calc_profit and trade_contract_size agree exactly
# with each other and disagree with trade_tick_value by exactly 10x, for every affected symbol
# tested). Both are now caught by GENERAL, metadata-derived checks -- see
# resolve_conversion_rate() and _validate_tick_value_self_consistency() -- with NO symbol-specific
# hardcoding anywhere in this module.
WARNING_MISMATCH = "SYMBOL_RISK_METADATA_MISMATCH"
CRITICAL_MISMATCH = "SYMBOL_RISK_METADATA_CRITICAL_MISMATCH"
NO_VALID_ESTIMATE = "NO_VALID_RISK_ESTIMATE"
INSUFFICIENT_QUORUM = "INSUFFICIENT_RISK_METHOD_QUORUM"
TICK_VALUE_SELF_INCONSISTENT = "TICK_VALUE_INCONSISTENT_WITH_CONTRACT_SIZE"

# Preference order used only to break ties when two methods land on the exact same (maximum)
# value -- selection itself is "most conservative available", never a blind hierarchy pick. See
# module docstring: hierarchy is about what we'd LIKE to trust most, not what we DO trust most.
METHOD_ORDER: tuple[str, ...] = ("order_calc_profit", "contract_size", "tick_value")

# --- Explicit risk-method quorum policy (documented, not implicit) ---
# - 0 valid methods            -> NO_VALID_ESTIMATE, fail closed.
# - 1 valid method             -> INSUFFICIENT_QUORUM, fail closed. Never trust a single
#   broker-reported field alone, even order_calc_profit -- this is the module's founding
#   principle, now enforced structurally rather than only by convention.
# - >=2 valid, independent methods, agreeing within `warning_pct`   -> OK, select the most
#   conservative (largest).
# - >=2 valid methods, disagreeing between warning_pct/critical_pct -> OK but WARNING_MISMATCH
#   flagged (still selects the most conservative estimate; not blocked).
# - >=2 valid methods, disagreeing >= critical_pct                  -> CRITICAL_MISMATCH,
#   fail closed -- UNLESS the disagreement is fully explained and resolved by
#   _validate_tick_value_self_consistency() excluding a dimensionally-inconsistent tick_value
#   estimate first, in which case the quorum is recomputed from the remaining trustworthy
#   methods (see calculate_canonical_loss_per_lot). Excluding a proven-inconsistent method is
#   not "trusting a lone survivor" -- the remaining methods must still independently agree.
MIN_TRUSTED_METHODS = 2
# Tolerance for the tick_value <-> contract_size dimensional self-consistency check (see
# _validate_tick_value_self_consistency). Deliberately generous relative to the ~90% disagreement
# a genuine broker-metadata error produces (confirmed live for XAUUSD), but tight enough that
# ordinary FX bid/ask/rounding noise on cross-currency conversions never trips it.
TICK_VALUE_SELF_CONSISTENCY_TOLERANCE_PCT = 20.0


@dataclass(frozen=True)
class MethodEstimate:
    method: str
    loss_per_lot: Decimal | None
    available: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "loss_per_lot": str(self.loss_per_lot) if self.loss_per_lot is not None else None,
            "available": self.available,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CanonicalRiskResult:
    selected_loss_per_lot: Decimal | None
    selected_method: str | None
    estimates: dict[str, MethodEstimate] = field(default_factory=dict)
    max_disagreement_pct: float | None = None
    warning_codes: list[str] = field(default_factory=list)
    blocked: bool = False
    block_reason: str | None = None
    quorum_met: bool = False
    trusted_method_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_loss": str(self.selected_loss_per_lot) if self.selected_loss_per_lot is not None else None,
            "selected_method": self.selected_method,
            "estimates": {name: est.to_dict() for name, est in self.estimates.items()},
            "max_disagreement_pct": self.max_disagreement_pct,
            "warning_codes": list(self.warning_codes),
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "quorum_met": self.quorum_met,
            "trusted_method_count": self.trusted_method_count,
        }


def _guess_broker_conversion_symbol(instrument_broker_symbol: str, conversion_pair_canonical: str) -> str | None:
    """Best-effort: derive a broker symbol for `conversion_pair_canonical` (e.g. "USDJPY") by
    reusing the SAME prefix/suffix wrapping this broker applies to the instrument already being
    sized (e.g. instrument_broker_symbol="EURJPY.a" -> suffix=".a"), since one MT5 broker
    applies one consistent naming convention across its whole symbol set. Returns None (never
    guesses blindly) when the instrument's own canonical code can't be located inside its own
    broker symbol, so the wrapping pattern can't be determined."""
    from backend.economic_intelligence.event_mapping import normalize_broker_symbol

    try:
        parts = normalize_broker_symbol(instrument_broker_symbol)
    except Exception:
        return None
    canonical = parts.canonical_symbol
    if not canonical:
        return None
    idx = instrument_broker_symbol.upper().find(canonical.upper())
    if idx == -1:
        return None
    prefix = instrument_broker_symbol[:idx]
    suffix = instrument_broker_symbol[idx + len(canonical):]
    return f"{prefix}{conversion_pair_canonical}{suffix}"


async def resolve_conversion_rate(*, from_currency: str, to_currency: str, instrument_broker_symbol: str, adapter: Any) -> tuple[Decimal | None, str]:
    """One canonical currency-conversion helper (Part 3): given a live `adapter` (anything with
    an async `latest_tick(symbol) -> quote with .bid/.ask`), resolves a multiplicative rate R
    such that `amount_in_to_currency = amount_in_from_currency * R`. Tries the direct pair
    (`{to}{from}`, e.g. "USDJPY" when converting JPY->USD -- MT5's standard quote-currency-second
    convention, R = 1/price) then the inverse pair (`{from}{to}`, e.g. "JPYUSD", R = price
    directly) using the SAME broker symbol suffix/prefix as the instrument being sized. Returns
    (None, "conversion_unavailable_no_broker_quote") -- never a guessed/assumed rate -- when
    neither pair resolves to a live, valid quote. Works for any account currency, not just USD:
    this is a pure from/to currency pair, no USD assumption anywhere."""
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()
    if from_currency == to_currency:
        return Decimal("1"), "no_conversion_needed"

    candidates = ((f"{to_currency}{from_currency}", True), (f"{from_currency}{to_currency}", False))
    for pair_canonical, invert in candidates:
        guessed_symbol = _guess_broker_conversion_symbol(instrument_broker_symbol, pair_canonical)
        if guessed_symbol is None:
            continue
        try:
            quote = await adapter.latest_tick(guessed_symbol)
        except Exception:
            continue
        if quote is None or quote.bid is None or quote.ask is None:
            continue
        bid, ask = Decimal(str(quote.bid)), Decimal(str(quote.ask))
        if bid <= 0 or ask <= 0:
            continue
        mid = (bid + ask) / Decimal("2")
        if invert:
            # e.g. USDJPY quotes JPY per 1 USD -- converting an amount FROM JPY TO USD divides.
            return (Decimal("1") / mid), f"direct_pair:{guessed_symbol}"
        # e.g. JPYUSD quotes USD per 1 JPY directly -- converting FROM JPY TO USD multiplies.
        return mid, f"inverse_pair:{guessed_symbol}"
    return None, "conversion_unavailable_no_broker_quote"


async def calculate_canonical_loss_per_lot(
    *,
    direction: str,
    entry: Decimal,
    stop: Decimal,
    symbol_info: MT5Symbol,
    mt5_client: Any | None = None,
    warning_pct: float = 10.0,
    critical_pct: float = 100.0,
    account_currency: str = "USD",
    adapter: Any | None = None,
) -> CanonicalRiskResult:
    """Computes projected monetary loss for 1.0 lot from `entry` to `stop`, using every method
    that has the data to run, and always SELECTS THE LARGEST (most conservative) valid estimate
    among TRUSTED methods -- never the smallest, never a single trusted field in isolation (see
    MIN_TRUSTED_METHODS). `direction` ("LONG"/"SHORT") only matters for the order_calc_profit
    call; the other two methods are direction-agnostic (they work on |entry - stop|).

    Methods (each records its own input units / raw output currency / conversion applied in
    its `detail` string -- Part 2's per-method unit declaration):
      1. order_calc_profit -- broker-native, ALWAYS account-currency (that is what MT5's
         order_calc_profit contractually returns). Requires `mt5_client`. Skipped (not an
         error) when unavailable.
      2. contract_size -- stop_distance * symbol_info.trade_contract_size. Raw output is in the
         symbol's PROFIT (quote) currency. When that differs from `account_currency`, this
         method is converted via resolve_conversion_rate() (requires `adapter`) or, if no
         reliable live conversion can be resolved, marked unavailable with a precise reason --
         never compared across mismatched currencies (Part 4).
      3. tick_value -- (stop_distance / tick_size) * tick_value; trade_tick_value is, per MT5's
         own contract, already expressed in account/deposit currency regardless of profit
         currency, so no conversion is applied here. Cross-validated against contract_size's
         account-currency-normalized figure by _validate_tick_value_self_consistency() -- when
         they are dimensionally inconsistent (confirmed live for this account's XAUUSD: exactly
         10x apart, while order_calc_profit and contract_size agree exactly), tick_value is
         excluded from the quorum with an explicit reason rather than treated as one more
         disagreeing-but-valid method (Part 7).

    Never raises: an unavailable/erroring method just gets `available=False` in its estimate and
    is excluded from selection.
    """
    stop_distance = abs(Decimal(str(entry)) - Decimal(str(stop)))
    order_calc_estimate = await _order_calc_profit_estimate(direction=direction, entry=entry, stop=stop, symbol_name=getattr(symbol_info, "symbol", "") or "", mt5_client=mt5_client)

    conversion_rate: Decimal | None = Decimal("1")
    conversion_source = "no_conversion_needed"
    profit_currency = getattr(symbol_info, "currency_profit", None)
    broker_symbol = getattr(symbol_info, "symbol", None)
    if account_currency and profit_currency and profit_currency.upper() != account_currency.upper():
        if adapter is not None and broker_symbol:
            conversion_rate, conversion_source = await resolve_conversion_rate(from_currency=profit_currency, to_currency=account_currency, instrument_broker_symbol=broker_symbol, adapter=adapter)
        else:
            conversion_rate, conversion_source = None, "conversion_unavailable_no_adapter"

    contract_estimate = _contract_size_estimate(stop_distance=stop_distance, symbol_info=symbol_info, account_currency=account_currency, conversion_rate=conversion_rate, conversion_source=conversion_source)
    tick_estimate_raw = _tick_value_estimate(stop_distance=stop_distance, symbol_info=symbol_info)
    tick_estimate = _validate_tick_value_self_consistency(tick_estimate_raw, contract_estimate)

    estimates: dict[str, MethodEstimate] = {"order_calc_profit": order_calc_estimate, "contract_size": contract_estimate, "tick_value": tick_estimate}
    return _select_conservative(estimates, warning_pct=warning_pct, critical_pct=critical_pct)


def calculate_conservative_loss_per_lot_sync(
    *,
    entry: Decimal,
    stop: Decimal,
    symbol_info: MT5Symbol,
    warning_pct: float = 10.0,
    critical_pct: float = 100.0,
    account_currency: str = "USD",
) -> CanonicalRiskResult:
    """Sync-callable subset of the canonical calculator: contract_size and tick_value methods
    only (no broker-native order_calc_profit, which requires an event loop, and no live-quote
    currency conversion, which requires an adapter). For contexts that cannot await -- e.g.
    AdaptiveManagementService._sync_position_state's legacy-position reconstruction path, which
    runs inside a synchronous ORM-sync method. Under the explicit quorum policy
    (MIN_TRUSTED_METHODS=2), this sync path can only ever produce a non-blocked result when BOTH
    of its two methods are available and dimensionally consistent -- a cross-currency symbol
    with no adapter available here will correctly fail closed rather than size off a single
    unconfirmed field. Callers that can await should prefer calculate_canonical_loss_per_lot."""
    stop_distance = abs(Decimal(str(entry)) - Decimal(str(stop)))
    contract_estimate = _contract_size_estimate(stop_distance=stop_distance, symbol_info=symbol_info, account_currency=account_currency, conversion_rate=None, conversion_source="conversion_unavailable_sync_no_adapter")
    tick_estimate_raw = _tick_value_estimate(stop_distance=stop_distance, symbol_info=symbol_info)
    tick_estimate = _validate_tick_value_self_consistency(tick_estimate_raw, contract_estimate)
    estimates: dict[str, MethodEstimate] = {"contract_size": contract_estimate, "tick_value": tick_estimate}
    return _select_conservative(estimates, warning_pct=warning_pct, critical_pct=critical_pct)


def _validate_tick_value_self_consistency(tick_estimate: MethodEstimate, contract_estimate: MethodEstimate) -> MethodEstimate:
    """Part 7: trade_tick_value and (an account-currency-comparable) trade_contract_size are two
    independent broker-reported descriptions of the exact same contract -- for a correctly
    configured symbol they must reproduce essentially the same loss-per-lot. Confirmed live on
    this account across every healthy tested FX pair (majors, USD-base crosses, JPY crosses all
    agree within noise); XAUUSD's trade_tick_value was found to be exactly 10x too small versus
    trade_contract_size while order_calc_profit independently agreed with trade_contract_size --
    i.e. trade_tick_value itself is the broken field for that symbol on this account, not
    contract_size. This is a purely metadata-derived check (no symbol name, no hardcoded
    multiplier) that generalizes to any symbol/broker with the same class of error (e.g. other
    CFDLEVERAGE-mode metals)."""
    if not tick_estimate.available or not contract_estimate.available or tick_estimate.loss_per_lot is None or contract_estimate.loss_per_lot is None or contract_estimate.loss_per_lot <= 0:
        return tick_estimate
    disagreement_pct = abs(tick_estimate.loss_per_lot - contract_estimate.loss_per_lot) / contract_estimate.loss_per_lot * Decimal("100")
    if disagreement_pct > Decimal(str(TICK_VALUE_SELF_CONSISTENCY_TOLERANCE_PCT)):
        return MethodEstimate(
            "tick_value", None, False,
            f"trade_tick_value inconsistent with trade_contract_size ({float(disagreement_pct):.1f}% apart, tolerance {TICK_VALUE_SELF_CONSISTENCY_TOLERANCE_PCT}%) -- {TICK_VALUE_SELF_INCONSISTENT}",
        )
    return tick_estimate


def _select_conservative(estimates: dict[str, MethodEstimate], *, warning_pct: float, critical_pct: float) -> CanonicalRiskResult:
    valid = [(name, est.loss_per_lot) for name, est in estimates.items() if est.available and est.loss_per_lot is not None and est.loss_per_lot > 0]
    if not valid:
        return CanonicalRiskResult(selected_loss_per_lot=None, selected_method=None, estimates=estimates, max_disagreement_pct=None, warning_codes=[NO_VALID_ESTIMATE], blocked=True, block_reason=NO_VALID_ESTIMATE, quorum_met=False, trusted_method_count=0)

    if len(valid) < MIN_TRUSTED_METHODS:
        # Explicit quorum policy: a single available method, even order_calc_profit, is never
        # trusted alone (module founding principle, now structurally enforced).
        return CanonicalRiskResult(selected_loss_per_lot=None, selected_method=None, estimates=estimates, max_disagreement_pct=None, warning_codes=[INSUFFICIENT_QUORUM], blocked=True, block_reason=INSUFFICIENT_QUORUM, quorum_met=False, trusted_method_count=len(valid))

    values = [v for _, v in valid]
    min_v, max_v = min(values), max(values)
    max_disagreement_pct = float((max_v - min_v) / min_v * 100) if min_v > 0 else None
    candidates_at_max = [name for name, v in valid if v == max_v]
    selected_method = next((m for m in METHOD_ORDER if m in candidates_at_max), candidates_at_max[0])

    warning_codes: list[str] = []
    blocked = False
    block_reason: str | None = None
    if max_disagreement_pct is not None and max_disagreement_pct >= critical_pct:
        warning_codes = [WARNING_MISMATCH, CRITICAL_MISMATCH]
        blocked = True
        block_reason = CRITICAL_MISMATCH
    elif max_disagreement_pct is not None and max_disagreement_pct >= warning_pct:
        warning_codes = [WARNING_MISMATCH]

    return CanonicalRiskResult(
        selected_loss_per_lot=max_v,
        selected_method=selected_method,
        estimates=estimates,
        max_disagreement_pct=max_disagreement_pct,
        warning_codes=warning_codes,
        blocked=blocked,
        block_reason=block_reason,
        quorum_met=True,
        trusted_method_count=len(valid),
    )


async def _order_calc_profit_estimate(*, direction: str, entry: Decimal, stop: Decimal, symbol_name: str, mt5_client: Any | None) -> MethodEstimate:
    if mt5_client is None:
        return MethodEstimate("order_calc_profit", None, False, "no broker client available")
    if not hasattr(mt5_client, "order_calc_profit"):
        return MethodEstimate("order_calc_profit", None, False, "mt5 client has no order_calc_profit")
    order_type = getattr(mt5_client, "ORDER_TYPE_BUY", 0) if direction.upper() == "LONG" else getattr(mt5_client, "ORDER_TYPE_SELL", 1)
    try:
        raw = await asyncio.to_thread(mt5_client.order_calc_profit, order_type, symbol_name, 1.0, float(entry), float(stop))
    except Exception as exc:
        return MethodEstimate("order_calc_profit", None, False, f"order_calc_profit raised {exc.__class__.__name__}")
    if raw is None:
        return MethodEstimate("order_calc_profit", None, False, "order_calc_profit returned None")
    loss = Decimal(str(raw)).copy_abs()
    return MethodEstimate("order_calc_profit", loss, True, "broker-native order_calc_profit(1.0 lot, entry -> stop); output units: account currency (contractual)")


def _contract_size_estimate(*, stop_distance: Decimal, symbol_info: MT5Symbol, account_currency: str | None = None, conversion_rate: Decimal | None = Decimal("1"), conversion_source: str = "no_conversion_needed") -> MethodEstimate:
    # getattr(..., None), not direct attribute access: symbol_info is sometimes a lightweight
    # duck-typed test double (or a partial payload from a degraded broker response) that only
    # defines the fields IT cares about, not the full MT5Symbol shape -- this must degrade to
    # "unavailable" for THIS method, never raise and take out the whole calculation.
    contract_size = getattr(symbol_info, "trade_contract_size", None)
    if not contract_size or contract_size <= 0:
        return MethodEstimate("contract_size", None, False, "trade_contract_size unavailable")
    # stop_distance x trade_contract_size is denominated in the symbol's PROFIT (quote) currency,
    # not necessarily the account currency. Correct directly only when they match; otherwise a
    # resolved live conversion rate is required (Part 3/4) -- never compared across mismatched
    # currencies, and never guessed.
    profit_currency = getattr(symbol_info, "currency_profit", None)
    raw_loss = (stop_distance * contract_size).copy_abs()  # units: profit_currency
    if account_currency and profit_currency and profit_currency.upper() != account_currency.upper():
        if conversion_rate is None or conversion_rate <= 0:
            return MethodEstimate("contract_size", None, False, f"profit currency {profit_currency} != account currency {account_currency}; no reliable conversion rate available ({conversion_source})")
        converted_loss = (raw_loss * conversion_rate).quantize(Decimal("0.0001"))
        return MethodEstimate("contract_size", converted_loss, True, f"stop_distance x trade_contract_size({contract_size}) [{profit_currency}] x conversion_rate({conversion_rate}, {conversion_source}) -> {account_currency}")
    return MethodEstimate("contract_size", raw_loss, True, f"stop_distance x trade_contract_size({contract_size}); output units: {profit_currency or account_currency or 'account currency'} (no conversion needed)")


def _tick_value_estimate(*, stop_distance: Decimal, symbol_info: MT5Symbol) -> MethodEstimate:
    tick_size = getattr(symbol_info, "trade_tick_size", None) or getattr(symbol_info, "point", None)
    tick_value = getattr(symbol_info, "trade_tick_value_loss", None) or getattr(symbol_info, "trade_tick_value", None) or getattr(symbol_info, "trade_tick_value_profit", None)
    if not tick_size or tick_size <= 0 or not tick_value or tick_value <= 0:
        return MethodEstimate("tick_value", None, False, "trade_tick_size/trade_tick_value unavailable")
    loss = ((stop_distance / tick_size) * tick_value).copy_abs()
    return MethodEstimate("tick_value", loss, True, f"(stop_distance/tick_size({tick_size})) x tick_value({tick_value}); output units: account currency (contractual, MT5 trade_tick_value is deposit-currency by definition)")


# --- Part 12: whole-universe risk-metadata diagnostic audit -----------------------------------
RISK_METADATA_OK = "OK"
RISK_METADATA_DEGRADED_TWO_METHOD = "DEGRADED_TWO_METHOD"
RISK_METADATA_UNSUPPORTED_METHOD = "UNSUPPORTED_METHOD"
RISK_METADATA_CRITICAL_MISMATCH = "CRITICAL_MISMATCH"


def classify_risk_metadata_status(result: CanonicalRiskResult) -> str:
    """Maps a CanonicalRiskResult to one of the four documented health classes (Part 12)."""
    if result.blocked and result.block_reason == CRITICAL_MISMATCH:
        return RISK_METADATA_CRITICAL_MISMATCH
    if result.blocked:
        # NO_VALID_ESTIMATE or INSUFFICIENT_QUORUM -- not enough trustworthy broker metadata to
        # size this symbol at all, distinct from "methods actively disagree".
        return RISK_METADATA_UNSUPPORTED_METHOD
    if result.trusted_method_count <= 2:
        return RISK_METADATA_DEGRADED_TWO_METHOD
    return RISK_METADATA_OK


async def audit_symbol_risk_metadata(*, symbol_info: MT5Symbol, account_currency: str, adapter: Any, mt5_client: Any | None) -> dict[str, Any]:
    """Part 12/19: read-only diagnostic for ONE symbol -- computes a representative test move
    (a small, deterministic distance derived from the symbol's own tick size, never a live
    trading decision) and runs it through the exact same canonical calculator a real entry
    would use, then classifies the result. Makes hidden EURJPY/XAUUSD-style issues visible
    before any strategy attempts a trade on the symbol."""
    broker_symbol = getattr(symbol_info, "symbol", None) or ""
    bid = getattr(symbol_info, "bid", None)
    ask = getattr(symbol_info, "ask", None)
    if not bid or not ask:
        try:
            quote = await adapter.latest_tick(broker_symbol)
            bid, ask = quote.bid, quote.ask
        except Exception as exc:
            return {"symbol": broker_symbol, "status": RISK_METADATA_UNSUPPORTED_METHOD, "reason": f"no_live_quote:{exc.__class__.__name__}"}
    if not bid or not ask:
        return {"symbol": broker_symbol, "status": RISK_METADATA_UNSUPPORTED_METHOD, "reason": "no_live_quote"}

    tick_size = getattr(symbol_info, "trade_tick_size", None) or getattr(symbol_info, "point", None) or Decimal("0.0001")
    entry = Decimal(str(ask))
    stop = entry - (Decimal(str(tick_size)) * Decimal("50"))
    result = await calculate_canonical_loss_per_lot(direction="LONG", entry=entry, stop=stop, symbol_info=symbol_info, mt5_client=mt5_client, account_currency=account_currency, adapter=adapter)
    status = classify_risk_metadata_status(result)
    return {
        "symbol": broker_symbol,
        "calc_mode": getattr(symbol_info, "trade_calc_mode", None),
        "digits": getattr(symbol_info, "digits", None),
        "point": str(getattr(symbol_info, "point", None)) if getattr(symbol_info, "point", None) is not None else None,
        "currency_base": getattr(symbol_info, "currency_base", None),
        "currency_profit": getattr(symbol_info, "currency_profit", None),
        "currency_margin": getattr(symbol_info, "currency_margin", None),
        "account_currency": account_currency,
        "volume_min": str(getattr(symbol_info, "volume_min", None)) if getattr(symbol_info, "volume_min", None) is not None else None,
        "volume_max": str(getattr(symbol_info, "volume_max", None)) if getattr(symbol_info, "volume_max", None) is not None else None,
        "volume_step": str(getattr(symbol_info, "volume_step", None)) if getattr(symbol_info, "volume_step", None) is not None else None,
        "test_move": {"entry": str(entry), "stop": str(stop), "distance": str(entry - stop)},
        "estimates": {name: est.to_dict() for name, est in result.estimates.items()},
        "disagreement_pct": result.max_disagreement_pct,
        "trusted_method_count": result.trusted_method_count,
        "quorum_met": result.quorum_met,
        "status": status,
    }


async def audit_forex_universe(*, instruments: list[Any], account_currency: str, adapter: Any, mt5_client: Any | None) -> list[dict[str, Any]]:
    """Part 12: batch version of audit_symbol_risk_metadata over every provided instrument
    (typically the current MT5ForexInstrument list from adapter.forex_universe()). A failure
    auditing one symbol never aborts the batch -- it is recorded as UNSUPPORTED_METHOD with the
    exception class as the reason."""
    results: list[dict[str, Any]] = []
    for instrument in instruments:
        symbol_info = getattr(instrument, "symbol", None)
        broker_symbol = getattr(instrument, "broker_symbol", None) or getattr(symbol_info, "symbol", "unknown")
        if symbol_info is None:
            results.append({"symbol": broker_symbol, "status": RISK_METADATA_UNSUPPORTED_METHOD, "reason": "no_symbol_metadata"})
            continue
        try:
            results.append(await audit_symbol_risk_metadata(symbol_info=symbol_info, account_currency=account_currency, adapter=adapter, mt5_client=mt5_client))
        except Exception as exc:
            results.append({"symbol": broker_symbol, "status": RISK_METADATA_UNSUPPORTED_METHOD, "reason": f"audit_error:{exc.__class__.__name__}"})
    return results


def record_mismatch_if_needed(result: CanonicalRiskResult, *, symbol: str, account_fingerprint: str | None, context: str) -> None:
    """Persists the audit record whenever a calculation crossed the warning threshold OR failed
    quorum -- symbol, selected method, every estimate (with unit/conversion detail), disagreement
    %, quorum state, account fingerprint, timestamp. No-op (no DB round trip) when there was
    nothing to report. `context` identifies the call site so a symbol that only ever mismatches
    in one code path is distinguishable from one that mismatches everywhere."""
    if not result.warning_codes:
        return
    with SessionLocal() as db:
        row = MT5RiskMetadataMismatchORM(
            mismatch_id="MTMM_" + hashlib.sha256(json.dumps({"symbol": symbol, "context": context, "time": datetime.now(timezone.utc).isoformat()}, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:40]
        )
        row.symbol = symbol.upper()
        row.account_fingerprint = account_fingerprint
        row.selected_method = result.selected_method
        row.selected_loss_per_lot = float(result.selected_loss_per_lot) if result.selected_loss_per_lot is not None else None
        row.estimates = {name: est.to_dict() for name, est in result.estimates.items()}
        row.disagreement_pct = result.max_disagreement_pct
        row.critical = CRITICAL_MISMATCH in result.warning_codes
        row.blocked_entry = result.blocked
        row.context = context
        db.merge(row)
        db.commit()
        logger.warning(
            "MT5 risk-metadata mismatch symbol=%s context=%s selected_method=%s selected_loss=%s disagreement_pct=%s blocked=%s block_reason=%s trusted_methods=%d estimates=%s",
            symbol, context, result.selected_method, row.selected_loss_per_lot, result.max_disagreement_pct, result.blocked, result.block_reason, result.trusted_method_count,
            json.dumps({name: est.to_dict() for name, est in result.estimates.items()}, default=str),
        )
