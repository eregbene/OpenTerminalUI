"""orderflow -- Faiz SMC course (G:/Faiz SMC Trading Course: SMC Basics 1-9, Trading Strategies 1-5 and 29), built
on bensim.indicators (LuxAlgo-style swings / internal BOS-CHoCH / FVG). Same plan() contract as bensim.strategy.

mode "liquidity_ob" (video 29, "Order Block Liquidity" -- the mentor's own upgrade of order flow), sell example
(mirror for buys):
  1. shift        an internal CHoCH (market structure shift) down, M15
  2. OB           the extreme candle of the last push up before the shift; it must be a bullish (opposite) candle --
                  if the highest candle of that push is bearish, "the setup is invalid"
  3. fake-out     a later candle BODY closes above the OB (wicks do not count), not further than max_fake_atr;
                  older wicks just above the OB (liq_lookback bars) must be cleared first -- they are liquidity
  4. invalid      price touches the OB and then makes a new low beyond the shift leg before faking out = "price
                  reacts heavily from that OB", it held as a real OB (new lows BEFORE any retest just extend the leg)
  5. reclaim      a body closes back below the OB top -> entry = sell limit on the retest of the OB top
  6. stop         beyond the fake-out extreme ("fake") or beyond the OB ("box"); target = nearest opposite
                  liquidity at >= min_rr, else rr_target x risk (his examples: 3R, 6R)
mode "flow" (videos 1-5, "order flow"): after a BOS or CHoCH, fib from the leg origin to the break extreme; the most
  extreme open FVG in the leg whose zone reaches the discount (< 50%) is the POI. Entry at the FVG's near edge when the
  gap is small (<= small_fvg_atr), else at the course's OB = the FIRST candle of that FVG (not the LuxAlgo/ICT
  "last opposite candle"); stop beyond the leg origin (his "safe side"), target = the break extreme.
mode "confirmation" (JeaFx, "This entry model will change how you trade"), buy example (mirror for sells):
  1. trend        a downtrend of lower lows / lower highs
  2. confirmation a candle CLOSES above the final lower high (higher high = trend change); wicks are rejections
                  and do not count (the internal CHoCH, close-based)
  3. demand       the last candle before the impulse (small / indecision / wicky base candle at the low; if the
                  lowest candle is itself the big impulse candle, the one before it)
  4. entry        buy limit just above the demand candle's high (+ entry_buffer_atr, his "0.2 pips for spread")
  5. stop         just under the swing low of the confirmation
  6. target       continuation (swing trend already agrees): the swing high that was broken;
                  reversal (no higher-timeframe trend): the next liquidity pool beyond (he aims higher on reversals)
  context         best when the higher timeframe agrees: confirm=("swing_trend",) and/or htf_poi
htf_poi (JeaFx context, also usable in the other modes): the shift must come after price tapped an open H1 FVG in
  the trade's direction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import indicators
from .fvg_strategy import htf_gap_tapped
from .strategy import _exit_bar, atr, order_for, pip  # noqa: F401  (order_for re-exported for the backtester)

Bars = dict[str, np.ndarray]


@dataclass(frozen=True)
class OrderFlowParams:
    mode: str = "liquidity_ob"  # "liquidity_ob" (Faiz video 29) | "flow" (Faiz videos 1-5) | "confirmation" (JeaFx)
    breaks: str = "choch"  # internal break that starts a setup: "choch" | "bos" | "any"
    max_fake_atr: float = 1.5  # fake-out may run at most this far beyond the OB
    liq_lookback: int = 20
    setup_bars: int = 32  # fake-out and reclaim must complete within N M15 bars of the shift
    stop_at: str = "fake"  # "fake" | "box"
    small_fvg_atr: float = 0.5  # flow: gap <= this -> enter at the FVG, else at the first-candle OB
    stop_buffer_atr: float = 0.1
    entry_buffer_atr: float = 0.02  # confirmation: limit "a little bit above" the demand for the spread
    min_rr: float = 2.0
    rr_target: float = 3.0
    htf_poi: bool = False
    tap_bars: int = 16
    need_liquidity: bool = True  # flow/confirmation: Faiz "if there's no liquidity you're agreeing to lose" (OF video 2)
    confirm: tuple = ()
    confirm_lookback: int = 20
    plan_bars: int = 48
    min_stop_costs: float = 8.0
    max_rr: float = 20.0
    max_hold_bars: int = 200
    cost_pips: dict[str, float] = field(default_factory=lambda: {"EURUSD": 0.9, "GBPUSD": 1.2, "USDJPY": 1.0, "AUDUSD": 1.0,
                                                                  "USDCAD": 1.1, "USDCHF": 1.1, "XAUUSD": 3.0})


def liquidity_ob(m: Bars, i: int, d: int, piv: int, last: int, a: float, p: OrderFlowParams) -> dict | None:
    """Video-29 setup for a direction-d shift at bar i: {ob_bar, top, bottom, fake_ext, ready} or None.
    `ready` = the reclaim bar (entry is armed from the next bar on)."""
    o, h, lo, c = m["open"], m["high"], m["low"], m["close"]
    ext = h if d == -1 else -lo  # "higher is more extreme" against the trade
    T = piv + int(np.argmax(ext[piv:i + 1]))  # first (oldest) extreme of the push before the shift
    if (c[T] - o[T]) * d >= 0:  # must be the opposite colour (bullish top for a sell)
        return None
    top, bot = float(h[T]), float(lo[T])
    edge = top if d == -1 else bot  # the side price fakes through and is reclaimed
    leg_end = float(lo[T:i + 1].min() if d == -1 else h[T:i + 1].max())
    limit = edge - d * p.max_fake_atr * a
    old = ext[max(0, T - p.liq_lookback):T]
    liq = old[(old > ext[T]) & (old <= (limit if d == -1 else -limit))]
    need = float(liq.max()) if len(liq) else -np.inf  # old wicks inside the fake range must be taken
    fake_ext, touched = None, False
    for j in range(i + 1, min(last, i + p.setup_bars) + 1):
        if fake_ext is None:
            if (c[j] > edge) if d == -1 else (c[j] < edge):
                fake_ext = j
                continue
            if (lo[j] < leg_end) if d == -1 else (h[j] > leg_end):
                if touched:
                    return None  # "price reacts heavily from that OB": it held as a real OB, not liquidity
                leg_end = float(lo[j] if d == -1 else h[j])  # still running away before any retest: the leg extends
            touched = touched or bool((h[j] >= bot) if d == -1 else (lo[j] <= top))
            continue
        fx = float(h[fake_ext:j + 1].max() if d == -1 else lo[fake_ext:j + 1].min())
        if (fx - limit) * -d > 0:
            return None  # fake-out too big
        if (c[j] < edge) if d == -1 else (c[j] > edge):
            if (fx if d == -1 else -fx) < need:
                return None  # liquidity above the OB not cleared yet
            return {"ob_bar": T, "top": top, "bottom": bot, "edge": edge, "fake_ext": fx, "ready": j}
    return None


def flow_poi(m: Bars, ind: dict, i: int, d: int, piv: int, a: float, p: OrderFlowParams) -> dict | None:
    """Videos 1-5: most extreme open FVG of the breaking leg that reaches the discount (premium for sells)."""
    h, lo = m["high"], m["low"]
    L = piv + int(np.argmin(lo[piv:i + 1]) if d == 1 else np.argmax(h[piv:i + 1]))  # leg origin
    hi_ = float(h[L:i + 1].max() if d == 1 else lo[L:i + 1].min())  # break extreme = target
    mid = (float(lo[L] if d == 1 else h[L]) + hi_) / 2
    for z in ind["raw_fvgs"]:  # oldest first = closest to the origin = most extreme
        if z["dir"] != d or not (L <= z["formed"] - 2 and z["formed"] <= i < z["mitigated"]):
            continue
        small = z["top"] - z["bottom"] <= p.small_fvg_atr * a
        first = z["formed"] - 2  # course OB: the first candle of the 3-candle gap
        entry = (z["top"] if d == 1 else z["bottom"]) if small else float(h[first] if d == 1 else lo[first])
        if (entry - mid) * d >= 0:
            continue  # the ENTRY must be in discount (buys) / premium (sells) -- video 9: "above 50% = risky"
        return {"ob_bar": first, "entry": float(entry), "stop0": float(lo[L] if d == 1 else h[L]), "target": hi_, "ready": i,
                "small_fvg": bool(small)}
    return None


def confirmation_zone(m: Bars, ind: dict, i: int, d: int, piv: int, p: OrderFlowParams, a: float) -> dict | None:
    """JeaFx: demand = last candle before the impulse; entry just above it, stop under the confirmation swing low."""
    o, h, lo, c = m["open"], m["high"], m["low"], m["close"]
    seg = (lo[piv:i + 1] if d == 1 else -h[piv:i + 1])[::-1]
    L = i - int(np.argmin(seg))  # swing low of the confirmation; ties -> the LAST candle before the impulse
    z = L
    if (c[L] - o[L]) * d > 0.5 * (h[L] - lo[L]) and L > piv:
        z = L - 1  # the extreme candle is the impulse itself -> the base is the candle before it
    entry = (h[z] if d == 1 else lo[z]) + d * p.entry_buffer_atr * a
    stop = float(lo[L] if d == 1 else h[L])
    brk = float(h[L:i + 1].max() if d == 1 else lo[L:i + 1].min())
    cont = bool(ind["swing"]["trend"][i] == d)
    return {"ob_bar": z, "entry": float(entry), "stop0": stop, "target": brk if cont else None, "ready": i,
            "type": "continuation" if cont else "reversal"}


def liquidity_target(ind: dict, j: int, d: int, entry: float, risk: float, p: OrderFlowParams) -> tuple[float | None, int]:
    """Nearest UNSWEPT opposite pool at >= min_rr. Levels within 0.1 x ATR(200) of each other form one zone (LuxAlgo
    buyside/sellside margin); the target is the zone's near edge and the zone size (how many pools) is returned."""
    lv = sorted((lvl for _, lvl in indicators._pools(ind, j, -d, fresh=True) if (lvl - entry) * d >= p.min_rr * risk), key=lambda x: abs(x - entry))
    if not lv:
        return None, 0
    margin = 0.1 * ind["atr200"][j]
    return lv[0], sum(abs(x - lv[0]) <= margin for x in lv)


