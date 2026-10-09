"""donchian_imbalance -- "combination B" from bensim_research (2026-10-09), the only live strategy.

Research: bensim_research/donchian.py::imb_entry_trades + docs/donchian-imbalance-combo-check.md.
Clean ForexSB/Dukascopy 2024-02 -> 2026-10: 1,205 trades, +0.165R/trade (t 2.2), positive every year;
2018-09 -> 2024-02: -0.010R/trade. A forward DEMO test, not a proven edge.

Rules (all on COMPLETED bars, no look-ahead):
  1. Donchian signal on M15 bar i: close beyond the 20-bar channel of the bars before it by >= 0.3 ATR(14),
     ATR(14)/SMA50(ATR) >= 1.1, and H1 swing-structure trend (k=2) agrees at the time bar i closed.
  2. Plan: the imbalance zone left by the breakout run = base candle of the most recent same-direction
     3-candle-gap chain whose first gap completed within the last 5 bars (<= i). Entry = zone near edge,
     stop = zone far edge -/+ 0.1 ATR(base bar), target = open of bar i+1 +/- 4 ATR(i).
     Skip if the stop is <= 8 round-trip spreads (research cost model) or reward:risk > 20 (all lost).
  3. The plan lives for 48 M15 bars. It triggers ONCE: on the first M5 bar whose range reaches the entry
     (never if an earlier bar already reached it). The cycle runs right after that M5 bar closes, so the
     order goes in at market (the app has no limit orders); stop/target stay at the planned prices.
No adaptive management, no partials -- fixed stop/target, exactly as tested.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from backend.mt5_strategies.context import StrategyContext
from backend.mt5_strategies.families._shared import _no_signal, _signal
from backend.mt5_strategies.models import StrategySignal

_STRATEGY_ID = "donchian_imbalance"
N, MIN_BRK, MIN_EXP, TGT_ATR, STOP_BUF, PLAN_BARS, ZONE_LOOKBACK, MIN_STOP_SPREADS, MAX_RR = 20, 0.3, 1.1, 4.0, 0.1, 48, 5, 8.0, 20.0
# round-trip spread+commission in pips, identical to the research cost model (bensim_research/backtest.py)
_COST_PIPS = {"EURUSD": 0.9, "GBPUSD": 1.2, "USDJPY": 1.0, "AUDUSD": 1.0, "USDCAD": 1.1, "USDCHF": 1.1, "XAUUSD": 3.0}


def _pip(symbol: str) -> float:
    return 0.1 if symbol == "XAUUSD" else (0.01 if symbol.endswith("JPY") else 0.0001)


def _arrays(rows: list[dict[str, Any]]) -> dict[str, np.ndarray]:
    # pd.Timestamp(...).timestamp() is unit-safe across pandas 2.x (ns) and 3.x (us) -- the container runs 3.x
    t = np.array([int(pd.Timestamp(r["time"]).timestamp()) for r in rows], dtype=np.int64)
    return {"t": t, **{k: np.array([float(r[k]) for r in rows]) for k in ("open", "high", "low", "close")}}


def _atr(b: dict[str, np.ndarray], n: int = 14) -> np.ndarray:
    prev = np.r_[b["close"][0], b["close"][:-1]]
    tr = np.maximum(b["high"] - b["low"], np.maximum(np.abs(b["high"] - prev), np.abs(b["low"] - prev)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().to_numpy()


def _trend(b: dict[str, np.ndarray], k: int = 2) -> np.ndarray:
    """Swing-structure trend after each bar's close (+1/-1/0): a close beyond the last confirmed pivot
    (pivot j confirmed at j+k) flips the trend. Same rule as bensim_research.features.structure."""
    h, l, c = b["high"], b["low"], b["close"]
    n = len(c)
    ph = {j + k: j for j in range(k, n - k) if h[j] > h[j - k:j].max() and h[j] >= h[j + 1:j + k + 1].max()}
    pl = {j + k: j for j in range(k, n - k) if l[j] < l[j - k:j].min() and l[j] <= l[j + 1:j + k + 1].min()}
    out, t, last_h, last_l, h_live, l_live = np.zeros(n, int), 0, np.nan, np.nan, False, False
    for i in range(n):
        if i in ph:
            last_h, h_live = h[ph[i]], True
        if i in pl:
            last_l, l_live = l[pl[i]], True
        if h_live and c[i] > last_h:
            t, h_live = 1, False
        elif l_live and c[i] < last_l:
            t, l_live = -1, False
        out[i] = t
    return out


def _chain_starts(b: dict[str, np.ndarray], d: int) -> np.ndarray:
    h, l = b["high"], b["low"]
    j = np.arange(2, len(h))
    gap = (h[j - 2] < l[j]) if d == 1 else (l[j - 2] > h[j])
    gj = j[gap]
    return gj[np.r_[True, np.diff(gj) > 1]] if len(gj) else gj


def _exit_bar(m: dict[str, np.ndarray], k: int, d: int, stop: float, target: float, last: int) -> int:
    """M15 bar index where a trade filled (limit) on bar k exits: stop first (same bar = stop, as in
    research), target never on the fill bar, time exit after 200 bars. Returns last+1 if still open."""
    end = min(last, k + 199)
    for j in range(k, end + 1):
        if (m["low"][j] <= stop) if d == 1 else (m["high"][j] >= stop):
            return j
        if j > k and ((m["high"][j] >= target) if d == 1 else (m["low"][j] <= target)):
            return j
    return k + 199 if k + 199 <= last else last + 1  # time exit, or still open at the newest completed bar


def plan(ctx: StrategyContext) -> dict[str, Any] | None:
    """Replays the research sequence (bensim_research.donchian.imb_entry_trades) over the completed-bar
    window: signals in time order, one trade at a time (a signal arriving while a simulated trade is open
    is skipped), first touch of a plan's zone = that plan's trade. Returns the plan whose first touch is
    the newest completed M5 bar (status TRIGGERED), else a status-only dict, else None."""
    if len(ctx.m15_rows) < N + 70 or not ctx.h1_rows or not ctx.m5_rows:
        return None
    m = _arrays(ctx.m15_rows)
    a = _atr(m)
    exp = a / pd.Series(a).rolling(50).mean().to_numpy()
    hi = pd.Series(m["high"]).rolling(N).max().shift(1).to_numpy()
    lo = pd.Series(m["low"]).rolling(N).min().shift(1).to_numpy()
    h1 = _arrays(ctx.h1_rows)
    h1_tr = _trend(h1)
    m5 = _arrays(ctx.m5_rows)
    # "Now" = the M15 bar containing the newest completed M5 bar. When that M5 bar is the last of its
    # M15 bar, the M15 bar is complete too -- its touch must still count as happening NOW, not as history.
    cur15 = (int(m5["t"][-1]) // 900) * 900
    last = int(np.searchsorted(m["t"], cur15)) - 1  # newest M15 bar strictly before the current one
    if last < N + 50:
        return None
    rt = _COST_PIPS.get(ctx.symbol, 1.2) * _pip(ctx.symbol)
    starts = {1: _chain_starts(m, 1), -1: _chain_starts(m, -1)}
    forming = np.flatnonzero(m5["t"] >= cur15)  # M5 bars of the current M15 bar, newest last
    busy_until = -1  # M15 bar index until which a simulated trade is open
    status = None
    for i in range(N + 50, last + 1):
        if i < busy_until:
            continue
        known = m["t"][i] + 900
        k1 = int(np.searchsorted(h1["t"] + 3600, known, side="right")) - 1
        for d, edge in ((1, hi[i]), (-1, lo[i])):
            if not ((m["close"][i] - edge) * d >= MIN_BRK * a[i] and exp[i] >= MIN_EXP and k1 >= 0 and h1_tr[k1] == d):
                continue
            js = starts[d][(starts[d] <= i) & (starts[d] >= i - ZONE_LOOKBACK)]
            if not len(js):
                continue
            B = int(js[-1]) - 2
            entry = m["high"][B] if d == 1 else m["low"][B]
            stop = (m["low"][B] if d == 1 else m["high"][B]) - d * STOP_BUF * a[B]
            if i + 1 <= last:
                open_next = m["open"][i + 1]
            else:
                q = int(np.searchsorted(m5["t"], known))
                if q >= len(m5["t"]):
                    continue
                open_next = m5["open"][q]
            target = open_next + d * TGT_ATR * a[i]
            if (entry - stop) * d < MIN_STOP_SPREADS * rt or (target - entry) * d <= 0:
                continue
            win = slice(i + 1, min(last + 1, i + 1 + PLAN_BARS))
            touch = (m["low"][win] <= entry) if d == 1 else (m["high"][win] >= entry)
            if touch.any():  # touched on a completed M15 bar -> research trade happened (or was missed live)
                k = i + 1 + int(np.argmax(touch))
                if (m["open"][k] - stop) * d <= 0:  # gapped through the stop: research takes no trade
                    continue
                busy_until = _exit_bar(m, k, d, stop, target, last)
                status = {"dir": d, "status": "CONSUMED" if busy_until <= last else "IN_TRADE", "signal_bar": i}
                break
            if last + 1 - (i + 1) >= PLAN_BARS:  # window over without a touch
                continue
            hit5 = (m5["low"][forming] <= entry) if d == 1 else (m5["high"][forming] >= entry)
            if not len(forming) or not hit5.any():
                status = status or {"dir": d, "status": "WAITING", "signal_bar": i}
                continue
            first = int(np.argmax(hit5))
            through = (m5["low"][forming[first]] <= stop) if d == 1 else (m5["high"][forming[first]] >= stop)
            if first == len(forming) - 1 and not through:
                return {"dir": d, "status": "TRIGGERED", "signal_bar": int(i), "entry": float(entry), "stop": float(stop),
                        "target": float(target), "zone_base_time": int(m["t"][B]), "signal_time": int(m["t"][i]),
                        "atr": float(a[i]), "atr_expansion": float(exp[i])}
            busy_until = last + 1  # touched earlier in the forming bar: that trade is (or would be) open now
            status = {"dir": d, "status": "IN_TRADE", "signal_bar": i}
            break
    return status


def evaluate_donchian_imbalance(ctx: StrategyContext) -> StrategySignal:
    p = plan(ctx)
    if p is None or p["status"] != "TRIGGERED":
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason=f"NO_ACTIVE_TRIGGER:{(p or {}).get('status', 'NO_PLAN')}")
    d = p["dir"]
    direction = "LONG" if d == 1 else "SHORT"
    price = float(ctx.ask if d == 1 else ctx.bid)
    if (price - p["stop"]) * d <= 0 or (p["target"] - price) * d <= 0:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="PRICE_OUTSIDE_PLAN")
    if abs(p["target"] - price) / abs(price - p["stop"]) > MAX_RR:
        return _no_signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", reason="RR_ABOVE_SANITY_CAP")
    evidence = {"strategy_version": "combo_b_v1", "planned_entry": p["entry"], "signal_time": p["signal_time"],
                "zone_base_time": p["zone_base_time"], "atr_m15": p["atr"], "atr_expansion": round(p["atr_expansion"], 3),
                "target_basis": "signal_next_open_plus_4atr", "adaptive_management": "none"}
    return _signal(ctx, strategy_id=_STRATEGY_ID, family=_STRATEGY_ID, timeframe="M15", direction=direction, strength=70.0,
                   entry=Decimal(str(price)), stop=Decimal(str(p["stop"])), target=Decimal(str(p["target"])), evidence=evidence)
