"""Justin Bennett's SMC building blocks, written from his own definitions (14 videos in D:/Downloads/SMC new +
the dailypriceaction.com SMC article; notes in docs/justin_bennett_smc.md). Self-contained: no LuxAlgo port, nothing
from the archived system. Every function reads only bars <= the bar it reports on (no lookahead).

  market_structure  BOS / CHoCH (= MSS) by CLOSE beyond the level (wicks never count); only the PROTECTED level can
                    flip the trend; protected low = the lowest low between the broken high and the BOS candle (his
                    "box"); HH / HL / LH / LL labels; everything between external high and protected low is internal
  fvgs              3-candle gap between candle 1 and candle 3, valid only once candle 3 has CLOSED; the imbalance
                    (supply/demand) is the cause, the gap the effect; one-time use (touched = used); HTF gaps tend to
                    fill COMPLETELY to the candle-1 extreme ("filled")
  ote_band / fib_pos  his fib 0 / 0.5 / 0.62 / 0.79 / 1 from the displacement start to the external extreme;
                    premium/discount at 50%, OTE 62-79%
  swing_points      the visible highs/lows he draws: a high above the n highs on each side (confirmed n bars later)
  equal_levels      consecutive swing highs (lows) within tol = equal highs (lows) = liquidity still building
  swept             a wick through a level with NO close beyond it (a close beyond = taken / broken, not a sweep)
  took_internal     the run into an external high took out an internal swing high first ("textbook" external high)
  sweep_reversal    his 3 steps: established low -> wick below, no close below -> close above the high that started
                    the sweep leg (no CHoCH needed)
  pools             liquidity a trade can target: the leg's external extreme (T1), untaken equal lows/highs and older
                    external lows/highs beyond it (T2)
"""
from __future__ import annotations

from typing import Any

import numpy as np

Bars = dict[str, np.ndarray]


def atr(b: Bars, n: int = 14) -> np.ndarray:
    """Plain average true range (simple mean of the last n true ranges)."""
    h, lo, c = b["high"], b["low"], b["close"]
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev), np.abs(lo - prev)))
    out = np.cumsum(tr)
    out[n:] = out[n:] - out[:-n]
    return out / np.minimum(np.arange(1, len(tr) + 1), n)


