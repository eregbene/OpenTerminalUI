"""Execution-cost provenance for historical outcome labeling (QuantConnect/LEAN gap-analysis
roadmap Phase 1, item 1). Answers, honestly, "where did this historical trade's cost assumption
come from" -- never silently substitutes fake precision for a cost that genuinely cannot be
determined.

Four-tier provenance model for SPREAD cost (used by outcomes.py::label_outcome):
  OBSERVED           -- a real bid/ask spread captured at or near THIS setup's own entry_time
                         (existing `real_spread` mechanism: a live decision-snapshot quote).
  HISTORICAL_ESTIMATE -- no real spread for this exact setup, but the corpus has genuine OBSERVED
                         spread samples for the SAME symbol at entry_times <= this setup's own
                         entry_time. HistoricalSpreadIndex below answers this with a
                         point-in-time-safe median of the most recent such observations --
                         deliberately never using anything observed AFTER this setup's entry_time
                         (that would be look-ahead: using knowledge of typical cost levels that
                         did not yet exist at the time this trade would have been placed).
  CONFIG_FALLBACK     -- an explicitly operator-configured typical spread for this symbol
                         (MT5_HISTORICAL_SPREAD_FALLBACK_<SYMBOL>, price units). Off by default --
                         only used if the operator deliberately sets it. Never fabricated by this
                         module.
  UNKNOWN             -- none of the above. net_outcome_r stays None; gross outcome_r is always
                         preserved regardless.

COMMISSION uses a narrower, two-outcome model (see resolve_commission_cost_r): this account's real
broker-reported commission is documented (brokers/mt5/trading_costs.py) as genuinely $0 -- cost is
embedded in spread instead. Converting a nonzero commission rate into R-units for a purely
hypothetical historical fingerprint would require an assumed lot size this module has no honest
basis for (same reasoning outcomes.py's own docstring already gave for not estimating commission)
-- so commission is only ever CONFIG_ZERO/OBSERVED_ZERO (a real, verified zero -- no lot-size
assumption needed since $0 x any volume is still $0) or UNKNOWN_REQUIRES_LOT_SIZE (explicitly
excluded from net_outcome_r, never guessed).

No look-ahead: HistoricalSpreadIndex is built once from persisted real_spread_price values and
answers point-in-time queries via bisect against entry_time -- it can only see observations
strictly at or before the query time, by construction.
"""
from __future__ import annotations

import bisect
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.brokers.mt5 import trading_costs
from backend.brokers.mt5.orm import MT5TradeRecordORM
from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
from backend.shared.db import SessionLocal

OBSERVED = "OBSERVED"
HISTORICAL_ESTIMATE = "HISTORICAL_ESTIMATE"
CONFIG_FALLBACK = "CONFIG_FALLBACK"
UNKNOWN = "UNKNOWN"

COMMISSION_CONFIG_ZERO = "CONFIG_ZERO"
COMMISSION_OBSERVED_ZERO = "OBSERVED_ZERO"
COMMISSION_UNKNOWN = "UNKNOWN_REQUIRES_LOT_SIZE"

_SPREAD_PROVENANCE_ORDER = {OBSERVED: 0, HISTORICAL_ESTIMATE: 1, CONFIG_FALLBACK: 2, UNKNOWN: 3}

# How many most-recent, point-in-time-eligible real observations to median over for a
# HISTORICAL_ESTIMATE -- small enough to stay locally representative (spreads drift over years),
# large enough to smooth out any single noisy snapshot.
_ESTIMATE_WINDOW = 20

# Commission-zero verification: a small, real, recent sample of actual closed-deal commission
# values for this account/broker. Not a fabricated assumption -- an empirical check of what the
# broker has actually reported.
_COMMISSION_SAMPLE_SIZE = 200
_COMMISSION_ZERO_EPSILON = 1e-9


def worse_provenance(a: str, b: str) -> str:
    return a if _SPREAD_PROVENANCE_ORDER.get(a, 3) >= _SPREAD_PROVENANCE_ORDER.get(b, 3) else b


