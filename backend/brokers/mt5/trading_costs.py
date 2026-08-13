"""Canonical trading-cost accounting for MT5 deals (real broker economics, not just raw
price-movement P&L).

Audit finding (see commit history / final report for this feature): before this module
existed, three DIFFERENT summation formulas computed "realized P&L" from the same underlying
deal data across the codebase --
  - adaptive_management/service.py + analytics.py: profit + commission + swap + fee (4 terms)
  - brokers/mt5/persistence.py (MT5TradeRecordORM.realized_pnl): profit + commission + swap
    (3 terms, missing fee)
  - portfolio_execution/service.py (build_snapshot): profit + commission (2 terms, missing
    swap and fee)
This module is the single source of truth going forward; all three call sites now delegate to
compute_trade_costs()/resolve_commission() here.

MT5 deal-field sign convention (verified against this account's live broker history): `profit`
is the raw gross price-movement P&L for the deal, EXCLUDING commission/swap/fee -- those are
separate, already-negative-when-a-cost fields on the SAME deal record. The canonical relation
is therefore additive, never subtractive:

    net_pnl = gross_pnl + commission + swap + fee

Do not negate or subtract commission a second time anywhere downstream -- it is already signed
correctly by the broker (or by resolve_commission()'s fallback, which negates the configured
positive per-lot rate before returning it, specifically so every caller can use the same `+`
formula regardless of where the commission value came from).

Live audit of this account (2026-08-12, 14-day window, 381 deals / 180 closed positions):
commission and fee are reported as exactly 0.0 on every single deal (this broker charges
nothing explicit -- cost is embedded in spread instead, which already shows up inside
`profit`); swap is usually 0.0 but was nonzero (both signs) on 4 deals. This is REAL broker
data, not a gap -- BROKER_REPORTED mode correctly reports $0 commission for this account,
because that is the truth. The CONFIG_FALLBACK mode exists for brokers/environments that
genuinely omit the commission field rather than reporting a real zero.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

BROKER_REPORTED = "BROKER_REPORTED"
CONFIG_FALLBACK = "CONFIG_FALLBACK"
NONE_MODE = "NONE"
_VALID_MODES = {BROKER_REPORTED, CONFIG_FALLBACK, NONE_MODE}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def commission_mode() -> str:
    """MT5_COMMISSION_MODE: BROKER_REPORTED (preferred/default) | CONFIG_FALLBACK | NONE.
    Read live (not cached at import time) so tests can monkeypatch os.environ per-case."""
    raw = str(os.getenv("MT5_COMMISSION_MODE", BROKER_REPORTED) or BROKER_REPORTED).strip().upper()
    return raw if raw in _VALID_MODES else BROKER_REPORTED


def commission_per_lot_round_turn() -> float:
    """MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN -- a positive dollar rate per 1.00 lot for a
    full round-turn (entry+exit). Only consulted in CONFIG_FALLBACK mode, or as an automatic
    fallback in BROKER_REPORTED mode when the broker genuinely did not report a commission
    value (None) for a given deal -- never when the broker reported a real value, including a
    real $0.00."""
    return _env_float("MT5_COMMISSION_PER_STANDARD_LOT_ROUND_TURN", 7.0)


def resolve_commission(*, broker_commission: float | None, volume: float | None) -> tuple[float, str]:
    """Returns (signed_commission_dollars, source) for ONE deal.

    - broker_commission is None means the broker genuinely did not supply a value (field
      absent from the payload) -- distinct from a real, broker-reported 0.0, which is trusted
      as-is. This distinction only survives through backend.brokers.mt5.models.MT5HistoryItem
      (raw MT5 API deal dicts, via history_deals_get, always populate the field with at least
      0.0 -- there is no "missing" case from that path today; the None case exists for
      manually-constructed/imported payloads and for future/other brokers that may omit it).
    - Fallback is always negative (a cost), converting the positive configured rate, so the
      canonical `net = gross + commission + swap + fee` formula never needs special-casing by
      source.
    """
    mode = commission_mode()
    vol = float(volume or 0.0)
    if mode == NONE_MODE:
        return 0.0, NONE_MODE
    if mode == BROKER_REPORTED:
        if broker_commission is not None:
            return float(broker_commission), BROKER_REPORTED
        return round(-abs(commission_per_lot_round_turn()) * vol, 8), CONFIG_FALLBACK
    # CONFIG_FALLBACK mode: always use the configured rate, regardless of what the broker sent.
    return round(-abs(commission_per_lot_round_turn()) * vol, 8), CONFIG_FALLBACK


@dataclass(frozen=True)
class TradeCostBreakdown:
    gross_pnl: float
    commission: float
    swap: float
    other_fees: float
    net_pnl: float
    total_volume: float
    commission_source: str  # BROKER_REPORTED | CONFIG_FALLBACK | NONE | MIXED (see compute_trade_costs)
    commission_per_lot_effective: float | None
    deal_count: int = 0

    @property
    def total_trading_cost(self) -> float:
        """Positive = costs reduced net P&L vs gross; negative = swap/fee credits increased it.
        Equivalent to gross_pnl - net_pnl."""
        return round(self.gross_pnl - self.net_pnl, 8)


_EMPTY_BREAKDOWN = TradeCostBreakdown(
    gross_pnl=0.0, commission=0.0, swap=0.0, other_fees=0.0, net_pnl=0.0,
    total_volume=0.0, commission_source=NONE_MODE, commission_per_lot_effective=None, deal_count=0,
)


def _get(deal: Any, key: str) -> Any:
    if isinstance(deal, Mapping):
        return deal.get(key)
    return getattr(deal, key, None)


def compute_trade_costs(deals: Iterable[Any]) -> TradeCostBreakdown:
    """Aggregates gross/commission/swap/fee/net across every deal belonging to ONE position
    (handles partial closes -- each deal contributes its own volume-proportional commission,
    whether broker-reported or fallback-derived, so partial fills aggregate correctly without
    any special-casing here).

    Each item in `deals` must expose (via mapping keys or attributes) `profit`, `commission`,
    `swap`, `fee`, `volume` -- matching AdaptiveTradeEventORM rows, MT5HistoryItem-derived
    dicts, and MT5Position-derived dicts alike."""
    rows = list(deals)
    if not rows:
        return _EMPTY_BREAKDOWN

    gross = 0.0
    commission = 0.0
    swap = 0.0
    fees = 0.0
    volume = 0.0
    sources: set[str] = set()

    for deal in rows:
        gross += float(_get(deal, "profit") or 0.0)
        raw_commission = _get(deal, "commission")
        deal_volume = float(_get(deal, "volume") or 0.0)
        deal_commission, source = resolve_commission(broker_commission=raw_commission, volume=deal_volume)
        commission += deal_commission
        sources.add(source)
        swap += float(_get(deal, "swap") or 0.0)
        fees += float(_get(deal, "fee") or 0.0)
        volume += deal_volume

    net = gross + commission + swap + fees
    commission_source = sources.pop() if len(sources) == 1 else "MIXED"
    commission_per_lot = round(commission / volume, 6) if volume > 0 else None

    return TradeCostBreakdown(
        gross_pnl=round(gross, 8),
        commission=round(commission, 8),
        swap=round(swap, 8),
        other_fees=round(fees, 8),
        net_pnl=round(net, 8),
        total_volume=round(volume, 8),
        commission_source=commission_source,
        commission_per_lot_effective=commission_per_lot,
        deal_count=len(rows),
    )
