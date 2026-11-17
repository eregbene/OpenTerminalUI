"""Her journal strategy (44 journaled trades, Sep-Oct 2026), coded with the rules her own trades said to keep:
  1. the daily AND the 12H structure point the same way (she trades the continuation of the 12H / daily leg)
  2. the 4H pulled back against them, then a 4H CHoCH back in the trend direction CONFIRMED by a 4H close
     (not "in formare") -> market entry on the first M5 bar after that 4H close
  3. stop beyond the 4H pullback extreme (the new protected level) + buffer; target = the 12H leg extreme
     ("in 12h high/low"); skip below min_rr. Break-even at +0.5R = backtester --be-r 0.5.
Our additions (off by default): fib_min = the 4H pullback must have retraced >= this much of the 12H leg (fib 0.618);
entry="fib" = resting limit at fib_entry of the 4H CHoCH leg (the fib618 entry); confirm_min of bensim's
(killzone, poi, sweep) on M15 -- always recorded in checks.
Structure = strategy.trend (close beyond the last confirmed swing pivot flips the trend = BOS/CHoCH by close). Her
CHoCHs are ordinary swing breaks: the protected-level reading (bennett_smc) gave only 2 4H CHoCHs in 3 months."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from . import bennett_smc as smc
from . import indicators
from .strategy import _agg, order_for, trend  # noqa: F401  (order_for: same market-order spec as fib618)

Bars = dict[str, np.ndarray]


@dataclass(frozen=True)
class GfParams:
    needs_htf = True  # (class constant) the backtester passes a long H1 window as htf
    htf_bars: int = 3200  # ~133 days of H1 -> enough D1 bars for the daily structure
    min_rr: float = 1.0
    stop_buffer_atr: float = 0.1
    fib_min: float = 0.0  # >0: pullback depth into the 12H leg must be >= this (0.618 = our fib)
    entry: str = "close"  # "close" (market after the 4H CHoCH close) | "fib" (limit at fib_entry of the 4H CHoCH leg)
    fib_entry: float = 0.618
    confirm_any: tuple = ("killzone", "poi", "sweep")
    confirm_min: int = 0
    target_r: float = 0.0  # >0: target = entry +/- target_r x risk instead of the 12H extreme (late CHoCH leaves no room)
    max_hold_bars: int = 480  # M15 bars = 5 days (swing trades)
    max_rr: float = 20.0


def _done(b: Bars, sec: int, now: int) -> Bars:
    a = _agg(b, sec)
    keep = a["t"] + sec <= now
    return {k: v[keep] for k, v in a.items()}


def plan(m: Bars, h1: Bars, m5: Bars, symbol: str, p: GfParams = GfParams(), waiting: list | None = None,
         htf: Bars | None = None) -> dict[str, Any] | None:
    if htf is None or not len(m5["t"]) or not len(htf["t"]):
        return None
    now = int(m5["t"][-1]) + 300
    if now % 14400 != 300:  # only the first M5 bar after a 4H close can trigger
        return None
    h4, h12, d1 = (_done(htf, s, now) for s in (14400, 43200, 86400))
    if len(d1["t"]) < 30 or len(h12["t"]) < 40 or len(h4["t"]) < 60 or h4["t"][-1] + 14400 != now - 300:
        return None
    t4, t12, td = trend(h4), trend(h12), trend(d1)
    i4 = len(h4["t"]) - 1
    d = int(t4[i4])
    if d == 0 or t4[i4 - 1] != -d or td[-1] != d or t12[-1] != d:  # 4H CHoCH this bar, daily + 12H agree
        return None
    lo4, hi4, lo12, hi12 = h4["low"], h4["high"], h12["low"], h12["high"]
    f4 = int(np.flatnonzero(t4[:i4] != -d)[-1]) + 1 if (t4[:i4] != -d).any() else 0  # start of the 4H pullback
    seg = slice(max(0, f4 - 2), i4 + 1)
    pull = float(lo4[seg].min() if d == 1 else hi4[seg].max())
    f12 = int(np.flatnonzero(t12 != d)[-1]) + 1 if (t12 != d).any() else 0  # start of the 12H leg
    target = float(hi12[f12:].max() if d == 1 else lo12[f12:].min())
    s12 = slice(max(0, f12 - 3), len(t12))
    leg0 = float(lo12[s12].min() if d == 1 else hi12[s12].max())
    if (target - leg0) * d <= 0:
        return None
    depth = (target - pull) * d / ((target - leg0) * d)
    if p.fib_min and depth < p.fib_min:
        return None
    stop = pull - d * p.stop_buffer_atr * float(smc.atr(h4)[i4])
    entry = float(m5["close"][-1])
    add = None
    if p.entry == "fib":
        pb = int(np.flatnonzero((lo4 if d == 1 else hi4) == pull)[-1])
        x = float(hi4[pb:i4 + 1].max() if d == 1 else lo4[pb:i4 + 1].min())
        entry = x - d * p.fib_entry * abs(x - pull)
        add = {"price": entry, "frac": 1.0}
    risk = (entry - stop) * d
    if p.target_r and risk > 0:
        target = entry + d * p.target_r * risk
    if risk <= 0 or (target - entry) * d < p.min_rr * risk:
        return None
    ind = indicators.compute(m)
    checks = indicators.confirm(ind, m, len(m["t"]) - 1, d, entry, stop)
    n_ok = sum(indicators.passes(checks, (k,)) for k in p.confirm_any)
    if p.confirm_min and n_ok < p.confirm_min:
        return None
    checks = {**checks, "n_confirm": n_ok, "depth_12h": round(float(depth), 2), "rr_planned": round((target - entry) * d / risk, 2)}
    return {"dir": d, "status": "TRIGGERED", "entry": entry, "stop": float(stop), "target": target,
            "signal_time": int(h4["t"][i4]), "checks": checks, "hold_bars": p.max_hold_bars, **({"add": add} if add else {})}
