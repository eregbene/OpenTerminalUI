"""htf_fvg -- top-down imbalance strategy: higher-timeframe FVG for direction + POI, M15 confirmation for entry.

Same input and plan lifecycle as bensim.strategy (completed M15/H1/M5 bars, UTC open times; TRIGGERED only on
the first M5 touch), so the backtester and a live engine can swap it in. Built from bensim.indicators
(LuxAlgo-style), as LuxAlgo's HTF FVG workflow and ICT's HTF-POI -> LTF-MSS model describe:

  1. HTF POI     an open (unmitigated) LuxAlgo FVG on H1 (or H4) gives the direction and the zone
  2. tap         price trades back into that HTF FVG on M15 (after the gap is known) within `tap_bars`
  3. LTF shift   an M15 internal-structure break (MSS/CHoCH or BOS) in the gap's direction after the tap
  4. displacement the leg from the tap low to the break left an open M15 FVG (LuxAlgo rules)
  5. entry       limit at that M15 FVG's 50% (CE) or near edge; stop below the leg origin (the sweep/tap low)
  6. target      nearest opposite liquidity ahead (EQH/EQL, previous-day, Asian, swing extreme) at >= min_rr,
                 else rr_target x risk
Confirmations from indicators.confirm() are recorded on every plan and can be required via `confirm`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import indicators
from .strategy import atr, order_for, pip  # noqa: F401  (order_for re-exported for the backtester)

Bars = dict[str, np.ndarray]


@dataclass(frozen=True)
class FvgParams:
    htf: str = "H1"  # "H1" | "H4"
    tap_bars: int = 16  # the M15 shift must come within N bars after price tapped the HTF FVG
    breaks: str = "any"  # "any" | "choch" | "bos" (internal structure)
    entry_at: str = "ce"  # "ce" | "edge"
    stop_buffer_atr: float = 0.1
    min_rr: float = 1.5
    rr_target: float = 2.0
    confirm: tuple = ()  # names from indicators.passes()
    confirm_lookback: int = 20
    plan_bars: int = 48
    min_stop_costs: float = 8.0
    max_rr: float = 20.0
    max_hold_bars: int = 200
    cost_pips: dict[str, float] = field(default_factory=lambda: {"EURUSD": 0.9, "GBPUSD": 1.2, "USDJPY": 1.0, "AUDUSD": 1.0,
                                                                  "USDCAD": 1.1, "USDCHF": 1.1, "XAUUSD": 3.0})


def to_h4(h1: Bars) -> Bars:
    key = h1["t"] // 14400
    s = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    e = np.r_[s[1:], len(key)]
    return {"t": key[s] * 14400, "open": h1["open"][s], "high": np.maximum.reduceat(h1["high"], s),
            "low": np.minimum.reduceat(h1["low"], s), "close": h1["close"][e - 1]}


def htf_gap_tapped(m: Bars, i: int, d: int, gaps: list[dict], htf_t: np.ndarray, sec: int, tap_bars: int) -> tuple[dict, int] | None:
    """Newest open HTF FVG in direction d that M15 price traded into within the last tap_bars bars.
    A gap is usable once its 3rd HTF bar has closed and until a HTF bar closes it (LuxAlgo fill rule)."""
    now = m["t"][i] + 900
    known = int(np.searchsorted(htf_t + sec, now, side="right")) - 1  # last closed HTF bar
    for g in sorted(gaps, key=lambda z: -z["formed"]):
        if g["dir"] != d or g["formed"] > known or g["mitigated"] <= known:
            continue
        since = int(np.searchsorted(m["t"], htf_t[g["formed"]] + sec))  # first M15 bar after the gap is known
        lo = max(since, i - tap_bars)
        if lo > i:
            continue
        seg = slice(lo, i + 1)
        tap = np.flatnonzero(m["low"][seg] <= g["top"]) if d == 1 else np.flatnonzero(m["high"][seg] >= g["bottom"])
        if len(tap):
            return g, lo + int(tap[0])
    return None


def _setup(m: Bars, ind: dict, i: int, d: int, gaps: list[dict], htf: Bars, sec: int, a: np.ndarray, p: FvgParams, rt: float) -> dict | None:
    it = ind["internal"]
    hit = htf_gap_tapped(m, i, d, gaps, htf["t"], sec, p.tap_bars)
    if hit is None:
        return None
    g, tap = hit
    piv = int(it["pivot"][i])
    lo = min(tap, piv) if piv >= 0 else tap
    seg = slice(lo, i + 1)
    L = lo + int(np.argmin(m["low"][seg]) if d == 1 else np.argmax(m["high"][seg]))  # leg origin = tap/sweep extreme
    disp = [z for z in ind["fvgs"] if z["dir"] == d and L + 2 <= z["formed"] <= i < z["mitigated"]]
    if not disp:
        return None
    z = disp[-1]
    near, far = (z["top"], z["bottom"]) if d == 1 else (z["bottom"], z["top"])
    entry = (near + far) / 2 if p.entry_at == "ce" else near
    stop = (m["low"][L] if d == 1 else m["high"][L]) - d * p.stop_buffer_atr * a[i]
    risk = (entry - stop) * d
    if risk < p.min_stop_costs * rt:
        return None
    ahead = [lvl for _, lvl in indicators._pools(ind, i, -d) if (lvl - entry) * d >= p.min_rr * risk]
    target = min(ahead, key=lambda x: abs(x - entry)) if ahead else entry + d * p.rr_target * risk
    checks = indicators.confirm(ind, m, i, d, float(entry), float(stop), p.confirm_lookback)
    if not indicators.passes(checks, p.confirm):
        return None
    checks |= {"ltf_break": "CHoCH" if it["kind"][i] else "BOS", "htf_gap_atr": round((g["top"] - g["bottom"]) / a[i], 2),
               "target_is_liquidity": bool(ahead), "rr_planned": round((target - entry) * d / risk, 2)}
    return {"entry": float(entry), "stop": float(stop), "target": float(target), "zone_bar": z["formed"], "checks": checks}


def plan(m: Bars, h1: Bars, m5: Bars, symbol: str, p: FvgParams = FvgParams(), waiting: list | None = None) -> dict[str, Any] | None:
    """Same contract as bensim.strategy.plan."""
    if len(m["t"]) < 100 or len(h1["t"]) < 10 or not len(m5["t"]):
        return None
    cur15 = (int(m5["t"][-1]) // 900) * 900
    last = int(np.searchsorted(m["t"], cur15)) - 1
    if last < 60:
        return None
    mm = {k: v[:last + 1] for k, v in m.items()}
    htf, sec = (to_h4(h1), 14400) if p.htf == "H4" else (h1, 3600)
    k_closed = int(np.searchsorted(htf["t"] + sec, cur15, side="right"))  # completed HTF bars only
    htf = {k: v[:k_closed] for k, v in htf.items()}
    if len(htf["t"]) < 5:
        return None
    ind = indicators.compute(mm)
    gaps = indicators.fvgs(htf)
    a = atr(mm)
    rt = p.cost_pips.get(symbol, 1.2) * pip(symbol)
    forming = np.flatnonzero(m5["t"] >= cur15)
    it = ind["internal"]
    busy_until, status = -1, None
    for i in np.flatnonzero(it["event"] != 0):
        i = int(i)
        if i < 50 or i < busy_until or last - i > p.plan_bars:
            continue
        d = int(it["event"][i])
        if p.breaks != "any" and ("choch" if it["kind"][i] else "bos") != p.breaks:
            continue
        s = _setup(mm, ind, i, d, gaps, htf, sec, a, p, rt)
        if s is None:
            continue
        entry, stop, target = s["entry"], s["stop"], s["target"]
        win = slice(i + 1, min(last + 1, i + 1 + p.plan_bars))
        touch = (m["low"][win] <= entry) if d == 1 else (m["high"][win] >= entry)
        ran = (m["high"][win] >= target) if d == 1 else (m["low"][win] <= target)
        if touch.any():
            k = i + 1 + int(np.argmax(touch))
            if ran[:k - i - 1].any() or (m["open"][k] - stop) * d <= 0:
                continue
            busy_until = k + p.max_hold_bars
            status = {"dir": d, "status": "IN_TRADE", "signal_bar": i}
            break
        if ran.any() or last + 1 - (i + 1) >= p.plan_bars:
            continue
        hit5 = (m5["low"][forming] <= entry) if d == 1 else (m5["high"][forming] >= entry)
        if not len(forming) or not hit5.any():
            status = status or {"dir": d, "status": "WAITING", "signal_bar": i}
            if waiting is not None:
                waiting.append((d, entry, stop))
            continue
        first = int(np.argmax(hit5))
        through = (m5["low"][forming[first]] <= stop) if d == 1 else (m5["high"][forming[first]] >= stop)
        if first == len(forming) - 1 and not through:
            return {"dir": d, "status": "TRIGGERED", "entry": entry, "stop": stop, "target": target,
                    "signal_time": int(m["t"][i]), "zone_base_time": int(m["t"][s["zone_bar"]]), "atr": float(a[i]),
                    "checks": s["checks"]}
        busy_until, status = last + 1, {"dir": d, "status": "IN_TRADE", "signal_bar": i}
        break
    return status


def _selfcheck() -> None:
    """H1 bullish gap known at 03:00; M15 dips into it, then rallies through a swing -> htf_gap_tapped finds it."""
    h1 = {"t": np.arange(6) * 3600, "open": np.array([1.0, 1.0, 1.012, 1.02, 1.02, 1.02]),
          "high": np.array([1.002, 1.013, 1.022, 1.025, 1.024, 1.023]), "low": np.array([0.998, 1.0, 1.010, 1.015, 1.016, 1.017]),
          "close": np.array([1.001, 1.012, 1.021, 1.02, 1.02, 1.02])}
    h1["close"][1] = 1.012  # body of the middle bar > threshold
    gaps = indicators.fvgs(h1)
    g = [z for z in gaps if z["dir"] == 1]
    assert g and g[0]["bottom"] == 1.002 and g[0]["top"] == 1.010, gaps
    t15 = np.arange(24) * 900
    low = np.full(24, 1.016)
    low[14] = 1.009  # dips into the gap at 03:30, after it is known (bar 2 closes 03:00)
    m = {"t": t15, "open": low + 0.001, "high": low + 0.003, "low": low, "close": low + 0.002}
    hit = htf_gap_tapped(m, 20, 1, gaps, h1["t"], 3600, 16)
    assert hit is not None and hit[1] == 14, hit
    assert htf_gap_tapped(m, 20, -1, gaps, h1["t"], 3600, 16) is None
    assert to_h4({k: np.r_[v, v] if k != "t" else np.arange(12) * 3600 for k, v in h1.items()})["t"].tolist() == [0, 14400, 28800]
    print("fvg_strategy selfcheck ok", g[0], "tap bar", hit[1])


if __name__ == "__main__":
    _selfcheck()
