"""Phase 12 (Forex/MT5 roadmap): DEMO vs tiny-live ("LIVE_VALIDATION") readiness checks.

Explicit, hard boundary: this module NEVER enables live trading. It computes, read-only, whether
a hypothetical small live account (default $100, matching the roadmap's own figure) COULD trade
each currently-eligible forex symbol at the account's real configured risk_percent_per_trade
without the broker's minimum lot size alone exceeding the risk budget. Nothing here flips
MT5_LIVE_TRADING_ENABLED, creates an account profile, or submits an order -- it is a pure
analytics report a human reviews before ever considering a real LIVE_VALIDATION account.

LIVE_VALIDATION account-profile concept: represented here purely as a parameter set
(hypothetical_balance, risk_percent_per_trade) passed into the report -- NOT a new
MT5AccountProfile row, NOT a new account_id the rest of the system would recognize. Turning this
into a real, tradeable account profile is a distinct, separate, human decision explicitly out of
scope for this module.

Reuses risk_calculator.calculate_canonical_loss_per_lot -- the SAME conservative,
multi-method-cross-validated loss-per-lot calculator already used for real order sizing (see
execution.py's order_calc_margin/order_calc_stop_loss checks) -- rather than a separate, simpler
estimate that could silently disagree with what a real order would actually cost.

Stop distance: real, current ATR(14) computed from the account's own just-fetched M15 candles (a
standard, defensible choice consistent with how this codebase's strategies already size stops
elsewhere) -- never a hardcoded pip guess.

Never rounds volume upward past the risk budget: if the broker's OWN minimum tradable lot size
already risks more than the account can afford at this risk_percent_per_trade, the verdict is
SKIP_MIN_LOT_EXCEEDS_RISK -- this module has no mechanism to submit a smaller-than-minimum lot
(brokers don't allow that), so skipping is the only safe outcome, never suggesting a larger lot
that would silently raise the account's real risk beyond risk_percent_per_trade.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

DEFAULT_HYPOTHETICAL_BALANCE = 100.0

READY = "READY"
SKIP_MIN_LOT_EXCEEDS_RISK = "SKIP_MIN_LOT_EXCEEDS_RISK"
SKIP_RISK_METADATA_UNTRUSTED = "SKIP_RISK_METADATA_UNTRUSTED"
SKIP_INSUFFICIENT_DATA = "SKIP_INSUFFICIENT_DATA"


def _atr14(m15_rows: list[Any]) -> Decimal | None:
    """Plain Wilder-style True Range average over the most recent 14 completed M15 bars --
    intentionally simple (no external TA dependency) since this is a readiness estimate, not a
    live trading signal."""
    completed = [row for row in m15_rows if getattr(row, "complete", True)]
    if len(completed) < 15:
        return None
    window = completed[-15:]
    true_ranges: list[Decimal] = []
    for i in range(1, len(window)):
        high, low, prev_close = Decimal(str(window[i].high)), Decimal(str(window[i].low)), Decimal(str(window[i - 1].close))
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
    if not true_ranges:
        return None
    return sum(true_ranges) / Decimal(len(true_ranges))


@dataclass
class SymbolReadiness:
    broker_symbol: str
    canonical_pair: str
    volume_min: float | None
    stop_distance: float | None
    risk_budget: float
    loss_at_min_lot: float | None
    selected_method: str | None
    verdict: str
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "broker_symbol": self.broker_symbol,
            "canonical_pair": self.canonical_pair,
            "volume_min": self.volume_min,
            "stop_distance": self.stop_distance,
            "risk_budget": self.risk_budget,
            "loss_at_min_lot": self.loss_at_min_lot,
            "selected_method": self.selected_method,
            "verdict": self.verdict,
            "detail": self.detail,
        }


async def symbol_readiness(*, instrument: Any, adapter: Any, mt5_client: Any, account_currency: str, hypothetical_balance: float, risk_percent_per_trade: float) -> SymbolReadiness:
    from backend.brokers.mt5.risk_calculator import calculate_canonical_loss_per_lot

    risk_budget = hypothetical_balance * risk_percent_per_trade / 100.0
    symbol_info = instrument.symbol
    volume_min = float(symbol_info.volume_min) if getattr(symbol_info, "volume_min", None) is not None else None

    try:
        m15 = await adapter.candles(instrument.broker_symbol, "M15", count=30, completed_only=True)
        quote = await adapter.latest_tick(instrument.broker_symbol)
    except Exception as exc:
        return SymbolReadiness(broker_symbol=instrument.broker_symbol, canonical_pair=instrument.canonical_pair, volume_min=volume_min, stop_distance=None, risk_budget=risk_budget, loss_at_min_lot=None, selected_method=None, verdict=SKIP_INSUFFICIENT_DATA, detail=f"market data fetch failed: {exc.__class__.__name__}")

    atr = _atr14(m15)
    if atr is None or atr <= 0 or quote is None or quote.bid is None:
        return SymbolReadiness(broker_symbol=instrument.broker_symbol, canonical_pair=instrument.canonical_pair, volume_min=volume_min, stop_distance=None, risk_budget=risk_budget, loss_at_min_lot=None, selected_method=None, verdict=SKIP_INSUFFICIENT_DATA, detail="insufficient M15 history for ATR(14) or no live quote")

    entry = Decimal(str(quote.bid))
    stop = entry - atr  # direction-agnostic for loss-per-lot purposes (|entry - stop| only)
    result = await calculate_canonical_loss_per_lot(direction="LONG", entry=entry, stop=stop, symbol_info=symbol_info, mt5_client=mt5_client, account_currency=account_currency, adapter=adapter)

    if result.selected_loss_per_lot is None or not result.quorum_met or result.blocked:
        return SymbolReadiness(broker_symbol=instrument.broker_symbol, canonical_pair=instrument.canonical_pair, volume_min=volume_min, stop_distance=float(atr), risk_budget=risk_budget, loss_at_min_lot=None, selected_method=result.selected_method, verdict=SKIP_RISK_METADATA_UNTRUSTED, detail=result.block_reason or "canonical loss-per-lot quorum not met")

    if volume_min is None:
        return SymbolReadiness(broker_symbol=instrument.broker_symbol, canonical_pair=instrument.canonical_pair, volume_min=None, stop_distance=float(atr), risk_budget=risk_budget, loss_at_min_lot=None, selected_method=result.selected_method, verdict=SKIP_INSUFFICIENT_DATA, detail="broker symbol has no volume_min")

    loss_at_min_lot = float(result.selected_loss_per_lot) * volume_min
    if loss_at_min_lot > risk_budget:
        return SymbolReadiness(broker_symbol=instrument.broker_symbol, canonical_pair=instrument.canonical_pair, volume_min=volume_min, stop_distance=float(atr), risk_budget=risk_budget, loss_at_min_lot=loss_at_min_lot, selected_method=result.selected_method, verdict=SKIP_MIN_LOT_EXCEEDS_RISK, detail=f"minimum lot {volume_min} risks {loss_at_min_lot:.2f} {account_currency}, exceeding the {risk_budget:.2f} {account_currency} budget -- never rounded down or forced")

    return SymbolReadiness(broker_symbol=instrument.broker_symbol, canonical_pair=instrument.canonical_pair, volume_min=volume_min, stop_distance=float(atr), risk_budget=risk_budget, loss_at_min_lot=loss_at_min_lot, selected_method=result.selected_method, verdict=READY, detail=f"minimum lot {volume_min} risks {loss_at_min_lot:.2f} {account_currency}, within the {risk_budget:.2f} {account_currency} budget")


async def readiness_report(account_id: str = "demo_10k", *, hypothetical_balance: float = DEFAULT_HYPOTHETICAL_BALANCE) -> dict[str, Any]:
    """Real, current report across every eligible symbol in this account's forex universe. Uses
    the account's REAL adapter (read-only calls only: candles/latest_tick/order_calc_profit) and
    REAL configured risk_percent_per_trade -- never a live-money account, never an order."""
    from backend.brokers.mt5.multi_account import adapter_for_account

    adapter = adapter_for_account(account_id)
    mt5_client = adapter.client
    account = await adapter.mt5_account()
    universe = await adapter.forex_universe()
    risk_percent = float(adapter.config.risk_percent_per_trade)

    results = []
    for instrument in universe.items:
        if not instrument.eligible:
            continue
        readiness = await symbol_readiness(instrument=instrument, adapter=adapter, mt5_client=mt5_client, account_currency=account.currency or "USD", hypothetical_balance=hypothetical_balance, risk_percent_per_trade=risk_percent)
        results.append(readiness.as_dict())

    ready = [r for r in results if r["verdict"] == READY]
    return {
        "account_id": account_id,
        "note": "LIVE_VALIDATION readiness analysis only -- does not enable live trading and creates no account profile",
        "hypothetical_balance": hypothetical_balance,
        "risk_percent_per_trade": risk_percent,
        "symbols_evaluated": len(results),
        "symbols_ready": len(ready),
        "ready_symbols": [r["broker_symbol"] for r in ready],
        "results": results,
    }