def _ensure_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class SpreadCostResult:
    real_spread_price: float | None  # persisted ONLY when provenance == OBSERVED
    spread_cost_r: float | None
    provenance: str


def resolve_spread_cost(*, canonical_symbol: str, entry_time: datetime, risk: float, real_spread: float | None, db: Any = None) -> SpreadCostResult:
    """`risk` must be > 0 (caller already validates this before calling label_outcome's cost
    logic). `real_spread` is the existing OBSERVED-tier input (price units, from a live decision
    snapshot) -- unchanged meaning from before this module existed."""
    if real_spread is not None and real_spread > 0:
        return SpreadCostResult(real_spread_price=float(real_spread), spread_cost_r=round(float(real_spread) / risk, 4), provenance=OBSERVED)

    estimate = _historical_spread_index().estimate_as_of(canonical_symbol, entry_time, db=db)
    if estimate is not None:
        return SpreadCostResult(real_spread_price=None, spread_cost_r=round(estimate / risk, 4), provenance=HISTORICAL_ESTIMATE)

    fallback = _config_fallback_spread(canonical_symbol)
    if fallback is not None:
        return SpreadCostResult(real_spread_price=None, spread_cost_r=round(fallback / risk, 4), provenance=CONFIG_FALLBACK)

    return SpreadCostResult(real_spread_price=None, spread_cost_r=None, provenance=UNKNOWN)


def _config_fallback_spread(canonical_symbol: str) -> float | None:
    """MT5_HISTORICAL_SPREAD_FALLBACK_<SYMBOL> (price units, e.g. 0.00015 for EURUSD) -- unset by
    default. Deliberately per-symbol (not a single global constant): a fallback spread for
    EURUSD and one for XAUUSD are not remotely comparable magnitudes, and a single shared
    constant would itself be a fabricated number."""
    symbol_key = "".join(ch for ch in canonical_symbol.upper() if ch.isalnum())
    raw = os.getenv(f"MT5_HISTORICAL_SPREAD_FALLBACK_{symbol_key}")
    if not raw:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


@dataclass(frozen=True)
class CommissionCostResult:
    commission_cost_r: float | None
    provenance: str


_commission_cache: CommissionCostResult | None = None


def resolve_commission_cost_r(*, db: Any = None) -> CommissionCostResult:
    """Cached at module level (commission mode/broker behavior does not change trade-to-trade,
    and this is called once per label_outcome() invocation -- a fresh DB query per call would be
    a meaningful cost at bulk-replay scale). Call invalidate_commission_cache() to force a
    re-check (tests, or after an operator changes MT5_COMMISSION_MODE)."""
    global _commission_cache
    if _commission_cache is not None:
        return _commission_cache

    mode = trading_costs.commission_mode()
    if mode == trading_costs.NONE_MODE:
        _commission_cache = CommissionCostResult(commission_cost_r=0.0, provenance=COMMISSION_CONFIG_ZERO)
        return _commission_cache

    if mode == trading_costs.BROKER_REPORTED:
        observed_zero = _empirically_zero_commission(db=db)
        if observed_zero is True:
            _commission_cache = CommissionCostResult(commission_cost_r=0.0, provenance=COMMISSION_OBSERVED_ZERO)
            return _commission_cache

    # CONFIG_FALLBACK mode, or BROKER_REPORTED with real nonzero/unknown commission: converting a
    # per-lot dollar rate into R-units for a hypothetical historical fingerprint needs an assumed
    # lot size this module has no honest basis for (same reasoning as outcomes.py's original
    # docstring) -- excluded from net_outcome_r, not guessed.
    _commission_cache = CommissionCostResult(commission_cost_r=None, provenance=COMMISSION_UNKNOWN)
    return _commission_cache


def invalidate_commission_cache() -> None:
    global _commission_cache
    _commission_cache = None