def market_structure(b: Bars, min_break: np.ndarray | None = None, seed: int = 10, swing: int = 2) -> dict[str, Any]:
    """His structure. Uptrend: a BOS is a close above an EXTERNAL high -- a high CONFIRMED by a pullback ("once we get a
    decent little pullback I mark this high"; here: a swing high with `swing` lower highs on each side, printed after
    the last break). New highs without a pullback just extend the leg -- "this is all internal". On each BOS the
    protected low = the lowest low between that external high and the BOS bar (his box). A CHoCH (MSS) is a close
    below the protected low; the new downtrend's protected high = the up-leg's top. Mirror for downtrends.
    Returns per bar: trend (+1/-1/0), prot (protected level), ext (running extreme of the current leg),
    events [(bar, "BOS"|"CHoCH", dir, level, level_bar)], labels [(bar, "HH"|"HL"|"LH"|"LL", price)]."""
    h, lo, c = b["high"], b["low"], b["close"]
    n = len(c)
    mb = np.zeros(n) if min_break is None else min_break
    trend = np.zeros(n, int)
    prot, ext = np.full(n, np.nan), np.full(n, np.nan)
    events, labels = [], []
    if n <= max(seed, 2 * swing + 1):
        return {"trend": trend, "prot": prot, "ext": ext, "events": events, "labels": labels}
    d, last = 0, 0
    run_hi, run_lo = int(np.argmax(h[:seed])), int(np.argmin(lo[:seed]))  # running extremes of the current leg
    sw_hi = sw_lo = None  # confirmed external high / low (by a pullback) since the last break
    p_lo = p_hi = None  # protected low (uptrend) / high (downtrend)
    for i in range(seed, n):
        j = i - swing  # pivot that the last `swing` bars can confirm now
        if j - swing >= 0 and j > last:
            if h[j] > h[j - swing:j].max() and h[j] >= h[j + 1:i + 1].max() and (sw_hi is None or h[j] > h[sw_hi]):
                sw_hi = j
            if lo[j] < lo[j - swing:j].min() and lo[j] <= lo[j + 1:i + 1].min() and (sw_lo is None or lo[j] < lo[sw_lo]):
                sw_lo = j
        if d >= 0 and sw_hi is not None and c[i] - mb[i] > h[sw_hi]:  # close above a confirmed external high
            kind = "BOS" if d == 1 else "CHoCH"
            p = sw_hi + int(np.argmin(lo[sw_hi:i + 1]))  # the box
            events.append((i, kind, 1, float(h[sw_hi]), sw_hi))
            labels += [(p, "HL", float(lo[p])), (sw_hi, "HH" if d == 1 else "LH", float(h[sw_hi]))]
            d, p_lo, run_hi, last, sw_hi, sw_lo = 1, p, i, i, None, None
        elif d <= 0 and sw_lo is not None and c[i] + mb[i] < lo[sw_lo]:  # close below a confirmed external low
            kind = "BOS" if d == -1 else "CHoCH"
            p = sw_lo + int(np.argmax(h[sw_lo:i + 1]))
            events.append((i, kind, -1, float(lo[sw_lo]), sw_lo))
            labels += [(p, "LH", float(h[p])), (sw_lo, "LL" if d == -1 else "HL", float(lo[sw_lo]))]
            d, p_hi, run_lo, last, sw_hi, sw_lo = -1, p, i, i, None, None
        elif d == 1 and c[i] + mb[i] < lo[p_lo]:  # close below the PROTECTED low = CHoCH (MSS) down
            events.append((i, "CHoCH", -1, float(lo[p_lo]), p_lo))
            labels += [(p_lo, "HL", float(lo[p_lo])), (run_hi, "HH", float(h[run_hi]))]  # the up-leg's top is external
            d, p_hi, run_lo, last, sw_hi, sw_lo = -1, run_hi, i, i, None, None  # protected high = the up-leg's top
        elif d == -1 and c[i] - mb[i] > h[p_hi]:  # close above the PROTECTED high = CHoCH (MSS) up
            events.append((i, "CHoCH", 1, float(h[p_hi]), p_hi))
            labels += [(p_hi, "LH", float(h[p_hi])), (run_lo, "LL", float(lo[run_lo]))]  # the down-leg's low is external
            d, p_lo, run_hi, last, sw_hi, sw_lo = 1, run_lo, i, i, None, None
        if h[i] > h[run_hi]:
            run_hi = i
        if lo[i] < lo[run_lo]:
            run_lo = i
        trend[i] = d
        prot[i] = lo[p_lo] if d == 1 else h[p_hi] if d == -1 else np.nan
        ext[i] = h[run_hi] if d == 1 else lo[run_lo] if d == -1 else np.nan
    return {"trend": trend, "prot": prot, "ext": ext, "events": events, "labels": labels}


def fvgs(b: Bars) -> list[dict]:
    """Every 3-candle gap. Bullish: low[c3] > high[c1], zone high[c1]..low[c3]; bearish mirror.
    formed = candle 3 (usable from its close); touched = first later bar trading into the gap (one-time use);
    filled = first later bar reaching the far edge (the candle-1 extreme)."""
    h, lo = b["high"], b["low"]
    n = len(h)
    out = []
    for i in range(2, n):
        for d in (1, -1):
            if d == 1 and not lo[i] > h[i - 2]:
                continue
            if d == -1 and not h[i] < lo[i - 2]:
                continue
            bot, top = (float(h[i - 2]), float(lo[i])) if d == 1 else (float(h[i]), float(lo[i - 2]))
            later_lo, later_hi = lo[i + 1:], h[i + 1:]
            t = np.flatnonzero(later_lo <= top) if d == 1 else np.flatnonzero(later_hi >= bot)
            f = np.flatnonzero(later_lo <= bot) if d == 1 else np.flatnonzero(later_hi >= top)
            out.append({"dir": d, "c1": i - 2, "formed": i, "bottom": bot, "top": top,
                        "extreme": bot if d == 1 else top,
                        "touched": i + 1 + int(t[0]) if len(t) else n, "filled": i + 1 + int(f[0]) if len(f) else n})
    return out


def ote_band(start: float, ext: float, d: int, levels: tuple = (0.62, 0.79)) -> tuple[float, float]:
    """OTE prices of a leg: sell (d=-1) leg from start (high) down to ext (low) -> the 62-79% retracement band."""
    a, b = (ext + f * (start - ext) for f in levels)  # retrace from the external extreme back toward the start
    return min(a, b), max(a, b)