def built_liquidity(pools: dict, after: int, upto: int, entry: float, far: float) -> bool:
    """Faiz: before price returns to the POI it must engineer liquidity -- here an internal swing low (buys) / high
    (sells) that formed after the break, was confirmed by bar `upto`, and sits between the entry and the break extreme."""
    return any(piv > after and conf <= upto and min(entry, far) < lvl < max(entry, far) for conf, (piv, lvl) in pools.items())


def _setup(m: Bars, ind: dict, i: int, d: int, last: int, a: np.ndarray, p: OrderFlowParams, rt: float, htf) -> dict | None:
    it = ind["internal"]
    piv = int(it["pivot"][i])
    if piv < 0:
        return None
    if p.htf_poi and htf_gap_tapped(m, i, d, htf[0], htf[1]["t"], 3600, p.tap_bars) is None:
        return None
    buf = p.stop_buffer_atr * a[i]
    if p.mode in ("flow", "confirmation"):
        s = flow_poi(m, ind, i, d, piv, a[i], p) if p.mode == "flow" else confirmation_zone(m, ind, i, d, piv, p, a[i])
        if s is None:
            return None
        entry, stop = s["entry"], s["stop0"] - d * buf
        target = s["target"]
    else:
        s = liquidity_ob(m, i, d, piv, last, a[i], p)
        if s is None:
            return None
        entry = s["edge"]
        stop = (s["fake_ext"] if p.stop_at == "fake" else (s["top"] if d == -1 else s["bottom"])) - d * buf
        target = None
    risk = (entry - stop) * d
    if risk < p.min_stop_costs * rt:
        return None
    zone = 0
    if target is None:
        target, zone = liquidity_target(ind, s["ready"], d, entry, risk, p)
        target = entry + d * p.rr_target * risk if target is None else target
    checks = indicators.confirm(ind, m, s["ready"], d, float(entry), float(stop), p.confirm_lookback)
    if not indicators.passes(checks, p.confirm):
        return None
    checks |= {"mode": p.mode, "setup_type": s.get("type"), "target_pool_levels": zone, "break": "CHoCH" if it["kind"][i] else "BOS", "rr_planned": round((target - entry) * d / risk, 2)}
    # flow / confirmation-continuation: "target the high" = the impulse extreme made between the break and the entry
    # (as bensim.strategy's liquidity target), so it is fixed at the touch, not at the break
    ext = p.mode == "flow" or s.get("type") == "continuation"
    return {"entry": float(entry), "stop": float(stop), "target": float(target), "zone_bar": s["ob_bar"], "ready": s["ready"],
            "checks": checks, "extreme": ext, "risk": float(risk)}


