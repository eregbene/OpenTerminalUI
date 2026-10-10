"""trend_pullback -- rebuilt from the archived OpenTerminalUI strategy (backend/mt5_strategies/families/trend_pullback.py,
archive/full-system-2026-10-09), its default mode (EMA_ZONE, every optional flag off), on bensim's clean data:

  1. local trend   M15 EMA20 above EMA50 -> only buys; below -> only sells
  2. pullback      the M15 close is back inside the EMA20/EMA50 band widened by 1 x ATR(14)
  3. HTF filter    the H1 swing trend is not against the trade (archive: htf_trend_h1 != opposite)
  4. entry         at market after that M15 close (here: on the first M5 close of the next M15 candle, which the
                   backtester and a live engine both see); stop 1.2 x ATR, target 2.5 x ATR (archive geometry)
Support (new, optional): the same SMC/ICT checks as the fib strategy (bensim.indicators.confirm) -- required via
`confirm`, or "at least N of M" via `confirm_any`/`confirm_min`. Same plan() contract as bensim.strategy.

Result 2026-07-09..10-10 (7 symbols, IC Markets costs): archive rules 955 trades -0.17R/trade (t -3.8, PF 0.77);
with 2 of (killzone, POI, sweep) 264 trades -0.06R (PF 0.92). Negative -> research only, not traded. (h1_mode="agree"
changes nothing: the H1 swing trend is never neutral once warmed up.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import indicators
from .strategy import atr, order_for, pip, trend  # noqa: F401  (order_for re-exported for the backtester)

Bars = dict[str, np.ndarray]


@dataclass(frozen=True)
class PullbackParams:
    fast: int = 20
    slow: int = 50
    zone_atr: float = 1.0
    stop_atr: float = 1.2
    target_atr: float = 2.5
    h1_mode: str = "not_against"  # "not_against" (archive) | "agree" (support: H1 trend must point the trade's way)
    confirm: tuple = ()
    confirm_any: tuple = ()
    confirm_min: int = 0
    confirm_lookback: int = 20
    min_stop_costs: float = 8.0
    max_rr: float = 20.0
    max_hold_bars: int = 200
    entry_sec: int = 900
    bias_sec: int = 3600
    cost_pips: dict[str, float] = field(default_factory=lambda: {"EURUSD": 0.9, "GBPUSD": 1.2, "USDJPY": 1.0, "AUDUSD": 1.0,
                                                                  "USDCAD": 1.1, "USDCHF": 1.1, "XAUUSD": 3.0})


def ema(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy()


def plan(m: Bars, h1: Bars, m5: Bars, symbol: str, p: PullbackParams = PullbackParams(), waiting: list | None = None) -> dict[str, Any] | None:
    """TRIGGERED only on the first M5 close after an M15 candle that met the pullback conditions."""
    if len(m["t"]) < p.slow + 70 or not len(h1["t"]) or not len(m5["t"]):
        return None
    cur15 = (int(m5["t"][-1]) // 900) * 900
    if int(np.sum(m5["t"] >= cur15)) != 1:  # not the first M5 close of this M15 candle
        return None
    i = int(np.searchsorted(m["t"], cur15)) - 1  # the M15 candle that just closed
    if i < p.slow + 50:
        return None
    c = m["close"][: i + 1]
    e1, e2 = ema(c, p.fast)[-1], ema(c, p.slow)[-1]
    a = atr({k: v[: i + 1] for k, v in m.items()})[-1]
    d = 1 if e1 > e2 else -1
    if not (min(e1, e2) - p.zone_atr * a <= c[-1] <= max(e1, e2) + p.zone_atr * a):
        return None
    k1 = int(np.searchsorted(h1["t"] + 3600, m["t"][i] + 900, side="right")) - 1
    h1t = int(trend(h1)[k1]) if k1 >= 0 else 0
    if h1t == -d or (p.h1_mode == "agree" and h1t != d):
        return None
    entry = float(m5["close"][-1])
    stop, target = entry - d * p.stop_atr * a, entry + d * p.target_atr * a
    if p.stop_atr * a < p.min_stop_costs * p.cost_pips.get(symbol, 1.2) * pip(symbol):
        return None
    checks: dict[str, Any] = {}
    if p.confirm or p.confirm_min:
        ind = indicators.compute({k: v[: i + 1] for k, v in m.items()})
        checks = indicators.confirm(ind, m, i, d, entry, float(stop), p.confirm_lookback)
        if not indicators.passes(checks, p.confirm):
            return None
        if p.confirm_min and sum(indicators.passes(checks, (n,)) for n in p.confirm_any) < p.confirm_min:
            return None
    return {"dir": d, "status": "TRIGGERED", "entry": entry, "stop": float(stop), "target": float(target),
            "signal_time": int(m["t"][i]), "zone_base_time": int(m["t"][i]), "atr": float(a), "checks": checks}


def _selfcheck() -> None:
    n = 400
    t15 = 1_780_000_000 - (1_780_000_000 % 900) + np.arange(n) * 900
    up = 1.1 + np.linspace(0, 0.02, n) + 0.0004 * np.sin(np.arange(n) / 3)
    up[-1] = up[-25]  # the last M15 close pulls back into the EMA band
    m = {"t": t15, "open": up, "high": up + 0.001, "low": up - 0.001, "close": up}
    key = t15 // 3600
    s = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    h1 = {"t": key[s] * 3600, "open": up[s], "high": np.maximum.reduceat(up + 0.001, s), "low": np.minimum.reduceat(up - 0.001, s),
          "close": up[np.r_[s[1:], len(key)] - 1]}
    m5 = {"t": np.array([t15[-1] + 900]), "open": up[-1:], "high": up[-1:] + 1e-4, "low": up[-1:] - 1e-4, "close": up[-1:]}
    pl = plan({k: np.r_[v, v[-1:] + (900 if k == "t" else 0)] for k, v in m.items()}, h1, m5, "EURUSD")
    assert pl is not None and pl["dir"] == 1 and pl["stop"] < pl["entry"] < pl["target"], pl
    late = {k: np.r_[v, v + (300 if k == "t" else 0)] for k, v in m5.items()}  # second M5 of the candle: no new trigger
    assert plan({k: np.r_[v, v[-1:] + (900 if k == "t" else 0)] for k, v in m.items()}, h1, late, "EURUSD") is None
    print("pullback selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