def fib_pos(price: float, start: float, ext: float) -> float:
    """0 = the external extreme, 0.5 = equilibrium, 1 = the displacement start (premium for sells above 0.5)."""
    return (price - ext) / (start - ext) if start != ext else 0.0


def swing_points(b: Bars, n: int = 2) -> tuple[list[tuple[int, int, float]], list[tuple[int, int, float]]]:
    """Visible swing highs/lows: (bar, confirmed_at, price) where the high is above the n highs on each side."""
    h, lo = b["high"], b["low"]
    highs, lows = [], []
    for i in range(n, len(h) - n):
        if h[i] > h[i - n:i].max() and h[i] >= h[i + 1:i + n + 1].max():
            highs.append((i, i + n, float(h[i])))
        if lo[i] < lo[i - n:i].min() and lo[i] <= lo[i + 1:i + n + 1].min():
            lows.append((i, i + n, float(lo[i])))
    return highs, lows


def equal_levels(points: list[tuple[int, int, float]], tol: float, high: bool) -> list[dict]:
    """Consecutive swing highs (lows) within tol = equal highs (lows): liquidity resting beyond the extreme of the two."""
    out = []
    for (b1, _, p1), (b2, c2, p2) in zip(points, points[1:]):
        if abs(p1 - p2) <= tol:
            out.append({"bars": (b1, b2), "confirmed": c2, "level": max(p1, p2) if high else min(p1, p2), "high": high})
    return out


def swept(b: Bars, i: int, level: float, d: int) -> bool:
    """Bar i swept a level: d=+1 sweeps sell-side liquidity below (wick under, close back above), d=-1 buy-side."""
    return bool(b["low"][i] < level <= b["close"][i]) if d == 1 else bool(b["high"][i] > level >= b["close"][i])


def took_internal(b: Bars, ext_bar: int, d: int, n: int = 2, look: int = 48, near: int = 3) -> bool:
    """The external high (d=-1, a sell leg's start) / low (d=+1) was made by a run that took out an internal swing
    high (low) within `near` bars of the extreme -- the internal liquidity was swept first."""
    highs, lows = swing_points({k: v[:ext_bar + 1] for k, v in b.items()}, n)
    x = b["high"] if d == -1 else -b["low"]
    for pb, conf, p in (highs if d == -1 else lows):
        lv = p if d == -1 else -p
        if pb >= ext_bar or pb < ext_bar - look or conf > ext_bar or lv >= x[ext_bar]:
            continue
        took = np.flatnonzero(x[pb + 1:ext_bar + 1] > lv)
        if len(took) and pb + 1 + took[0] >= ext_bar - near:
            return True
    return False


def sweep_reversal(b: Bars, d: int, from_i: int, n: int = 2, mb: np.ndarray | None = None) -> float | None:
    """His liquidity-sweep reversal firing on the LAST bar. d=-1 (sell): a swing high is wicked above with no close
    above (step 2), then the last bar closes below the low the sweep leg started from (step 3). Returns the sweep
    extreme (for the stop) or None."""
    h, lo, c = b["high"], b["low"], b["close"]
    j = len(c) - 1
    mb = np.zeros(len(c)) if mb is None else mb
    highs, lows = swing_points({k: v[:j] for k, v in b.items()}, n)
    pts = highs if d == -1 else lows
    for k in range(j - 1, max(from_i, 1) - 1, -1):  # newest sweep first
        for pb, conf, lvl in pts:
            if conf >= k or pb < from_i - 3 * n:
                continue
            if not swept(b, k, lvl, -d):
                continue
            beyond = (c[k + 1:j + 1] > lvl) if d == -1 else (c[k + 1:j + 1] < lvl)
            if beyond.any():
                continue  # accepted beyond: taken, not swept
            trig = float(lo[pb:k + 1].min() if d == -1 else h[pb:k + 1].max())  # where the sweep leg started
            ext = float(h[k:j + 1].max() if d == -1 else lo[k:j + 1].min())
            fire = (c[j] + mb[j] < trig <= c[j - 1] + mb[j - 1]) if d == -1 else (c[j] - mb[j] > trig >= c[j - 1] - mb[j - 1])
            return ext if fire else None
    return None