def plan(m: Bars, h1: Bars, m5: Bars, symbol: str, p: OrderFlowParams = OrderFlowParams(), waiting: list | None = None) -> dict[str, Any] | None:
    """Same contract as bensim.strategy.plan: TRIGGERED only on the M5 bar that first touches the entry."""
    if len(m["t"]) < 100 or len(h1["t"]) < 10 or not len(m5["t"]):
        return None
    cur15 = (int(m5["t"][-1]) // 900) * 900
    last = int(np.searchsorted(m["t"], cur15)) - 1
    if last < 60:
        return None
    mm = {k: v[:last + 1] for k, v in m.items()}
    htf = None
    if p.htf_poi:
        hk = {k: v[:int(np.searchsorted(h1["t"] + 3600, cur15, side="right"))] for k, v in h1.items()}
        htf = (indicators.fvgs(hk), hk)
    ind = indicators.compute(mm)
    if p.mode == "flow":
        ind["raw_fvgs"] = indicators.fvgs(mm, auto=False)  # course FVG: any 3-candle gap, no size filter
    a = atr(mm)
    rt = p.cost_pips.get(symbol, 1.2) * pip(symbol)
    forming = np.flatnonzero(m5["t"] >= cur15)
    it = ind["internal"]
    tops, btms = indicators.swings(mm, 5)  # internal swings = the stop pools price builds on the pullback
    busy_until, status = -1, None
    for i in np.flatnonzero(it["event"] != 0):
        i = int(i)
        if i < 50 or i < busy_until or last - i > p.plan_bars + p.setup_bars:
            continue
        d = int(it["event"][i])
        if p.breaks != "any" and ("choch" if it["kind"][i] else "bos") != p.breaks:
            continue
        s = _setup(mm, ind, i, d, last, a, p, rt, htf)
        if s is None:
            continue
        entry, stop, target, r, ext = s["entry"], s["stop"], s["target"], s["ready"], s["extreme"]
        need = p.mode in ("flow", "confirmation") and p.need_liquidity
        liq = lambda upto, far: not need or built_liquidity(btms if d == 1 else tops, i, upto, entry, far)  # noqa: E731
        impulse = lambda upto: float(m["high"][i:upto + 1].max() if d == 1 else m["low"][i:upto + 1].min())  # noqa: E731
        rr_ok = lambda tg: (tg - entry) * d >= p.min_rr * s["risk"]  # noqa: E731
        win = slice(r + 1, min(last + 1, r + 1 + p.plan_bars))
        touch = (m["low"][win] <= entry) if d == 1 else (m["high"][win] >= entry)
        ran = np.zeros(len(touch), bool) if ext else ((m["high"][win] >= target) if d == 1 else (m["low"][win] <= target))
        if touch.any():
            k = r + 1 + int(np.argmax(touch))
            if ext:
                target = impulse(k - 1)
            if ran[:k - r - 1].any() or (m["open"][k] - stop) * d <= 0 or not liq(k - 1, target) or (ext and not rr_ok(target)):
                continue
            busy_until = _exit_bar(m, k, d, stop, target, last, p.max_hold_bars)  # the real exit, as bensim.strategy
            status = {"dir": d, "status": "CONSUMED" if busy_until <= last else "IN_TRADE", "signal_bar": i}
            if busy_until > last:
                break  # still open: one trade per symbol
            continue  # closed: later setups are live again
        if ran.any() or last - r >= p.plan_bars:
            continue
        hit5 = (m5["low"][forming] <= entry) if d == 1 else (m5["high"][forming] >= entry)
        if not len(forming) or not hit5.any():
            status = status or {"dir": d, "status": "WAITING", "signal_bar": i}
            if waiting is not None:
                waiting.append((d, entry, stop))
            continue
        first = int(np.argmax(hit5))
        through = (m5["low"][forming[first]] <= stop) if d == 1 else (m5["high"][forming[first]] >= stop)
        if ext:
            pre = m5["high"][forming[:first]] if d == 1 else m5["low"][forming[:first]]
            target = float(np.max(np.r_[impulse(last), pre]) if d == 1 else np.min(np.r_[impulse(last), pre]))
            if not rr_ok(target):
                continue
        if not liq(last, target):
            continue  # touching the POI without any liquidity built first: skip
        if first == len(forming) - 1 and not through:
            return {"dir": d, "status": "TRIGGERED", "entry": entry, "stop": stop, "target": target,
                    "signal_time": int(m["t"][i]), "zone_base_time": int(m["t"][s["zone_bar"]]), "atr": float(a[i]),
                    "checks": s["checks"]}
        busy_until, status = last + 1, {"dir": d, "status": "IN_TRADE", "signal_bar": i}
        break
    return status


def _selfcheck() -> None:
    """Sell: push up to a bullish top candle, shift down, fake-out close above it, close back below -> setup."""
    #            0     1     2     3     4     5     6     7     8     9
    o = np.array([1.00, 1.01, 1.02, 1.03, 1.02, 1.01, 1.015, 1.032, 1.035, 1.028])
    c = np.array([1.01, 1.02, 1.03, 1.035, 1.01, 1.005, 1.03, 1.037, 1.028, 1.02])
    h = np.maximum(o, c) + 0.001
    lo = np.minimum(o, c) - 0.001
    m = {"t": np.arange(10) * 900, "open": o, "high": h, "low": lo, "close": c}
    p = OrderFlowParams(max_fake_atr=1.0)
    s = liquidity_ob(m, 5, -1, 0, 9, 0.01, p)  # shift down at bar 5, push from bar 0; top candle = bar 3 (bullish)
    assert s and s["ob_bar"] == 3 and s["edge"] == h[3] and s["ready"] == 8 and s["fake_ext"] == h[7:9].max(), s
    assert liquidity_ob(m, 5, -1, 0, 9, 0.001, p) is None  # same fake-out is too big for a tiny ATR
    c2 = c.copy()
    c2[3], o[3] = 1.03, 1.035  # top candle bearish -> invalid
    assert liquidity_ob({**m, "open": o, "close": c2}, 5, -1, 0, 9, 0.01, p) is None
    pools = {12: (7, 1.02)}  # swing confirmed at bar 12, pivot bar 7, level 1.02
    assert built_liquidity(pools, 5, 12, 1.01, 1.05) and not built_liquidity(pools, 5, 11, 1.01, 1.05)
    assert not built_liquidity(pools, 8, 12, 1.01, 1.05) and not built_liquidity(pools, 5, 12, 1.025, 1.05)
    # JeaFx buy: downtrend to a low at bar 3 (small base candle), impulse up through bar 2's high at bar 6
    o2 = np.array([1.05, 1.04, 1.03, 1.021, 1.022, 1.03, 1.04])
    c2b = np.array([1.04, 1.03, 1.021, 1.022, 1.03, 1.04, 1.05])
    m2 = {"t": np.arange(7) * 900, "open": o2, "high": np.maximum(o2, c2b) + 0.001, "low": np.minimum(o2, c2b) - 0.001, "close": c2b}
    ind2 = {"swing": {"trend": np.full(7, -1)}}
    z = confirmation_zone(m2, ind2, 6, 1, 0, OrderFlowParams(entry_buffer_atr=0.0), 0.01)
    assert z["ob_bar"] == 3 and z["entry"] == m2["high"][3] and z["stop0"] == m2["low"][3] and z["type"] == "reversal", z
    print("orderflow selfcheck ok", s)


if __name__ == "__main__":
    _selfcheck()
