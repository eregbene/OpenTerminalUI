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
Options:
  trigger="lsr"   his favourite reversal instead of the LTF CHoCH: inside the area a LTF swing high is WICKED above
                  without a close above it, then a close below the low the sweep leg started from; stop above the
                  sweep high
  htf="H4D1"      his daily / 4H baseline ("start with the daily or 4H"): the 4H and daily structure trends (closes)
                  must point the trade's way ("H4", "D1" or "H4D1"; needs the ~54-day H1 window the backtester passes)
  premium_entry   his "I cannot short in discount": the entry itself must still be beyond the 50% of the leg
  swept_internal  the leg's external high/low must have SWEPT internal liquidity (an internal 1H swing taken on the run
                  into it) -- "BOS + took internal liquidity = textbook external high"
  max_area_atr    the area of interest (FVGs inside the OTE) may be at most this x 1H ATR wide ("10-15 pips on 1H")
  h4_fill         the no-trade zone: the nearest 4H FVG left by the displacement must be FULLY mitigated first
  add_on          half size on the LTF CHoCH close, the other half on a limit at the CHoCH leg's 15M FVG ("if we come
                  back into the imbalance I add"); no FVG left -> full size on the close
  loss_wait_1h    after a failed LTF trigger in this leg ("after a 15M loss"), only a 1H CHoCH may trigger
  dxy="agree"     his DXY "barometer": the synthetic DXY's last 1H break must point the dollar's way for the trade
                  (EURUSD/GBPUSD/AUDUSD/XAUUSD sell = DXY bullish; USDJPY/USDCAD/USDCHF sell = DXY bearish)
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
    trigger: str = "choch"  # "choch" | "lsr"
    dxy: str = "off"  # "off" | "agree"
    htf: str = "off"  # "off" | "H4" | "D1" | "H4D1"
    premium_entry: bool = False
    swept_internal: bool = False
    max_area_atr: float = 0.0  # 0 = off; 1.5 ~ his 10-15 pips on EURUSD 1H
    h4_fill: bool = False
    add_on: bool = False
    loss_wait_1h: bool = False
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


def fvg_zones(b: Bars, d: int, t0: int, t1: int, now_i: int, lo: float, hi: float) -> list[tuple[float, float]]:
    """Unmitigated plain 3-candle FVGs in direction d, formed in [t0, t1] (the breaking leg), overlapping [lo, hi]."""
    return [(z["bottom"], z["top"]) for z in indicators.fvgs(b, auto=False)
            if z["dir"] == d and t0 <= b["t"][z["formed"]] <= t1 and z["mitigated"] > now_i and z["top"] >= lo and z["bottom"] <= hi]


def fvg_in_area(b: Bars, d: int, t0: int, t1: int, now_i: int, lo: float, hi: float) -> bool:
    return bool(fvg_zones(b, d, t0, t1, now_i, lo, hi))


def agg(h1_long: Bars, sec: int) -> Bars:
    """H1 bars -> sec bars on UTC boundaries (4H / daily)."""
    key = h1_long["t"] // sec
    s = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    e = np.r_[s[1:], len(key)]
    return {"t": key[s] * sec, "open": h1_long["open"][s], "high": np.maximum.reduceat(h1_long["high"], s),
            "low": np.minimum.reduceat(h1_long["low"], s), "close": h1_long["close"][e - 1]}


def swept_internal(h: Bars, leg: dict, length: int = 3, near: int = 3) -> bool:
    """The external high (sell) was made by a run that took out an internal 1H swing high within `near` bars of it."""
    d, sb = leg["d"], leg["s_bar"]
    tops, btms = indicators.swings(_cut(h, sb + 1), length)
    x = h["high"] if d == -1 else -h["low"]
    for _, (pb, lvl) in (tops if d == -1 else btms).items():
        lv = lvl if d == -1 else -lvl
        if pb >= sb or pb < sb - 48 or lv >= x[sb]:
            continue
        took = np.flatnonzero(x[pb + 1:sb + 1] > lv)
        if len(took) and pb + 1 + took[0] >= sb - near:
            return True
    return False