def pools(b: Bars, ms: dict, d: int, upto: int, eq_tol: float) -> list[float]:
    """Liquidity a direction-d trade can target (sell: below price): older external lows from the structure labels
    and equal lows, both still untaken (no low below them since they formed) at bar `upto`."""
    lo, h = b["low"], b["high"]
    x = lo if d == -1 else -h
    out = []
    for bar, lab, price in ms["labels"]:
        if bar >= upto or lab not in (("LL", "HL") if d == -1 else ("HH", "LH")):
            continue
        p = price if d == -1 else -price
        if not (x[bar + 1:upto + 1] < p).any():
            out.append(price)
    highs, lows = swing_points({k: v[:upto + 1] for k, v in b.items()}, 2)
    for e in equal_levels(lows if d == -1 else highs, eq_tol, high=d == 1):
        p = e["level"] if d == -1 else -e["level"]
        if e["confirmed"] <= upto and not (x[e["bars"][1] + 1:upto + 1] < p).any():
            out.append(e["level"])
    return out


def _selfcheck() -> None:
    # structure: up-leg BOSes, internal dips that don't close below the protected low change nothing, then a close
    # below it = CHoCH down with the up-leg's top as protected high; the next BOS moves it to the box high
    # up-leg with pullbacks: swing high 1.044 (bar 2, confirmed bar 4) broken at bar 5; swing high 1.074 (bar 6) broken
    # at bar 9 -> protected low = box low 1.046 (bar 8); top 1.104 (bar 10); bar 14 dips but closes 1.05 (internal);
    # bar 15 closes 1.03 < 1.046 = CHoCH down, protected high = the top 1.104; bar 17's new low is NOT a BOS (no
    # pullback-confirmed swing low yet)
    px = np.array([1.00, 1.02, 1.04, 1.03, 1.02, 1.05, 1.07, 1.06, 1.05, 1.08, 1.10, 1.09, 1.08, 1.07, 1.05, 1.03, 1.04, 1.02])
    b = {"t": np.arange(len(px)) * 3600, "open": px, "high": px + 0.004, "low": px - 0.004, "close": px}
    ms = market_structure(b, None, seed=3, swing=2)
    ev = [(e[0], e[1], e[2]) for e in ms["events"]]
    assert ev == [(5, "CHoCH", 1), (9, "BOS", 1), (15, "CHoCH", -1)], ev
    assert abs(ms["prot"][14] - 1.046) < 1e-9 and ms["trend"][14] == 1
    assert ms["trend"][-1] == -1 and abs(ms["prot"][-1] - 1.104) < 1e-9
    # a new high WITHOUT a pullback is not a BOS (bars 9-10 rise straight up: only one BOS at 9, none at 10)
    assert not any(e[0] == 10 for e in ms["events"])
    # a wick alone never breaks: bar 15 only wicks below the protected low and closes above it
    b2 = {**b, "low": b["low"].copy(), "close": b["close"].copy()}
    b2["close"][15:], b2["low"][15] = 1.07, 1.03
    assert market_structure(b2, None, seed=3, swing=2)["trend"][15] == 1
    # FVG: bullish gap candle-1 high 1.010, candle-3 low 1.020; touched at bar 5 (low 1.018), filled at bar 6 (1.009)
    g = {"high": np.array([1.010, 1.030, 1.035, 1.034, 1.033, 1.025, 1.015]), "low": np.array([1.000, 1.012, 1.020, 1.024, 1.026, 1.018, 1.009])}
    z = [x for x in fvgs(g) if x["dir"] == 1][0]
    assert (z["bottom"], z["top"], z["formed"], z["touched"], z["filled"], z["extreme"]) == (1.010, 1.020, 2, 5, 6, 1.010), z
    # OTE / fib
    lo_, hi_ = ote_band(1.10, 1.00, -1)
    assert abs(lo_ - 1.062) < 1e-9 and abs(hi_ - 1.079) < 1e-9 and abs(fib_pos(1.05, 1.10, 1.00) - 0.5) < 1e-12
    lo2, hi2 = ote_band(1.00, 1.10, 1)
    assert abs(lo2 - 1.021) < 1e-9 and abs(hi2 - 1.038) < 1e-9
    # equal lows half a pip apart = liquidity; a sweep = wick through + close back
    eq = equal_levels([(3, 5, 1.15759), (9, 11, 1.15762), (20, 22, 1.1500)], 0.0001, high=False)
    assert len(eq) == 1 and eq[0]["level"] == 1.15759
    sb = {"low": np.array([1.150, 1.149]), "high": np.array([1.152, 1.152]), "close": np.array([1.151, 1.148])}
    assert swept(sb, 0, 1.1505, 1) and not swept(sb, 1, 1.1495, 1)
    print("bennett_smc selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
