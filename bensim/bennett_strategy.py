"""bennett -- Justin Bennett's SMC entry model, built step by step from the manual top-down read of EURUSD Apr-Jul 2026
(docs/justin_bennett_smc.md sections 1-6) and his 14 videos. Every concept comes from bensim.bennett_smc (his own
definitions; no LuxAlgo, nothing from the archived system). Same plan() contract as bensim.strategy.

The manual process, as code (sell; buys are the mirror):
  1. daily bias      the daily structure trend (protected-level BOS/CHoCH, closes only) must be DOWN -- sells only
  2. leg             the last BOS/CHoCH on the leg timeframe (4H default, "4H -> 15M/5M"; 1H as in his base video):
                     fib from the PROTECTED high (where the displacement began) to the running external low
  3. relief          price must trade back into the OTE (62-79%); reaching only the 50% is not enough (setups B and
                     late June were skipped by hand); taking the protected high kills the idea
  4. area            an FVG of the leg (leg TF / 1H / 15M) overlapping the OTE, untouched before this relief
                     (one-time use)
  5. liquidity       equal highs printed in the relief = obvious buy-side liquidity (1.1620 in setup C)
  6. trigger         a CHoCH on the trigger timeframe (5M default) on the candle that just closed, after the OTE
                     touch, with the entry still in premium (above the leg's 50%: "I cannot short in discount")
  7. stop            above the trigger leg's protected high AND above the relief's equal highs + buffer
                     (the 17 Jun sell was stopped 1.3 pips under 1.1620 before falling 100 pips)
  8. targets         T1 = the leg's external low; T2 = untaken equal lows / older external lows beyond it; the first
                     that pays >= min_rr (he takes ~2.75-3R) -- never a far level just to reach the RR
  9. stand aside     the setup expires expire_h hours after the LATEST push into the OTE (chop = "red flag"); after a
                     LOSS in the leg -- an earlier VALID trigger (5M CHoCH our way, after the OTE touch, entry in
                     premium) whose stop was later hit -- only a 1H CHoCH may trigger: "after a 15M loss, now I need
                     the 1H to convince me" (a reversed CHoCH that was never a valid trade is not a loss)
News (his filter 6) = the backtester's --news-block. Options: trigger="lsr" (sweep reversal instead of the CHoCH),
add_on (half now, half at the trigger leg's 15M FVG), h4_fill (nearest 4H FVG of the leg fully filled first),
took_internal (the leg start swept internal liquidity), max_area_atr (area width), dxy="agree" (synthetic DXY bias).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import bennett_smc as smc
from .strategy import order_for, pip  # noqa: F401  (order_for re-exported for the backtester)

Bars = dict[str, np.ndarray]


@dataclass(frozen=True)
class BennettParams:
    needs_htf = True  # (class constant, not a param) the backtester passes a ~54-day H1 window as htf
    leg_tf: str = "H4"  # "H4" | "H1"
    ltf: str = "M5"  # trigger timeframe "M5" | "M15"
    bias: str = "D1"  # "D1" | "D1H4" | "W1D1" | "off" -- higher-timeframe structure that must agree
    htf_bars: int = 1300  # H1 bars the backtester passes as htf (~54 days); W1 bias needs ~6500 (~1 year)
    break_atr: float = 0.25  # close-through needed for BOS/CHoCH, x ATR(14) of that timeframe
    ote: tuple = (0.62, 0.79)
    need_fvg: bool = True
    premium_entry: bool = True
    stop_above_liquidity: bool = True
    eq_tol_atr: float = 0.1  # equal highs/lows within 0.1 x ATR of the leg timeframe
    min_rr: float = 2.75
    max_targets: int = 2  # T1, T2
    stop_buffer_atr: float = 0.1
    expire_h: float = 48.0
    after_fail: str = "1h"  # "1h" (his rule) | "stand" (no more trades in the leg) | "off"
    hold: str = "leg"  # "leg": max hold = 1.5 x the time the leg took (his time-in-trade filter), 50 h .. 10 days | "fixed"
    m5_bars: int = 1000  # M5 history passed in (~3.5 days): enough to see earlier triggers of the same leg
    trigger: str = "choch"  # "choch" | "lsr"
    add_on: bool = False
    entry: str = "close"  # "close" (market on the CHoCH close) | "fvg" (limit at the CHoCH leg's 15M FVG, else no trade)
    t1_partial: bool = False  # 50% off at T1, the rest runs to T2
    choch_plus: bool = False  # require a lower high (sell) before the CHoCH -- "a stronger change of character"
    clear_path: bool = False  # no untouched opposite imbalance between the entry and T1
    h4_fill: bool = False
    took_internal: bool = False
    ltf_swept: bool = False  # base video: the 5M level the CHoCH breaks must itself have swept internal 5M liquidity
    max_area_atr: float = 0.0
    dxy: str = "off"
    min_stop_costs: float = 8.0
    max_rr: float = 20.0
    max_hold_bars: int = 200
    cost_pips: dict[str, float] = field(default_factory=lambda: {"EURUSD": 0.9, "GBPUSD": 1.2, "USDJPY": 1.0, "AUDUSD": 1.0,
                                                                  "USDCAD": 1.1, "USDCHF": 1.1, "XAUUSD": 3.0})


USD_QUOTE = {"EURUSD", "GBPUSD", "AUDUSD", "XAUUSD"}
_DXY: dict | None = None


def _cut(b: Bars, n: int) -> Bars:
    return {k: v[:n] for k, v in b.items()}


def agg(h1: Bars, sec: int) -> Bars:
    """H1 bars -> sec bars on UTC boundaries (4H / daily)."""
    key = h1["t"] // sec
    s = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    e = np.r_[s[1:], len(key)]
    return {"t": key[s] * sec, "open": h1["open"][s], "high": np.maximum.reduceat(h1["high"], s),
            "low": np.minimum.reduceat(h1["low"], s), "close": h1["close"][e - 1]}


def closed(b: Bars, sec: int, now: int) -> Bars:
    """Only the bars of size sec that have closed by `now`."""
    return _cut(b, int(np.searchsorted(b["t"] + sec, now, side="right")))


def structure(b: Bars, p: BennettParams, seed: int = 10) -> dict[str, Any]:
    return smc.market_structure(b, p.break_atr * smc.atr(b), seed=seed)


def leg_of(b: Bars, p: BennettParams) -> dict | None:
    """Step 2: side, the protected level (displacement start) and the running external extreme of the last break."""
    ms = structure(b, p)
    if not ms["events"] or ms["trend"][-1] == 0:
        return None
    e, kind, d, _, piv = ms["events"][-1]
    j = len(b["t"]) - 1
    start, ext = float(ms["prot"][j]), float(ms["ext"][j])
    x_s, x_e = (b["high"], b["low"]) if d == -1 else (b["low"], b["high"])
    s_bar = int(np.flatnonzero(x_s[:j + 1] == start)[-1])
    x_bar = int(np.flatnonzero(x_e[:j + 1] == ext)[-1])
    return {"d": d, "e": e, "piv": piv, "kind": kind, "start": start, "s_bar": s_bar, "ext": ext, "x_bar": x_bar,
            "atr": float(smc.atr(b)[-1]), "ms": ms}


def dxy_h1() -> Bars:
    """Synthetic DXY 1H bars from the DB (bensim.dxy --store), loaded once per process."""
    global _DXY
    if _DXY is None:
        from .backtest import load_m5, resample
        _DXY = resample(load_m5("DXY", None, None), 3600)
    return _DXY


def plan(m: Bars, h1: Bars, m5: Bars, symbol: str, p: BennettParams = BennettParams(), waiting: list | None = None,
         htf: Bars | None = None) -> dict[str, Any] | None:
    """TRIGGERED on the M5 close that completes the trigger (M5), or the first M5 after the M15 trigger candle (M15).
    htf = a long H1 window (~54 days) for the 4H leg and the daily bias (the backtester passes it)."""
    if len(m["t"]) < 100 or len(m5["t"]) < 30 or htf is None or len(htf["t"]) < 400:
        return None
    if p.ltf == "M5" and waiting is not None:  # the backtester only re-plans mid-M15 when asked: ask for every M5
        waiting.append((1, np.inf, None))
    cur15 = (int(m5["t"][-1]) // 900) * 900
    i15 = int(np.searchsorted(m["t"], cur15)) - 1
    if p.ltf == "M15" and int(np.sum(m5["t"] >= cur15)) != 1:
        return None
    now = int(m5["t"][-1]) + 300
    h_all = closed(htf, 3600, now)  # completed 1H bars (long window, for the weekly bias)
    h = _cut({k: v[-1300:] for k, v in h_all.items()}, 1300)
    hb, sec = (closed(agg(h, 14400), 14400, now), 14400) if p.leg_tf == "H4" else (h, 3600)
    if len(hb["t"]) < 40:
        return None
    leg = leg_of(hb, p)  # step 2
    if leg is None:
        return None
    d = leg["d"]
    if p.bias != "off":  # step 1
        if structure(closed(agg(h, 86400), 86400, now), p, seed=5)["trend"][-1] != d:
            return None
        if "W1" in p.bias:  # weekly: UTC weeks starting Monday (epoch day 4 = Monday 5 Jan 1970)
            wk = agg({**h_all, "t": h_all["t"] - 4 * 86400}, 7 * 86400)
            wk = {**wk, "t": wk["t"] + 4 * 86400}
            if structure(closed(wk, 7 * 86400, now), p, seed=4)["trend"][-1] != d:
                return None
        if "H4" in p.bias and p.leg_tf != "H4" and structure(closed(agg(h, 14400), 14400, now), p)["trend"][-1] != d:
            return None
    if p.dxy == "agree":
        dx = closed(dxy_h1(), 3600, now)
        dl = leg_of(dx, p) if len(dx["t"]) >= 100 else None
        if dl is None or dl["d"] != (-d if symbol in USD_QUOTE else d):
            return None
    z_lo, z_hi = smc.ote_band(leg["start"], leg["ext"], d, p.ote)
    m15 = _cut(m, i15 + 1)
    t_ext = int(hb["t"][leg["x_bar"]])
    after = m15["t"] >= t_ext
    if not after.any():
        return None
    reach = m15["high"][after].max() if d == -1 else m15["low"][after].min()  # step 3
    if (reach < z_lo) if d == -1 else (reach > z_hi):
        return None  # relief has not reached the OTE
    if (reach > leg["start"]) if d == -1 else (reach < leg["start"]):
        return None  # took the protected level: the idea is invalid
    touch_i = int(np.flatnonzero(after & ((m15["high"] >= z_lo) if d == -1 else (m15["low"] <= z_hi)))[0])
    touch_t = int(m15["t"][touch_i])
    push_i = int(np.flatnonzero(after)[0]) + int(np.argmax(m15["high"][after]) if d == -1 else np.argmin(m15["low"][after]))
    if now - int(m15["t"][push_i]) > p.expire_h * 3600:
        return None  # step 9: no new push into the area for expire_h hours -- chop, stand aside
    t0, t1 = int(hb["t"][leg["s_bar"]]), t_ext + sec
    if p.took_internal and not smc.took_internal(hb, leg["s_bar"], d):
        return None
    if p.need_fvg or p.max_area_atr:  # step 4: the leg's FVGs in the OTE, untouched before this relief
        frames = [hb] + ([h] if hb is not h else []) + [m15]
        zones = []
        for b in frames:
            touch_b = int(np.searchsorted(b["t"], touch_t))
            zones += [(z["bottom"], z["top"]) for z in smc.fvgs(b)
                      if z["dir"] == d and t0 <= b["t"][z["formed"]] <= t1 and z["touched"] >= touch_b
                      and z["top"] >= z_lo and z["bottom"] <= z_hi]
        if not zones:
            return None
        a_lo, a_hi = max(min(z[0] for z in zones), z_lo), min(max(z[1] for z in zones), z_hi)
        if p.max_area_atr and a_hi - a_lo > p.max_area_atr * smc.atr(h)[-1]:
            return None  # "20-30 pip windows are problematic"
    if p.h4_fill:
        h4 = closed(agg(h, 14400), 14400, now)
        gaps = [z for z in smc.fvgs(h4) if z["dir"] == d and t0 <= h4["t"][z["formed"]] <= t1]
        if gaps:
            z = min(gaps, key=lambda g: g["top"]) if d == -1 else max(gaps, key=lambda g: g["bottom"])
            if z["filled"] >= len(h4["t"]):
                return None  # the nearest 4H imbalance of the leg is not fully filled yet: no-trade zone
    ltf = m5 if p.ltf == "M5" else m15
    la = smc.atr(ltf)
    j = len(ltf["t"]) - 1
    if ltf["t"][j] < touch_t:
        return None
    failed = False
    if p.after_fail != "off":  # step 9: was there a real LOSS in this leg? (a valid earlier trigger, stop hit)
        s5 = structure(m5, p, seed=6)
        a5 = smc.atr(m5)
        k0, last5 = int(np.searchsorted(m5["t"], touch_t)), len(m5["t"]) - 1
        for i, kind, dd, _, _ in s5["events"]:
            if i < k0 or i >= last5 or kind != "CHoCH" or dd != d:
                continue
            if smc.fib_pos(float(m5["close"][i]), leg["start"], leg["ext"]) < 0.5:
                continue  # in discount: never a valid trade, so not a loss
            stop_i = float(s5["prot"][i]) - d * p.stop_buffer_atr * a5[i]
            if ((m5["high"][i + 1:last5 + 1] >= stop_i) if d == -1 else (m5["low"][i + 1:last5 + 1] <= stop_i)).any():
                failed = True
                break
        if failed and p.after_fail == "stand":
            return None
    if failed:  # "now I need the 1H": a 1H CHoCH our way on the 1H candle that just closed (= this M5 close)
        if int(h["t"][-1]) + 3600 != now:
            return None
        hs = structure(h, p)
        ev = hs["events"][-1] if hs["events"] else None
        if ev is None or ev[0] != len(h["t"]) - 1 or ev[1] != "CHoCH" or ev[2] != d:
            return None
        top = float(hs["prot"][-1])
    elif p.trigger == "lsr":  # step 6 (option): his sweep reversal
        top = smc.sweep_reversal(ltf, d, int(np.searchsorted(ltf["t"], touch_t)), 2, p.break_atr * la)
        if top is None:
            return None
    else:  # step 6: a CHoCH on the candle that just closed
        ms = structure(ltf, p, seed=6)
        ev = ms["events"][-1] if ms["events"] else None
        if ev is None or ev[0] != j or ev[1] != "CHoCH" or ev[2] != d:
            return None
        if p.ltf_swept and not smc.took_internal(ltf, ev[4], -d, look=60, near=6):
            return None  # "this is my external low -- it took out the internal lows, confirmed by a 5M BOS"
        top = float(ms["prot"][j])  # the counter-move's external high = his invalidation level
        top_bar = int(np.flatnonzero((ltf["high"] if d == -1 else ltf["low"])[:j + 1] == top)[-1])
        sh, sl_ = smc.swing_points({k: v[:j] for k, v in ltf.items()}, 2)
        plus = any(top_bar < b_ < j and ((pr < top) if d == -1 else (pr > top)) for b_, _, pr in (sh if d == -1 else sl_))
        if p.choch_plus and not plus:
            return None
    entry = float(m5["close"][-1])
    if p.premium_entry and smc.fib_pos(entry, leg["start"], leg["ext"]) < 0.5:
        return None  # "I cannot short below the 50%"
    eq_tol = p.eq_tol_atr * leg["atr"]
    if p.stop_above_liquidity:  # step 7: above the relief's equal highs too
        sh, sl_ = smc.swing_points({k: v[max(0, touch_i - 6):] for k, v in m15.items()}, 2)
        lv = [e["level"] for e in smc.equal_levels(sh if d == -1 else sl_, eq_tol, high=d == -1)
              if (e["level"] - entry) * -d > 0]
        if lv:
            top = max([top] + lv) if d == -1 else min([top] + lv)
    stop = float(top) - d * p.stop_buffer_atr * la[j]
    risk = (entry - stop) * d
    if risk <= 0 or risk < p.min_stop_costs * p.cost_pips.get(symbol, 1.2) * pip(symbol):
        return None
    limit = None
    if p.entry == "fvg":  # base video: if the CHoCH leg left a 15M FVG, wait for price to come back into it
        lo_t = int(ltf["t"][max(0, j - 36)])
        edges = [(z["bottom"] if d == -1 else z["top"]) for z in smc.fvgs(m15)
                 if z["dir"] == d and m15["t"][z["formed"]] >= lo_t and z["touched"] >= len(m15["t"])]
        edges = [x for x in edges if (x - entry) * d < 0 and (x - stop) * d > 0]
        if not edges:
            return None
        limit = float(min(edges) if d == -1 else max(edges))
        risk = (limit - stop) * d
        if risk < p.min_stop_costs * p.cost_pips.get(symbol, 1.2) * pip(symbol):
            return None
    ref = limit if limit is not None else entry
    cands = [leg["ext"]] + smc.pools(hb, leg["ms"], d, len(hb["t"]) - 1, eq_tol)  # step 8
    ahead = sorted({x for x in cands if (x - ref) * d > 0}, key=lambda x: abs(x - ref))[:p.max_targets]
    target = next((x for x in ahead if (x - ref) * d >= p.min_rr * risk), None)
    if target is None:
        return None  # no logical target pays the minimum RR -- don't stretch
    if p.clear_path:  # 9 filters: nothing obstructing -- no untouched opposite imbalance between entry and target
        for b in ([hb] + ([h] if hb is not h else [])):
            for z in smc.fvgs(b):
                if z["dir"] == -d and z["touched"] >= len(b["t"]) and min(ref, target) < (z["bottom"] + z["top"]) / 2 < max(ref, target):
                    return None
    partial = None
    if p.t1_partial:  # base video: half off at T1, the rest to T2 (the next liquidity beyond T1)
        beyond = sorted({x for x in cands if (x - target) * d > 0}, key=lambda x: abs(x - target))
        if beyond:
            partial, target = {"price": float(target), "frac": 0.5}, float(beyond[0])
    add = {"price": limit, "frac": 1.0} if limit is not None else None
    if p.add_on and add is None:  # half now, half at the trigger leg's nearest untouched 15M FVG between entry and stop
        lo_t = int(ltf["t"][max(0, j - 36)])
        edges = [(z["bottom"] if d == -1 else z["top"]) for z in smc.fvgs(m15)
                 if z["dir"] == d and m15["t"][z["formed"]] >= lo_t and z["touched"] >= len(m15["t"])]
        edges = [x for x in edges if (x - entry) * d < 0 and (x - stop) * d > 0]
        if edges:
            add = {"price": float(min(edges) if d == -1 else max(edges)), "frac": 0.5}
    hold_bars = p.max_hold_bars
    if p.hold == "leg":  # "measure how long the recent legs took from high to low" -- don't cut it after a few hours
        took = (int(hb["t"][leg["x_bar"]]) - int(hb["t"][leg["s_bar"]])) // 900
        hold_bars = int(min(960, max(p.max_hold_bars, 1.5 * took)))
    checks = {"leg": f"{p.leg_tf} {leg['kind']}", "hold_bars": hold_bars, "fib_pos": round(smc.fib_pos(entry, leg["start"], leg["ext"]), 2),
              "ote": [round(z_lo, 5), round(z_hi, 5)], "target_is_t1": target == leg["ext"],
              "rr_planned": round((target - entry) * d / risk, 2), "trigger": "1H after fail" if failed else p.trigger, "add_on": add is not None,
              "choch_plus": bool(plus) if not failed and p.trigger != "lsr" else None, "entry_mode": p.entry,
              "t1_partial": partial is not None}
    return {"dir": d, "status": "TRIGGERED", "entry": entry, "stop": stop, "target": float(target),
            "signal_time": int(ltf["t"][j]), "zone_base_time": int(hb["t"][leg["e"]]), "atr": float(la[j]),
            "checks": checks, "hold_bars": hold_bars, **({"add": add} if add else {}), **({"partial": partial} if partial else {})}


def _selfcheck() -> None:
    # aggregation: 8 H1 bars -> two 4H bars with the right OHLC
    x = np.arange(8, dtype=float)
    h1 = {"t": np.arange(8) * 3600, "open": x, "high": x + 0.5, "low": x - 0.5, "close": x + 0.1}
    a = agg(h1, 14400)
    assert a["t"].tolist() == [0, 14400] and a["open"].tolist() == [0, 4] and a["high"].tolist() == [3.5, 7.5]
    assert a["low"].tolist() == [-0.5, 3.5] and a["close"].tolist() == [3.1, 7.1]
    # leg_of on a sell leg: rise to 1.10, then a fall that closes below the protected low -> start = the up-leg top
    px = np.r_[np.linspace(1.00, 1.10, 15), np.linspace(1.09, 1.03, 8)]
    b = {"t": np.arange(len(px)) * 14400, "open": px, "high": px + 0.004, "low": px - 0.004, "close": px}
    lg = leg_of(b, BennettParams(break_atr=0.0))
    assert lg["d"] == -1 and lg["ext"] == b["low"][-1] and lg["start"] > lg["ext"], lg
    lo, hi = smc.ote_band(lg["start"], lg["ext"], -1)
    assert lg["ext"] < lo < hi < lg["start"]
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
    assert tr["add_filled"] and tr["exit_reason"] == "target" and abs(tr["r"] - 3.25) < 1e-6, tr
    # T1 partial: sell 1.1000, stop 1.1010, T1 1.0970 (half), T2 1.0950 -> both hit = 0.5*3 + 0.5*5 = 4R
    bars2 = {"t": 1_780_000_000 + np.arange(4) * 300, "open": np.array([1.1, 1.0995, 1.098, 1.096]),
             "high": np.array([1.1001, 1.1, 1.0985, 1.0965]), "low": np.array([1.0999, 1.0968, 1.0975, 1.0948]),
             "close": np.array([1.1, 1.098, 1.0978, 1.0952])}
    o2 = {"side": "SELL", "price": 1.1000, "sl": 1.1010, "tp": 1.0950, "rr": 5.0,
          "plan": {"signal_time": 0, "checks": {}, "partial": {"price": 1.0970, "frac": 0.5}}}
    t2 = _simulate("EURUSD", bars2, 0, o2, SimpleNamespace(max_hold_bars=10), ex, 0.0)
    assert t2["exit_reason"] == "target" and abs(t2["r"] - 4.0) < 1e-6, t2
    # limit-only entry at 1.1005 (frac 1): R measured from the limit (risk 5 pips) -> target 1.0970 = 7R
    o3 = {"side": "SELL", "price": 1.1000, "sl": 1.1010, "tp": 1.0970, "rr": 7.0,
          "plan": {"signal_time": 0, "checks": {}, "add": {"price": 1.1005, "frac": 1.0}}}
    t3 = _simulate("EURUSD", bars, 0, o3, SimpleNamespace(max_hold_bars=10), ex, 0.0)
    assert t3["add_filled"] and t3["exit_reason"] == "target" and abs(t3["r"] - 7.0) < 1e-6, t3
    print("bennett selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