def h4_filled(h4: Bars, m15: Bars, leg: dict, t0: int, t1: int) -> bool:
    """No-trade zone: the nearest 4H FVG the displacement left (formed in [t0, t1]) must have been FULLY mitigated --
    price traded back to its far edge (candle-1 low of a bearish gap / candle-1 high of a bullish one)."""
    d = leg["d"]
    gaps = [z for z in indicators.fvgs(h4, auto=False) if z["dir"] == d and t0 <= h4["t"][z["formed"]] <= t1]
    if not gaps:
        return True
    z = min(gaps, key=lambda g: g["top"]) if d == -1 else max(gaps, key=lambda g: g["bottom"])
    since = m15["t"] >= h4["t"][z["formed"]] + 14400
    if not since.any():
        return False
    return bool(m15["high"][since].max() >= z["top"]) if d == -1 else bool(m15["low"][since].min() <= z["bottom"])


USD_QUOTE = {"EURUSD", "GBPUSD", "AUDUSD", "XAUUSD"}  # the dollar is the quote: pair down = dollar up
_DXY: dict | None = None


def dxy_h1() -> Bars:
    """Synthetic DXY 1H bars from the DB (bensim.dxy --store), loaded once per process."""
    global _DXY
    if _DXY is None:
        from .backtest import load_m5, resample
        _DXY = resample(load_m5("DXY", None, None), 3600)
    return _DXY


def lsr_trigger(b: Bars, d: int, from_i: int, length: int, mb: np.ndarray) -> float | None:
    """Liquidity-sweep reversal on the LTF bars, firing on the last bar: sell (d=-1) = a swing high formed before the
    sweep is wicked above but not closed above, then the last bar closes below the low the sweep leg started from.
    Returns the sweep extreme (for the stop) or None."""
    h, lo, c = b["high"], b["low"], b["close"]
    j = len(c) - 1
    tops, btms = indicators.swings(b, length)
    pivs = tops if d == -1 else btms
    for k in range(j - 1, max(from_i, 1) - 1, -1):  # newest sweep first
        for conf, (pb, lvl) in pivs.items():
            if conf >= k or pb < from_i - 3 * length:
                continue
            swept = (h[k] > lvl >= c[k]) if d == -1 else (lo[k] < lvl <= c[k])
            if not swept:
                continue
            trig = float(lo[pb:k + 1].min() if d == -1 else h[pb:k + 1].max())  # the low the sweep leg started from
            ext = float(h[k:j + 1].max() if d == -1 else lo[k:j + 1].min())
            beyond = (c[k + 1:j + 1] > lvl) if d == -1 else (c[k + 1:j + 1] < lvl)
            if beyond.any():
                continue  # accepted above the high: not a sweep
            fire = (c[j] + mb[j] < trig <= c[j - 1] + mb[j - 1]) if d == -1 else (c[j] - mb[j] > trig >= c[j - 1] - mb[j - 1])
            return ext if fire else None
    return None


def htf_trend(h1_long: Bars, sec: int, length: int, now: int) -> int:
    """Structure trend (close-based breaks) of H1 bars aggregated to `sec`, completed bars only."""
    b = agg(h1_long, sec)
    b = _cut(b, int(np.searchsorted(b["t"] + sec, now, side="right")))
    if len(b["t"]) < 3 * length:
        return 0
    return int(indicators.structure(b, length, indicators.atr_n(b, 200), min_break=0.25 * atr(b))["trend"][-1])