def _empirically_zero_commission(*, db: Any = None) -> bool | None:
    """Reads a real, recent, bounded sample of actual closed-deal commission values for this
    account. Returns True only if EVERY sampled deal reported (effectively) zero commission;
    False if any real nonzero value was found; None if there is no sample to check yet (too few
    deals) -- treated the same as False by the caller (falls through to UNKNOWN rather than
    assuming zero with no evidence)."""
    def _query(session: Any) -> bool | None:
        rows = (
            session.query(MT5TradeRecordORM.commission)
            .filter(MT5TradeRecordORM.commission.isnot(None), MT5TradeRecordORM.close_timestamp.isnot(None))
            .order_by(MT5TradeRecordORM.close_timestamp.desc())
            .limit(_COMMISSION_SAMPLE_SIZE)
            .all()
        )
        if len(rows) < 20:
            return None
        return all(abs(float(r[0] or 0.0)) < _COMMISSION_ZERO_EPSILON for r in rows)

    if db is not None:
        return _query(db)
    with SessionLocal() as session:
        return _query(session)


class HistoricalSpreadIndex:
    """In-memory, per-symbol, point-in-time index of genuinely OBSERVED historical spreads
    (HistoricalSetupOutcomeORM.real_spread_price, joined to its fingerprint's entry_time/symbol).
    Built lazily once, then answered via bisect -- O(log n) per lookup after the initial O(n log
    n) build, so bulk-replay-scale label_outcome() calls do not each issue a fresh DB round-trip.
    """

    def __init__(self) -> None:
        self._by_symbol: dict[str, tuple[list[datetime], list[float]]] | None = None

    def invalidate(self) -> None:
        self._by_symbol = None

    def _ensure_built(self, db: Any = None) -> None:
        if self._by_symbol is not None:
            return

        def _query(session: Any) -> list[tuple[str, datetime, float]]:
            rows = (
                session.query(
                    HistoricalPatternFingerprintORM.canonical_symbol,
                    HistoricalPatternFingerprintORM.entry_time,
                    HistoricalSetupOutcomeORM.real_spread_price,
                )
                .join(HistoricalSetupOutcomeORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id)
                .filter(HistoricalSetupOutcomeORM.real_spread_price.isnot(None))
                .all()
            )
            return [(symbol, _ensure_utc(entry_time), float(spread)) for symbol, entry_time, spread in rows]

        raw = _query(db) if db is not None else self._query_own_session(_query)

        grouped: dict[str, list[tuple[datetime, float]]] = {}
        for symbol, entry_time, spread in raw:
            grouped.setdefault(symbol.upper(), []).append((entry_time, spread))

        built: dict[str, tuple[list[datetime], list[float]]] = {}
        for symbol, pairs in grouped.items():
            pairs.sort(key=lambda p: p[0])
            built[symbol] = ([p[0] for p in pairs], [p[1] for p in pairs])
        self._by_symbol = built

    @staticmethod
    def _query_own_session(fn: Any) -> Any:
        with SessionLocal() as session:
            return fn(session)

    def estimate_as_of(self, canonical_symbol: str, entry_time: datetime, *, db: Any = None) -> float | None:
        self._ensure_built(db=db)
        assert self._by_symbol is not None
        series = self._by_symbol.get(canonical_symbol.upper())
        if not series:
            return None
        times, spreads = series
        entry_time = _ensure_utc(entry_time)
        # Rightmost insertion point among times <= entry_time -- never includes an observation
        # from strictly after this setup's own entry_time (no look-ahead).
        cutoff = bisect.bisect_right(times, entry_time)
        if cutoff == 0:
            return None
        window = spreads[max(0, cutoff - _ESTIMATE_WINDOW):cutoff]
        window_sorted = sorted(window)
        mid = len(window_sorted) // 2
        if len(window_sorted) % 2 == 1:
            return window_sorted[mid]
        return (window_sorted[mid - 1] + window_sorted[mid]) / 2.0


_spread_index: HistoricalSpreadIndex | None = None


def _historical_spread_index() -> HistoricalSpreadIndex:
    global _spread_index
    if _spread_index is None:
        _spread_index = HistoricalSpreadIndex()
    return _spread_index


def invalidate_spread_index() -> None:
    global _spread_index
    _spread_index = None
