"""bennett -- Justin Bennett's SMC entry model (Daily Price Action; 14 videos in D:/Downloads/SMC new + the
dailypriceaction.com SMC article; notes in docs/justin_bennett_smc.md). Same plan() contract as bensim.strategy.

  1. 1H bias      the last 1H BOS/CHoCH decides the side; a break is a CLOSE at least break_atr x ATR beyond the
                  level ("0.2 pip is not a break, 3-5 pips on the 1H") -- wicks never count
  2. leg + fib    from the start of the breaking displacement (the leg's high for a sell) to the new external low;
                  short only in premium, the area is the OTE 62-79% (his fib: 0 / 0.5 / 0.62 / 0.79 / 1)
  3. area         price has traded into the OTE since the external low, without taking the leg high (invalidation)
  4. FVG          (need_fvg) an unmitigated 1H or 15M FVG of the breaking leg overlaps the OTE -- his 4 FVG rules:
                  with the trend, in the leg that broke structure, in premium/discount, one-time use
  5. trigger      a lower-timeframe (15M default / 5M) CHoCH in the trade direction after the OTE touch -- the
                  internal counter-move breaks; entry at market on that close ("if there's nothing to rebalance,
                  enter on the confirmation")
  6. stop         beyond the LTF swing high the CHoCH came from (the lower high = invalidation) + buffer
  7. target       T1 = the leg's external low (the prior 1H low), else T2 = the next unswept liquidity pool -- the
                  first of those that pays >= min_rr (he takes 2.75-2.8R, not 2.5R); never a far-away level just
                  to reach the RR ("path to target", no form-fitting)
News (his filter 6) = the backtester's --news-block; emotional state / clarity check are human filters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import indicators
from .strategy import atr, order_for, pip  # noqa: F401  (order_for re-exported for the backtester)

Bars = dict[str, np.ndarray]


@dataclass(frozen=True)
class BennettParams:
    h1_len: int = 10  # 1H external swing length
    ltf: str = "M15"  # trigger timeframe: "M15" (his "less noise") | "M5" (his base video)
    ltf_len: int = 3
    break_atr: float = 0.25  # close-through needed for a BOS/CHoCH, x ATR(14) of that timeframe
    ote: tuple = (0.62, 0.79)
    need_fvg: bool = True
    min_rr: float = 2.75
    max_targets: int = 2  # T1, T2 -- no stretching
    stop_buffer_atr: float = 0.1
    min_stop_costs: float = 8.0
    max_rr: float = 20.0
    max_hold_bars: int = 200
    cost_pips: dict[str, float] = field(default_factory=lambda: {"EURUSD": 0.9, "GBPUSD": 1.2, "USDJPY": 1.0, "AUDUSD": 1.0,
                                                                  "USDCAD": 1.1, "USDCHF": 1.1, "XAUUSD": 3.0})


def _cut(b: Bars, n: int) -> Bars:
    return {k: v[:n] for k, v in b.items()}


def h1_leg(h: Bars, p: BennettParams) -> dict | None:
    """Last 1H break -> side, the displacement start (leg extreme against the trade) and the new external extreme."""
    a1 = atr(h)
    st = indicators.structure(h, p.h1_len, indicators.atr_n(h, 200), min_break=p.break_atr * a1)
    ev = np.flatnonzero(st["event"] != 0)
    if not len(ev):
        return None
    e = int(ev[-1])
    d, piv = int(st["event"][e]), int(st["pivot"][e])
    if piv < 0:
        return None
    hi, lo = h["high"], h["low"]
    start = float(hi[piv:e + 1].max() if d == -1 else lo[piv:e + 1].min())  # where the displacement started
    s_bar = piv + int(np.argmax(hi[piv:e + 1]) if d == -1 else np.argmin(lo[piv:e + 1]))
    x_bar = e + int(np.argmin(lo[e:]) if d == -1 else np.argmax(hi[e:]))  # the new external low/high (extends)
    ext = float(lo[x_bar] if d == -1 else hi[x_bar])
    return {"d": d, "e": e, "piv": piv, "kind": "CHoCH" if st["kind"][e] else "BOS", "start": start, "s_bar": s_bar,
            "ext": ext, "x_bar": x_bar, "atr": float(a1[-1])}


def ote_band(leg: dict, p: BennettParams) -> tuple[float, float]:
    """OTE prices for the leg (sell: ext + 0.62..0.79 x range up from the low)."""
    rng = (leg["start"] - leg["ext"]) * -leg["d"]  # > 0
    a, b = (leg["ext"] - leg["d"] * f * rng for f in p.ote)
    return min(a, b), max(a, b)


def fvg_in_area(b: Bars, d: int, t0: int, t1: int, now_i: int, lo: float, hi: float) -> bool:
    """An unmitigated plain 3-candle FVG in direction d, formed in [t0, t1] (the breaking leg), overlapping [lo, hi]."""
    for z in indicators.fvgs(b, auto=False):
        if z["dir"] == d and t0 <= b["t"][z["formed"]] <= t1 and z["mitigated"] > now_i and z["top"] >= lo and z["bottom"] <= hi:
            return True
    return False


def plan(m: Bars, h1: Bars, m5: Bars, symbol: str, p: BennettParams = BennettParams(), waiting: list | None = None) -> dict[str, Any] | None:
    """TRIGGERED on the first M5 close after the LTF candle whose close made the CHoCH (M15), or on that M5 (M5)."""
    if len(m["t"]) < 100 or len(h1["t"]) < 60 or len(m5["t"]) < 30:
        return None
    cur15 = (int(m5["t"][-1]) // 900) * 900
    i15 = int(np.searchsorted(m["t"], cur15)) - 1  # last completed M15 bar
    if p.ltf == "M15":
        if int(np.sum(m5["t"] >= cur15)) != 1:  # only the first M5 close of the new M15 candle
            return None
        ltf = _cut(m, i15 + 1)
    else:
        ltf = m5
        if waiting is not None:  # the backtester only re-plans mid-M15 when a waiting zone can be hit: ask for every M5
            waiting.append((1, np.inf, None))
    now = int(m5["t"][-1]) + 300
    h = _cut(h1, int(np.searchsorted(h1["t"] + 3600, now, side="right")))  # completed 1H bars only
    leg = h1_leg(h, p)
    if leg is None:
        return None
    d = leg["d"]
    z_lo, z_hi = ote_band(leg, p)
    t_ext = int(h["t"][leg["x_bar"]])
    m15 = _cut(m, i15 + 1)
    after = m15["t"] >= t_ext  # M15 bars since the external low/high
    if not after.any():
        return None
    reach = m15["high"][after].max() if d == -1 else m15["low"][after].min()
    if (reach < z_lo) if d == -1 else (reach > z_hi):
        return None  # price has not traded into the OTE yet
    if (reach > leg["start"]) if d == -1 else (reach < leg["start"]):
        return None  # took the leg high/low: the idea is invalidated
    touch_t = int(m15["t"][np.flatnonzero(after & ((m15["high"] >= z_lo) if d == -1 else (m15["low"] <= z_hi)))[0]])
    if p.need_fvg:
        t0, t1 = int(h["t"][leg["s_bar"]]), t_ext + 3600
        if not (fvg_in_area(h, d, t0, t1, len(h["t"]) - 1, z_lo, z_hi) or fvg_in_area(m15, d, t0, t1, len(m15["t"]) - 1, z_lo, z_hi)):
            return None
    la = atr(ltf)
    lst = indicators.structure(ltf, p.ltf_len, indicators.atr_n(ltf, 200), min_break=p.break_atr * la)
    j = len(ltf["t"]) - 1
    if lst["event"][j] != d or not lst["kind"][j] or ltf["t"][j] < touch_t:
        return None  # the trigger is a LTF CHoCH in our direction, made on the candle that just closed
    top = lst["top"][j] if d == -1 else lst["btm"][j]  # the LTF swing the counter-move came from (lower high)
    if not np.isfinite(top):
        return None
    entry = float(m5["close"][-1])
    stop = float(top) - d * p.stop_buffer_atr * la[j]
    risk = (entry - stop) * d
    if risk <= 0 or risk < p.min_stop_costs * p.cost_pips.get(symbol, 1.2) * pip(symbol):
        return None
    ind = indicators.compute(h)
    pools = [leg["ext"]] + [lvl for _, lvl in indicators._pools(ind, len(h["t"]) - 1, -d, fresh=True)]
    ahead = sorted({x for x in pools if (x - entry) * d > 0}, key=lambda x: abs(x - entry))[:p.max_targets]
    target = next((x for x in ahead if (x - entry) * d >= p.min_rr * risk), None)
    if target is None:
        return None  # no logical target pays the minimum RR -- don't stretch
    checks = {"h1_break": leg["kind"], "ote": [round(z_lo, 5), round(z_hi, 5)], "target_is_t1": target == leg["ext"],
              "rr_planned": round((target - entry) * d / risk, 2), "ltf": p.ltf}
    return {"dir": d, "status": "TRIGGERED", "entry": entry, "stop": stop, "target": float(target),
            "signal_time": int(ltf["t"][j]), "zone_base_time": int(h["t"][leg["e"]]), "atr": float(la[j]), "checks": checks}


def _selfcheck() -> None:
    """Sell leg: start 1.10 high, external low 1.00 -> OTE 1.062-1.079; FVG overlap and leg-window logic."""
    p = BennettParams()
    leg = {"d": -1, "start": 1.10, "ext": 1.00}
    lo, hi = ote_band(leg, p)
    assert abs(lo - 1.062) < 1e-9 and abs(hi - 1.079) < 1e-9, (lo, hi)
    lo2, hi2 = ote_band({"d": 1, "start": 1.00, "ext": 1.10}, p)  # buy mirror: 1.021-1.038
    assert abs(lo2 - 1.021) < 1e-9 and abs(hi2 - 1.038) < 1e-9, (lo2, hi2)
    # bearish 3-candle gap at bars 0-2: candle-1 low 1.07, candle-3 high 1.065 -> gap 1.065-1.07 inside the OTE
    b = {"t": np.arange(6) * 3600, "open": np.array([1.075, 1.07, 1.06, 1.055, 1.05, 1.05]),
         "high": np.array([1.08, 1.071, 1.065, 1.06, 1.055, 1.052]), "low": np.array([1.07, 1.059, 1.055, 1.05, 1.045, 1.044]),
         "close": np.array([1.071, 1.06, 1.056, 1.052, 1.046, 1.05])}
    assert fvg_in_area(b, -1, 0, 5 * 3600, 5, lo, hi)
    assert not fvg_in_area(b, -1, 3 * 3600, 5 * 3600, 5, lo, hi)  # formed outside the leg window
    assert not fvg_in_area(b, 1, 0, 5 * 3600, 5, lo, hi)
    print("bennett selfcheck ok", round(lo, 4), round(hi, 4))


if __name__ == "__main__":
    _selfcheck()
