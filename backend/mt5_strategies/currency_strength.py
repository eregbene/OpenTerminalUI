"""Point-in-time-safe cross-sectional FX currency strength/momentum.

Methodology check (explicit, per instruction -- no proprietary code copied, no third-party
source referenced): this is the standard, published cross-sectional currency momentum
construction -- see Menkhoff, Sarno, Schmeling & Schrimpf (2012), "Currency Momentum
Strategies," Journal of Financial Economics -- construct each currency's own return from an
equal-weighted basket of the pairs it appears in (correctly signed for base/quote orientation),
rank currencies by that return, go long the strongest / short the weakest. This module is an
independent implementation of that well-established idea, adapted to Bensim's own M15/H1/H4 bar
data and ATR-normalization convention (not the daily-close convention the academic literature
uses, since that data isn't available here) -- a deliberate, disclosed adaptation, not a claim of
reproducing the original papers' exact numbers.

Ranks exactly the 7 currencies named in the design brief: USD, EUR, GBP, JPY, CHF, AUD, NZD.
CAD is deliberately excluded from the ranked set (not requested) -- USDCAD's own return still
contributes to USD's strength (real information about USD momentum), it just never produces a
CAD rank or a USDCAD trade candidate. XAUUSD is excluded entirely (gold is not a currency).

Pure functions only -- no broker I/O, no StrategyContext coupling, no imports from
mt5_strategies.context (this deliberately stays a standalone, independently-testable module,
called by the live cycle orchestrator once per cycle with already-fetched bars, and directly by
the validation harness with point-in-time-safe replayed bars).
"""
from __future__ import annotations

from typing import Any

RANKED_CURRENCIES: tuple[str, ...] = ("USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD")

# (symbol, base_currency, quote_currency) -- base appreciates when the pair rises, quote
# depreciates. XAUUSD deliberately absent (not a currency pair).
_PAIR_CURRENCIES: dict[str, tuple[str, str]] = {
    "EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"), "USDJPY": ("USD", "JPY"),
    "AUDUSD": ("AUD", "USD"), "NZDUSD": ("NZD", "USD"), "USDCAD": ("USD", "CAD"),
    "USDCHF": ("USD", "CHF"), "EURJPY": ("EUR", "JPY"), "GBPJPY": ("GBP", "JPY"),
}

HORIZONS: dict[str, tuple[str, int]] = {
    # name: (timeframe, lookback_bars) -- standard, round-number choices, not parameter-mined.
    "SHORT": ("M15", 20),    # ~5 hours
    "MEDIUM": ("H1", 24),    # ~1 day
    "LONG": ("H4", 30),      # ~5 trading days
}


def _atr_normalized_return(bars: list[dict[str, Any]], lookback: int, atr: float | None) -> float | None:
    """(close_now - close_n_bars_ago) / atr -- expresses the move in ATR units, comparable
    across pairs with very different price scales (e.g. USDJPY vs EURUSD) the same way
    breakout_distance_atr already does elsewhere in this codebase. None if insufficient
    history or ATR is unusable."""
    if len(bars) <= lookback or not atr or atr <= 0:
        return None
    close_now = float(bars[-1]["close"])
    close_then = float(bars[-1 - lookback]["close"])
    return (close_now - close_then) / atr


def _atr_from_bars(bars: list[dict[str, Any]], period: int = 14) -> float | None:
    """Simple ATR over `bars` (Wilder-style true range, simple moving average) -- a self-
    contained calc since this module deliberately has no context.py dependency. Same formula
    shape as bar_utils.average_true_range, independently computed here to keep this module's
    only inputs plain bar dicts."""
    if len(bars) < period + 1:
        return None
    trs = []
    for i in range(len(bars) - period, len(bars)):
        high, low, prev_close = float(bars[i]["high"]), float(bars[i]["low"]), float(bars[i - 1]["close"])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None


def compute_pair_momentum(bars_by_symbol: dict[str, list[dict[str, Any]]], horizon_name: str) -> dict[str, dict[str, Any]]:
    """For each available pair, the ATR-normalized return over `horizon_name`'s lookback, plus
    a momentum_persistence check (does the first half of the window agree in sign with the
    second half -- a simple, standard robustness proxy for "real trend" vs "single spike").
    Returns {symbol: {"return_atr": float, "persistence": bool}}, only for symbols present in
    `bars_by_symbol` with sufficient history."""
    timeframe_key, lookback = HORIZONS[horizon_name]
    out: dict[str, dict[str, Any]] = {}
    for symbol, bars in bars_by_symbol.items():
        if symbol not in _PAIR_CURRENCIES or len(bars) <= lookback:
            continue
        atr = _atr_from_bars(bars)
        ret = _atr_normalized_return(bars, lookback, atr)
        if ret is None:
            continue
        half = lookback // 2
        if half < 2 or len(bars) <= half:
            persistence = None
        else:
            first_half_ret = _atr_normalized_return(bars[:-half] if half < len(bars) else bars, half, atr)
            second_half_ret = _atr_normalized_return(bars, half, atr)
            persistence = (first_half_ret is not None and second_half_ret is not None
                           and first_half_ret * second_half_ret > 0 and abs(second_half_ret) > 0)
        out[symbol] = {"return_atr": ret, "persistence": persistence}
    return out


def compute_currency_strength(bars_by_symbol: dict[str, list[dict[str, Any]]], horizon_name: str) -> dict[str, Any]:
    """The core cross-sectional construction: each currency's strength = the simple average of
    (correctly signed) ATR-normalized returns across every available pair it appears in.
    Returns {"strength": {currency: float}, "rank": {currency: int (1=strongest)},
    "pair_momentum": {...}} -- rank/strength cover exactly RANKED_CURRENCIES; CAD contributes
    to USD's strength via USDCAD but is never itself ranked (see module docstring)."""
    pair_momentum = compute_pair_momentum(bars_by_symbol, horizon_name)
    contributions: dict[str, list[float]] = {c: [] for c in RANKED_CURRENCIES + ("CAD",)}
    for symbol, data in pair_momentum.items():
        base, quote = _PAIR_CURRENCIES[symbol]
        ret = data["return_atr"]
        if base in contributions:
            contributions[base].append(ret)
        if quote in contributions:
            contributions[quote].append(-ret)

    strength = {c: (sum(vals) / len(vals) if vals else None) for c, vals in contributions.items() if c in RANKED_CURRENCIES}
    ranked = sorted((c for c in RANKED_CURRENCIES if strength.get(c) is not None), key=lambda c: strength[c], reverse=True)
    rank = {c: i + 1 for i, c in enumerate(ranked)}  # 1 = strongest
    return {"strength": strength, "rank": rank, "pair_momentum": pair_momentum, "horizon": horizon_name}