def plan(m: Bars, h1: Bars, m5: Bars, symbol: str, p: BennettParams = BennettParams(), waiting: list | None = None,
         htf: Bars | None = None) -> dict[str, Any] | None:
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
    t0, t1 = int(h["t"][leg["s_bar"]]), t_ext + 3600
    if p.swept_internal and not swept_internal(h, leg):
        return None
    if p.need_fvg or p.max_area_atr:
        zones = fvg_zones(h, d, t0, t1, len(h["t"]) - 1, z_lo, z_hi) + fvg_zones(m15, d, t0, t1, len(m15["t"]) - 1, z_lo, z_hi)
        if not zones:
            return None
        a_lo, a_hi = max(min(z[0] for z in zones), z_lo), min(max(z[1] for z in zones), z_hi)  # the refined area
        if p.max_area_atr and a_hi - a_lo > p.max_area_atr * leg["atr"]:
            return None  # "if you get 20-30 pip windows that's going to be problematic"
    if p.h4_fill:
        h4 = agg(htf if htf is not None else h, 14400)
        h4 = _cut(h4, int(np.searchsorted(h4["t"] + 14400, now, side="right")))
        if not h4_filled(h4, m15, leg, t0, t1):
            return None
    if p.htf != "off":
        if htf is None:
            return None
        if "H4" in p.htf and htf_trend(htf, 14400, 5, now) != d:
            return None
        if "D1" in p.htf and htf_trend(htf, 86400, 3, now) != d:
            return None
    if p.dxy == "agree":
        dx = dxy_h1()
        dx = _cut(dx, int(np.searchsorted(dx["t"] + 3600, now, side="right")))
        dl = h1_leg(dx, p) if len(dx["t"]) >= 60 else None
        want = -d if symbol in USD_QUOTE else d  # the dollar direction this trade needs
        if dl is None or dl["d"] != want:
            return None
    la = atr(ltf)
    j = len(ltf["t"]) - 1
    if ltf["t"][j] < touch_t:
        return None
    failed = False
    if p.loss_wait_1h:  # did an earlier trigger in this leg fail? (an M15 CHoCH our way, later broken the other way)
        # ponytail: detected on M15 structure since the OTE touch; a failed 5M-only trigger can be missed
        s15 = indicators.structure(m15, p.ltf_len, indicators.atr_n(m15, 200), min_break=p.break_atr * atr(m15))
        k0 = int(np.searchsorted(m15["t"], touch_t))
        ours = np.flatnonzero((s15["event"][k0:] == d) & (s15["kind"][k0:] == 1))
        failed = bool(len(ours)) and bool((s15["event"][k0 + ours[0] + 1:] == -d).any())
    if failed:  # "now I need the 1H to convince me": the trigger must be a 1H CHoCH on the 1H candle that just closed
        if int(m5["t"][-1]) % 3600 != 3300 or int(h["t"][-1]) + 3600 != int(m5["t"][-1]) + 300:
            return None  # only on the M5 close that is also the 1H close
        hst = indicators.structure(h, 3, indicators.atr_n(h, 200), min_break=p.break_atr * atr(h))
        if hst["event"][-1] != d or not hst["kind"][-1]:
            return None
        top = hst["top"][-1] if d == -1 else hst["btm"][-1]
        if not np.isfinite(top):
            return None
    elif p.trigger == "lsr":
        from_i = int(np.searchsorted(ltf["t"], touch_t))
        top = lsr_trigger(ltf, d, from_i, p.ltf_len, p.break_atr * la)
        if top is None:
            return None
    else:
        lst = indicators.structure(ltf, p.ltf_len, indicators.atr_n(ltf, 200), min_break=p.break_atr * la)
        if lst["event"][j] != d or not lst["kind"][j]:
            return None  # the trigger is a LTF CHoCH in our direction, made on the candle that just closed
        top = lst["top"][j] if d == -1 else lst["btm"][j]  # the LTF swing the counter-move came from (lower high)
        if not np.isfinite(top):
            return None
    entry = float(m5["close"][-1])
    if p.premium_entry and (entry - (leg["ext"] + leg["start"]) / 2) * d > 0:
        return None  # already back in discount (sells) / premium (buys): "I cannot short down here"
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
    add = None
    if p.add_on:  # half now, half at the CHoCH leg's 15M imbalance (if it left one between entry and stop)
        lo_t = int(ltf["t"][max(0, j - 36)])
        gaps = fvg_zones(m15, d, lo_t, int(m15["t"][-1]), len(m15["t"]) - 1, min(entry, stop), max(entry, stop))
        edges = [g[0] if d == -1 else g[1] for g in gaps]  # the near edge price retraces into first
        edges = [e for e in edges if (e - entry) * d < 0 and (e - stop) * d > 0]
        if edges:
            add = {"price": float(min(edges) if d == -1 else max(edges)), "frac": 0.5}
    checks = {"h1_break": leg["kind"], "add_on": add is not None, "after_failed_trigger": failed, "ote": [round(z_lo, 5), round(z_hi, 5)], "target_is_t1": target == leg["ext"],
              "rr_planned": round((target - entry) * d / risk, 2), "ltf": p.ltf, "trigger": p.trigger, "dxy": p.dxy}
    return {"dir": d, "status": "TRIGGERED", "entry": entry, "stop": stop, "target": float(target),
            "signal_time": int(ltf["t"][j]), "zone_base_time": int(h["t"][leg["e"]]), "atr": float(la[j]), "checks": checks,
            **({"add": add} if add else {})}


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
    # LSR sell: swing high 1.10 at bar 3 (confirmed bar 6 with length 3), bar 8 wicks to 1.102 but closes 1.098,
    # the sweep leg started from the 1.090 low (bar 5); the last bar closes 1.088 < 1.090 -> fires, stop ref 1.102
    hi_ = np.array([1.093, 1.096, 1.098, 1.100, 1.095, 1.092, 1.094, 1.097, 1.102, 1.096, 1.090])
    lo_ = hi_ - 0.003
    lo_[5] = 1.090
    cl = hi_ - 0.001
    cl[8], cl[10] = 1.098, 1.088
    lo_[10] = 1.087
    bb = {"t": np.arange(11) * 900, "open": cl, "high": hi_, "low": lo_, "close": cl}
    assert lsr_trigger(bb, -1, 0, 3, np.zeros(11)) == 1.102
    cl2 = cl.copy()
    cl2[9] = 1.1005  # a close above the swept high = acceptance, not a sweep
    assert lsr_trigger({**bb, "close": cl2}, -1, 0, 3, np.zeros(11)) is None
    # rising staircase of H1 bars over 40 days -> 4H and daily structure trend up; reversed -> down
    n = 960
    x = 1.0 + np.arange(n) * 1e-4 + 0.002 * np.sin(np.arange(n) / 6) + 0.02 * np.sin(np.arange(n) * 2 * np.pi / 120)  # 5-day waves
    h1 = {"t": np.arange(n) * 3600, "open": x, "high": x + 5e-4, "low": x - 5e-4, "close": x + 1e-4}
    assert htf_trend(h1, 14400, 5, n * 3600) == 1 and htf_trend(h1, 86400, 3, n * 3600) == 1
    hd = {**h1, "open": x[::-1], "high": x[::-1] + 5e-4, "low": x[::-1] - 5e-4, "close": x[::-1] - 1e-4}
    assert htf_trend(hd, 14400, 5, n * 3600) == -1
    # swept internal liquidity: internal high 1.050 at bar 3, the run to the external high (bar 9) takes it at bar 8
    hh = np.array([1.040, 1.045, 1.048, 1.050, 1.046, 1.043, 1.044, 1.047, 1.052, 1.058, 1.050, 1.045])
    hb = {"t": np.arange(12) * 3600, "open": hh - 0.001, "high": hh, "low": hh - 0.004, "close": hh - 0.002}
    assert swept_internal(hb, {"d": -1, "s_bar": 9})
    assert not swept_internal(hb, {"d": -1, "s_bar": 2})  # no internal high before it
    # 4H no-trade zone: bearish 4H gap 1.070-1.080 (candle-1 low 1.080) must be traded back up to 1.080
    h4 = {"t": np.arange(4) * 14400, "open": np.array([1.09, 1.08, 1.06, 1.06]), "high": np.array([1.095, 1.082, 1.070, 1.065]),
          "low": np.array([1.080, 1.060, 1.055, 1.050]), "close": np.array([1.081, 1.062, 1.058, 1.052])}
    t15 = 3 * 14400 + np.arange(8) * 900
    up = {"t": t15, "high": np.array([1.06, 1.07, 1.075, 1.081, 1.07, 1.06, 1.06, 1.06]), "low": np.full(8, 1.05)}
    assert h4_filled(h4, up, {"d": -1}, 0, 3 * 14400)
    assert not h4_filled(h4, {**up, "high": up["high"].clip(max=1.078)}, {"d": -1}, 0, 3 * 14400)
    # add-on fill in the backtester: sell 1.1000, stop 1.1010, target 1.0970, add at 1.1005 -> filled, then target
    from types import SimpleNamespace
    from .backtest import Exec, _simulate
    ex = Exec(raw_spread_pips={"EURUSD": 0.0}, slippage_pips=0.0, commission_per_lot_rt=0.0)
    bars = {"t": 1_780_000_000 + np.arange(4) * 300, "open": np.array([1.1, 1.1, 1.1003, 1.098]),
            "high": np.array([1.1001, 1.1006, 1.1004, 1.0985]), "low": np.array([1.0999, 1.0998, 1.0990, 1.0965]),
            "close": np.array([1.1, 1.1003, 1.0992, 1.0968])}
    o = {"side": "SELL", "price": 1.1000, "sl": 1.1010, "tp": 1.0970, "rr": 3.0,
         "plan": {"signal_time": 0, "checks": {}, "add": {"price": 1.1005, "frac": 0.5}}}
    tr = _simulate("EURUSD", bars, 0, o, SimpleNamespace(max_hold_bars=10), ex, 0.0)
    assert tr["add_filled"] and tr["exit_reason"] == "target" and abs(tr["r"] - (0.5 * 3 + 0.5 * 3.5)) < 1e-6, tr
    print("bennett selfcheck ok", round(lo, 4), round(hi, 4))


if __name__ == "__main__":
    _selfcheck()
