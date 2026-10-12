"""donchian_imbalance ("combination B") -- the single strategy, shared by live trading and backtests.

Input: plain bar arrays {"t","open","high","low","close"} (t = bar OPEN time, UTC epoch seconds), completed
bars only. Same logic as the verified backend version (88% identical trades vs the research engine).

Rules
  1. Donchian signal on M15 bar i: close beyond the 20-bar channel of the bars before it by >= 0.3 ATR(14),
     ATR(14)/SMA50(ATR) >= 1.1, and the H1 swing-structure trend (k=2) agrees when bar i closes.
  2. Plan: imbalance zone left by the breakout run = base candle of the most recent same-direction
     3-candle-gap chain whose first gap completed within the last 5 bars. Entry = zone near edge,
     stop = zone far edge -/+ 0.1 ATR(base), target = open of bar i+1 +/- 4 ATR(i).
     Skip if the stop is <= 8 round-trip costs or reward:risk > 20.
  3. One trade at a time per symbol (signals during a trade are skipped). A plan lives 48 M15 bars and
     triggers once, on the first M5 bar that reaches its entry (never if that bar also reached the stop).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import indicators

Bars = dict[str, np.ndarray]


@dataclass(frozen=True)
class Params:
    channel: int = 20
    min_break_atr: float = 0.3
    min_atr_expansion: float = 1.1
    target_atr: float = 4.0
    stop_buffer_atr: float = 0.1
    plan_bars: int = 48
    zone_lookback: int = 5
    min_stop_costs: float = 8.0
    max_rr: float = 20.0
    max_hold_bars: int = 200
    # ICT/SMC filters on the signal bar -- all off by default (= the verified strategy)
    killzones: tuple = ()  # UTC (start_hour, end_hour) windows the breakout bar must open in, e.g. ((7, 10), (12, 15))
    min_body_ratio: float = 0.0  # displacement: |close-open| / range of the breakout bar
    sweep_lookback: int = 0  # >0: an opposite-side liquidity sweep (wick beyond a 20-bar extreme, close back inside) within N bars before
    discount_lookback: int = 0  # >0: long entry in the lower half (short: upper half) of the last N-bar range
    h4_bias: bool = False  # H4 swing trend must agree too
    zone_mode: str = "gap"  # "gap": base of the newest gap chain (<= zone_lookback bars); "origin": OB at the start of the impulse
    target_mode: str = "atr"  # "atr": open(i+1) +/- target_atr*ATR; "liquidity": the impulse's high/low reached before the pullback (BSL/SSL)
    min_rr_liq: float = 2.0
    fib_level: float = 0.618  # zone_mode="fib": entry at this retracement of the breakout leg, stop beyond its 100% (origin)
    fib_ext: float = 1.272  # target_mode="fib_ext": target at this extension of the leg
    balance_max_atr: float = 0.0  # >0: the channel bars before the break must range <= N x ATR (tight balance / flag / triangle)  # liquidity target: skip when target is < this many R away
    placebo_seed: int = 0  # research control: >0 moves each zone to a fake level 0.5-1.5x the real pullback depth (same stop width)
    # SMC confirmations (bensim.indicators, LuxAlgo-style) -- always recorded on the plan, required only if named here:
    # swing_trend, internal_break, choch, fvg, ob, poi (fvg or ob), discount, sweep, killzone
    confirm: tuple = ()
    entry_sec: int = 900  # entry/plan timeframe: 300 = M5, 900 = M15, 1800 = M30 (channel/plan_bars/hold count these bars)
    bias_sec: int = 3600  # trend-bias timeframe: 3600 = H1, 14400 = H4, 43200 = H12, 86400 = D1
    confirm_lookback: int = 20
    # "at least N of these": e.g. confirm_any=("killzone","poi","sweep"), confirm_min=2 (on top of the required `confirm`)
    confirm_any: tuple = ()
    confirm_min: int = 0
    # context rules from the 2026-10-09 loss analysis -- all off by default
    weekend_cancel: bool = False  # a pending order dies at the Friday close (no fills on the Sunday gap)
    h4_discount: bool = False  # entry in the cheap half (buys) / expensive half (sells) of the H4 swing range (LuxAlgo 50)
    trend_eff_min: float = 0.0  # >0: |net move| / sum of |daily moves| over the last 20 D1 bars must be >= this (trending regime)
    # round-trip cost in pips (spread + commission) used for the minimum-stop rule; ICMarkets raw-like
    cost_pips: dict[str, float] = field(default_factory=lambda: {"EURUSD": 0.9, "GBPUSD": 1.2, "USDJPY": 1.0, "AUDUSD": 1.0,
                                                                  "USDCAD": 1.1, "USDCHF": 1.1, "XAUUSD": 3.0})


def pip(symbol: str) -> float:
    return 0.1 if symbol == "XAUUSD" else (0.01 if symbol.endswith("JPY") else 0.0001)


def atr(b: Bars, n: int = 14) -> np.ndarray:
    prev = np.r_[b["close"][0], b["close"][:-1]]
    tr = np.maximum(b["high"] - b["low"], np.maximum(np.abs(b["high"] - prev), np.abs(b["low"] - prev)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().to_numpy()


def trend(b: Bars, k: int = 2) -> np.ndarray:
    """Swing-structure trend after each bar's close (+1/-1/0): a close beyond the last confirmed pivot
    (pivot j confirmed at j+k) flips the trend."""
    h, l, c = b["high"].tolist(), b["low"].tolist(), b["close"].tolist()  # lists: tiny-slice max/min without numpy overhead
    n = len(c)
    ph = {j + k: j for j in range(k, n - k) if h[j] > max(h[j - k:j]) and h[j] >= max(h[j + 1:j + k + 1])}
    pl = {j + k: j for j in range(k, n - k) if l[j] < min(l[j - k:j]) and l[j] <= min(l[j + 1:j + k + 1])}
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


def chain_starts(b: Bars, d: int) -> np.ndarray:
    h, l = b["high"], b["low"]
    j = np.arange(2, len(h))
    gj = j[(h[j - 2] < l[j]) if d == 1 else (l[j - 2] > h[j])]
    return gj[np.r_[True, np.diff(gj) > 1]] if len(gj) else gj


def _exit_bar(m: Bars, k: int, d: int, stop: float, target: float, last: int, hold: int) -> int:
    end = min(last, k + hold - 1)
    for j in range(k, end + 1):
        if (m["low"][j] <= stop) if d == 1 else (m["high"][j] >= stop):
            return j
        if j > k and ((m["high"][j] >= target) if d == 1 else (m["low"][j] <= target)):
            return j
    return k + hold - 1 if k + hold - 1 <= last else last + 1


def _ict_ok(m: Bars, i: int, d: int, entry: float, p: Params, h4_tr: np.ndarray | None, h4_t: np.ndarray | None) -> bool:
    if p.killzones:
        hr = (int(m["t"][i]) % 86400) // 3600
        if not any(a <= hr < b for a, b in p.killzones):
            return False
    if p.min_body_ratio:
        rng = m["high"][i] - m["low"][i]
        if rng <= 0 or abs(m["close"][i] - m["open"][i]) / rng < p.min_body_ratio:
            return False
    if p.sweep_lookback:
        swept = False
        for j in range(max(20, i - p.sweep_lookback), i):
            ext = m["low"][j - 20:j].min() if d == 1 else m["high"][j - 20:j].max()
            if (m["low"][j] < ext < m["close"][j]) if d == 1 else (m["high"][j] > ext > m["close"][j]):
                swept = True
                break
        if not swept:
            return False
    if p.discount_lookback:
        s = slice(max(0, i - p.discount_lookback + 1), i + 1)
        mid = (m["high"][s].max() + m["low"][s].min()) / 2
        if (entry - mid) * d > 0:
            return False
    if h4_tr is not None:
        k4 = int(np.searchsorted(h4_t + 14400, m["t"][i] + p.entry_sec, side="right")) - 1
        if k4 < 0 or h4_tr[k4] != d:
            return False
    return True


def _week(t: int) -> int:
    return (int(t) // 86400 - 2) // 7  # weeks start Saturday 00:00 UTC, so Friday and the following Sunday differ


def _agg(b: Bars, sec: int) -> Bars:
    key = b["t"] // sec
    s = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    return {"t": key[s] * sec, "open": b["open"][s], "high": np.maximum.reduceat(b["high"], s),
            "low": np.minimum.reduceat(b["low"], s), "close": b["close"][np.r_[s[1:], len(key)] - 1]}


def _context_ok(htf: Bars | None, known: int, d: int, entry: float, p: Params) -> bool:
    """H4 discount / D1 trend-efficiency rules on bars completed before `known` (htf = a long H1 window)."""
    if not (p.h4_discount or p.trend_eff_min):
        return True
    if htf is None or not len(htf["t"]):
        return False
    if p.h4_discount:
        h4 = _agg(htf, 14400)
        h4 = {k: v[h4["t"] + 14400 <= known] for k, v in h4.items()}
        if len(h4["t"]) < 120:
            return False
        sw = indicators.structure(h4, 50, indicators.atr_n(h4, 200))
        if (entry - (sw["trail_up"][-1] + sw["trail_dn"][-1]) / 2) * d >= 0:
            return False
    if p.trend_eff_min:
        d1 = _agg(htf, 86400)
        c = d1["close"][d1["t"] + 86400 <= known][-21:]
        r = np.diff(np.log(c))
        if len(r) < 20 or abs(r.sum()) / np.abs(r).sum() < p.trend_eff_min:
            return False
    return True


def plan(m: Bars, h1: Bars, m5: Bars, symbol: str, p: Params = Params(), waiting: list | None = None, htf: Bars | None = None) -> dict[str, Any] | None:
    """Plan state right after the newest completed M5 bar: TRIGGERED (with entry/stop/target) only when that
    M5 bar is the plan's first touch; otherwise WAITING / IN_TRADE / CONSUMED / None.
    If `waiting` is a list, every untouched plan's (dir, entry, stop) is appended (the backtester uses this
    to skip M5 bars that cannot trigger anything)."""
    if len(m["t"]) < p.channel + 70 or not len(h1["t"]) or not len(m5["t"]):
        return None
    a = atr(m)
    exp = a / pd.Series(a).rolling(50).mean().to_numpy()
    hi = pd.Series(m["high"]).rolling(p.channel).max().shift(1).to_numpy()
    lo = pd.Series(m["low"]).rolling(p.channel).min().shift(1).to_numpy()
    h1_tr = trend(h1)
    h4_tr = h4_t = None
    if p.h4_bias:  # H4 from H1 on UTC 4-hour boundaries; only completed H4 bars count (checked in _ict_ok)
        key = h1["t"] // 14400
        s = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
        h4 = {"t": key[s] * 14400, "high": np.maximum.reduceat(h1["high"], s), "low": np.minimum.reduceat(h1["low"], s),
              "close": h1["close"][np.r_[s[1:], len(key)] - 1]}
        h4_tr, h4_t = trend(h4), h4["t"]
    cur15 = (int(m5["t"][-1]) // p.entry_sec) * p.entry_sec  # "now" = the entry-tf bar holding the newest completed M5 bar
    last = int(np.searchsorted(m["t"], cur15)) - 1
    if last < p.channel + 50:
        return None
    rt = p.cost_pips.get(symbol, 1.2) * pip(symbol)
    starts = {1: chain_starts(m, 1), -1: chain_starts(m, -1)}
    forming = np.flatnonzero(m5["t"] >= cur15)
    busy_until, status, ind = -1, None, None
    for i in range(p.channel + 50, last + 1):
        if i < busy_until:
            continue
        known = m["t"][i] + p.entry_sec
        k1 = int(np.searchsorted(h1["t"] + p.bias_sec, known, side="right")) - 1
        for d, edge in ((1, hi[i]), (-1, lo[i])):
            if not ((m["close"][i] - edge) * d >= p.min_break_atr * a[i] and exp[i] >= p.min_atr_expansion and k1 >= 0 and h1_tr[k1] == d):
                continue
            seg = slice(i - p.channel, i + 1)
            L = i - p.channel + int(np.argmin(m["low"][seg]) if d == 1 else np.argmax(m["high"][seg]))  # breakout leg origin
            leg0 = m["low"][L] if d == 1 else m["high"][L]
            leg1 = m["high"][L:i + 1].max() if d == 1 else m["low"][L:i + 1].min()
            if p.balance_max_atr and (m["high"][i - p.channel:i].max() - m["low"][i - p.channel:i].min()) > p.balance_max_atr * a[i]:
                continue
            if p.zone_mode == "fib":
                B = L
            elif p.zone_mode == "origin":  # picture-style OB: last opposite candle at the impulse's start (lowest low of the channel for longs)
                opp = [j for j in range(max(0, L - 3), L + 1) if (m["close"][j] - m["open"][j]) * d < 0]
                B = opp[-1] if opp else L
            else:
                js = starts[d][(starts[d] <= i) & (starts[d] >= i - p.zone_lookback)]
                if not len(js):
                    continue
                B = int(js[-1]) - 2
            entry = m["high"][B] if d == 1 else m["low"][B]
            stop = (m["low"][B] if d == 1 else m["high"][B]) - d * p.stop_buffer_atr * a[B]
            if p.zone_mode == "fib":
                entry = leg1 - d * p.fib_level * abs(leg1 - leg0)
                stop = leg0 - d * p.stop_buffer_atr * a[i]
            if p.placebo_seed:  # same signal, same zone size, fake location (deterministic per signal)
                f = 0.5 + np.random.default_rng((p.placebo_seed, int(m["t"][i]))).random()
                width = (entry - stop) * d
                entry = m["close"][i] - d * f * (m["close"][i] - entry) * d
                stop = entry - d * width
            if i + 1 <= last:
                open_next = m["open"][i + 1]
            else:
                q = int(np.searchsorted(m5["t"], known))
                if q >= len(m5["t"]):
                    continue
                open_next = m5["open"][q]
            target = open_next + d * p.target_atr * a[i]
            if p.target_mode == "fib_ext":
                target = leg0 + d * p.fib_ext * abs(leg1 - leg0)
            liq = p.target_mode == "liquidity"
            if (entry - stop) * d < p.min_stop_costs * rt or (not liq and (target - entry) * d <= 0):
                continue
            if not _ict_ok(m, i, d, entry, p, h4_tr, h4_t):
                continue
            ind = ind or indicators.compute({k: v[:last + 1] for k, v in m.items()})
            checks = indicators.confirm(ind, m, i, d, float(entry), float(stop), p.confirm_lookback)
            if not indicators.passes(checks, p.confirm):
                continue
            if p.confirm_min and sum(indicators.passes(checks, (k,)) for k in p.confirm_any) < p.confirm_min:
                continue
            if not _context_ok(htf, int(known), d, float(entry), p):
                continue
            win = slice(i + 1, min(last + 1, i + 1 + p.plan_bars))
            touch = (m["low"][win] <= entry) if d == 1 else (m["high"][win] >= entry)
            if touch.any():
                k = i + 1 + int(np.argmax(touch))
                if p.weekend_cancel and _week(m["t"][k]) != _week(m["t"][i]):
                    continue
                if (m["open"][k] - stop) * d <= 0:
                    continue
                if liq:  # target = the impulse extreme made between the break and the pullback
                    target = m["high"][i:k].max() if d == 1 else m["low"][i:k].min()
                    if (target - entry) * d < p.min_rr_liq * (entry - stop) * d:
                        continue
                busy_until = _exit_bar(m, k, d, stop, target, last, p.max_hold_bars)
                status = {"dir": d, "status": "CONSUMED" if busy_until <= last else "IN_TRADE", "signal_bar": i}
                break
            if last + 1 - (i + 1) >= p.plan_bars:
                continue
            if p.weekend_cancel and _week(cur15) != _week(m["t"][i]):
                continue
            hit5 = (m5["low"][forming] <= entry) if d == 1 else (m5["high"][forming] >= entry)
            if not len(forming) or not hit5.any():
                status = status or {"dir": d, "status": "WAITING", "signal_bar": i}
                if waiting is not None:
                    waiting.append((d, float(entry), float(stop)))
                continue
            first = int(np.argmax(hit5))
            through = (m5["low"][forming[first]] <= stop) if d == 1 else (m5["high"][forming[first]] >= stop)
            if liq:
                ext = np.r_[m["high"][i:last + 1], m5["high"][forming[:first]]] if d == 1 else np.r_[m["low"][i:last + 1], m5["low"][forming[:first]]]
                target = ext.max() if d == 1 else ext.min()
                if (target - entry) * d < p.min_rr_liq * (entry - stop) * d:
                    continue
            if first == len(forming) - 1 and not through:
                return {"dir": d, "status": "TRIGGERED", "entry": float(entry), "stop": float(stop), "target": float(target),
                        "signal_time": int(m["t"][i]), "zone_base_time": int(m["t"][B]), "atr": float(a[i]), "atr_expansion": float(exp[i]), "checks": checks}
            busy_until, status = last + 1, {"dir": d, "status": "IN_TRADE", "signal_bar": i}
            break
    return status


def order_for(pl: dict[str, Any], bid: float, ask: float, p: Params = Params()) -> tuple[dict[str, Any] | None, str]:
    """Market order spec for a TRIGGERED plan at the current quote, or (None, reason)."""
    if not pl or pl.get("status") != "TRIGGERED":
        return None, f"NO_TRIGGER:{(pl or {}).get('status', 'NO_PLAN')}"
    d = pl["dir"]
    price = ask if d == 1 else bid
    if (price - pl["stop"]) * d <= 0 or (pl["target"] - price) * d <= 0:
        return None, "PRICE_OUTSIDE_PLAN"
    rr = abs(pl["target"] - price) / abs(price - pl["stop"])
    if rr > p.max_rr:
        return None, "RR_ABOVE_CAP"
    return {"side": "BUY" if d == 1 else "SELL", "price": price, "sl": pl["stop"], "tp": pl["target"], "rr": rr, "plan": pl}, "OK"
