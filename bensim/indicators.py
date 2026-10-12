"""SMC / ICT confirmation indicators, built the way LuxAlgo's "Smart Money Concepts" script builds them.

These are NOT entry signals. The main strategy (bensim.strategy) finds the trade; confirm() tells it which
SMC conditions agree at that moment. Everything is causal: the state at bar i uses bars <= i only.

Indicator           LuxAlgo rule (Pine source, via the ProRealCode port) used here
  swings(len)       bar i-len is a swing high when high[i-len] > highest(high, len) of the bars after it
                    (alternating top/bottom state `os`); confirmed at bar i. Swing = 50, internal = 5.
  structure         close crossing over the last swing high = bullish break; CHoCH when the trend was bearish,
                    else BOS (mirror for lows). Each swing can be broken once. Internal breaks of the same level
                    as the swing structure are skipped (LuxAlgo `itop_y != top_y`).
  order block       on each break: among the bars between the broken swing and the break bar whose range is
                    < 2 x ATR(200), the lowest-low bar (bullish) / highest-high bar (bearish); zone = its
                    low..high. Mitigated when a later close goes through the far edge.
  FVG               bullish at bar i: low[i] > high[i-2], close[i-1] > high[i-2], and bar i-1's body move in %
                    > 2 x its running mean (LuxAlgo auto threshold). Zone high[i-2]..low[i]; filled when a later
                    low goes below its bottom (mirror for bearish).
  EQH / EQL         pivots(3,3); equal when two consecutive pivot highs/lows are within 0.1 x ATR(200).
  premium/discount  trailing high/low of the swing structure (reset at each swing pivot); equilibrium = 50%.
  strong/weak high  in a bullish swing trend the trailing high is WEAK (expected to be taken = target).
Extra ICT liquidity pools (not in that LuxAlgo script): previous UTC day high/low, Asian range (00-06 UTC),
killzones London 07-10 / New York 12-15 UTC.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

Bars = dict[str, np.ndarray]
KILLZONES = ((7, 10), (12, 15))
ASIA = (0, 6)


def atr_n(b: Bars, n: int = 200) -> np.ndarray:
    prev = np.r_[b["close"][0], b["close"][:-1]]
    tr = np.maximum(b["high"] - b["low"], np.maximum(np.abs(b["high"] - prev), np.abs(b["low"] - prev)))
    return pd.Series(tr).rolling(n, min_periods=1).mean().to_numpy()  # Pine ta.atr is RMA; SMA warms up faster


def swings(b: Bars, length: int) -> tuple[dict[int, tuple[int, float]], dict[int, tuple[int, float]]]:
    """{confirm bar: (pivot bar, level)} for swing highs and lows (LuxAlgo `swings(len)`)."""
    h, l = b["high"], b["low"]
    up = pd.Series(h).rolling(length).max().to_numpy()
    dn = pd.Series(l).rolling(length).min().to_numpy()
    tops, btms, os_prev = {}, {}, 0
    for i in range(length, len(h)):
        os = 0 if h[i - length] > up[i] else (1 if l[i - length] < dn[i] else os_prev)
        if os == 0 and os_prev != 0:
            tops[i] = (i - length, float(h[i - length]))
        if os == 1 and os_prev != 1:
            btms[i] = (i - length, float(l[i - length]))
        os_prev = os
    return tops, btms


def structure(b: Bars, length: int, a200: np.ndarray, skip_levels: dict | None = None) -> dict[str, Any]:
    """BOS/CHoCH + order blocks + trailing extremes for one structure layer."""
    h, l, c = b["high"], b["low"], b["close"]
    n = len(c)
    tops, btms = swings(b, length)
    trend, top, btm = 0, None, None
    ev, kind, tr = np.zeros(n, int), np.zeros(n, int), np.zeros(n, int)  # kind: 1 = CHoCH, 0 = BOS
    piv = np.full(n, -1)  # bar of the swing each break broke
    top_lvl, btm_lvl = np.full(n, np.nan), np.full(n, np.nan)
    t_up, t_dn = np.full(n, np.nan), np.full(n, np.nan)
    up, dn = h[0], l[0]
    obs = []
    for i in range(n):
        if i in tops:
            top = [*tops[i], True]
            up = tops[i][1]
        if i in btms:
            btm = [*btms[i], True]
            dn = btms[i][1]
        up, dn = max(up, h[i]), min(dn, l[i])
        t_up[i], t_dn[i] = up, dn
        for d, s in ((1, top), (-1, btm)):
            if s is None or not s[2] or i == 0:
                continue
            lvl = s[1]
            if skip_levels is not None and lvl == skip_levels[d][i]:
                continue
            if (c[i] > lvl >= c[i - 1]) if d == 1 else (c[i] < lvl <= c[i - 1]):
                ev[i], kind[i], piv[i] = d, int(trend == -d), s[0]
                s[2], trend = False, d
                ob = order_block(b, s[0], i, d, a200)
                if ob:
                    obs.append(ob)
        tr[i] = trend
        top_lvl[i] = top[1] if top else np.nan
        btm_lvl[i] = btm[1] if btm else np.nan
    return {"event": ev, "kind": kind, "pivot": piv, "trend": tr, "top": top_lvl, "btm": btm_lvl, "trail_up": t_up, "trail_dn": t_dn, "obs": obs}


def order_block(b: Bars, pivot: int, brk: int, d: int, a200: np.ndarray) -> dict | None:
    """LuxAlgo ob_coord: the extreme bar between the swing and the break, skipping bars wider than 2 x ATR(200)."""
    h, l, c = b["high"], b["low"], b["close"]
    best = None
    for j in range(brk - 1, pivot, -1):  # newest first; ties go to the older bar, as in the Pine loop
        if h[j] - l[j] >= 2 * a200[j]:
            continue
        if best is None or ((l[j] <= l[best]) if d == 1 else (h[j] >= h[best])):
            best = j
    if best is None:
        return None
    top, bot = float(h[best]), float(l[best])
    after = c[brk + 1:]
    hit = np.flatnonzero(after < bot) if d == 1 else np.flatnonzero(after > top)
    return {"dir": d, "bar": best, "formed": brk, "top": top, "bottom": bot, "mitigated": brk + 1 + int(hit[0]) if len(hit) else len(c)}


def fvgs(b: Bars, auto: bool = True) -> list[dict]:
    """LuxAlgo FVGs with the auto threshold (2 x running mean of |bar i-1 body move %|); auto=False = every plain
    3-candle gap (the course definition: any gap between candle 1 and candle 3 -- no size or middle-close rule)."""
    o, h, l, c = b["open"], b["high"], b["low"], b["close"]
    n = len(c)
    delta = np.r_[0.0, (c[:-1] - o[:-1]) / o[:-1] * 100]  # bar i-1's move, seen at bar i
    thr = np.cumsum(np.abs(delta)) / np.arange(1, n + 1) * 2 if auto else np.full(n, -np.inf)
    out = []
    for i in range(2, n):
        for d in (1, -1):
            if d == 1 and not (l[i] > h[i - 2] and (c[i - 1] > h[i - 2] or not auto) and delta[i] > thr[i]):
                continue
            if d == -1 and not (h[i] < l[i - 2] and (c[i - 1] < l[i - 2] or not auto) and -delta[i] > thr[i]):
                continue
            top, bot = (float(l[i]), float(h[i - 2])) if d == 1 else (float(l[i - 2]), float(h[i]))
            hit = np.flatnonzero(l[i + 1:] < bot) if d == 1 else np.flatnonzero(h[i + 1:] > top)
            out.append({"dir": d, "formed": i, "top": top, "bottom": bot, "mitigated": i + 1 + int(hit[0]) if len(hit) else n})
    return out


def equal_levels(b: Bars, a200: np.ndarray, length: int = 3, thr: float = 0.1) -> list[dict]:
    """EQH (dir=+1, buy-side liquidity) / EQL (dir=-1, sell-side) from consecutive pivots(length, length)."""
    h, l = b["high"], b["low"]
    n = len(h)
    out, prev = [], {1: None, -1: None}
    for j in range(length, n - length):
        conf = j + length
        for d, x in ((1, h), (-1, l)):
            left, right = x[j - length:j], x[j + 1:j + length + 1]
            piv = (x[j] > left.max() and x[j] >= right.max()) if d == 1 else (x[j] < left.min() and x[j] <= right.min())
            if not piv:
                continue
            p = prev[d]
            if p is not None and abs(x[j] - p) < thr * a200[conf]:
                lvl = float(max(x[j], p) if d == 1 else min(x[j], p))
                taken = np.flatnonzero(h[conf + 1:] > lvl) if d == 1 else np.flatnonzero(l[conf + 1:] < lvl)
                out.append({"dir": d, "confirmed": conf, "level": lvl, "taken": conf + 1 + int(taken[0]) if len(taken) else n})
            prev[d] = float(x[j])
    return out


def day_levels(b: Bars) -> dict[str, np.ndarray]:
    """Previous UTC day high/low and the latest finished Asian range (00-06 UTC), per bar."""
    t = b["t"]
    day = t // 86400
    hour = (t % 86400) // 3600
    df = pd.DataFrame({"day": day, "h": b["high"], "l": b["low"], "asia": (hour >= ASIA[0]) & (hour < ASIA[1])})
    dh, dl = df.groupby("day").h.max(), df.groupby("day").l.min()
    ah, al = df[df.asia].groupby("day").h.max(), df[df.asia].groupby("day").l.min()
    pdh = pd.Series(day).map(dh.shift(1)).to_numpy(float)
    pdl = pd.Series(day).map(dl.shift(1)).to_numpy(float)
    done = hour >= ASIA[1]  # today's Asian range only once it is over
    asia_hi = np.where(done, pd.Series(day).map(ah).to_numpy(float), np.nan)
    asia_lo = np.where(done, pd.Series(day).map(al).to_numpy(float), np.nan)
    idx = pd.Series(np.arange(len(t)))
    day_start = idx.groupby(day).transform("min").to_numpy()
    asia_start = pd.Series(np.where(done, idx, np.nan)).groupby(day).transform("min").to_numpy()
    return {"pdh": pdh, "pdl": pdl, "asia_hi": asia_hi, "asia_lo": asia_lo, "hour": hour,
            "day_start": day_start, "asia_start": asia_start}  # first bar each level is valid from (for fresh pools)


def compute(b: Bars, swing_len: int = 50, internal_len: int = 5) -> dict[str, Any]:
    a200 = atr_n(b, 200)
    sw = structure(b, swing_len, a200)
    it = structure(b, internal_len, a200, skip_levels={1: sw["top"], -1: sw["btm"]})
    ar = np.arange(len(b["close"]))
    for k in ("top", "btm"):  # bar each swing level started (for fresh pools)
        x = sw[k]
        sw[k + "_since"] = np.maximum.accumulate(np.where(np.r_[True, x[1:] != x[:-1]], ar, 0))
    return {"atr200": a200, "swing": sw, "internal": it, "fvgs": fvgs(b), "eq": equal_levels(b, a200), **day_levels(b),
            "high": b["high"], "low": b["low"]}


# ---------------------------------------------------------------- confirmations for the main strategy

def _active(zones: list[dict], i: int, d: int, price: float) -> bool:
    return any(z["dir"] == d and z["formed"] <= i < z["mitigated"] and z["bottom"] <= price <= z["top"] for z in zones)


def _pools(ind: dict, j: int, d: int, fresh: bool = False) -> list[tuple[str, float]]:
    """Liquidity on the side a long (d=1) wants swept: sell-side pools below; mirror for shorts.
    fresh=True drops previous-day / Asian / swing levels that price already traded through before bar j (LuxAlgo
    removes swept levels); the default keeps the original behaviour the live strategy was validated with."""
    side = -d
    pools = [("eq", e["level"]) for e in ind["eq"] if e["dir"] == side and e["confirmed"] < j <= e["taken"]]
    pools += [(n, float(ind[k][j]), s) for n, k, s in (("pd", "pdl" if d == 1 else "pdh", "day_start"),
                                                       ("asia", "asia_lo" if d == 1 else "asia_hi", "asia_start"))
              if np.isfinite(ind[k][j])]
    lvl = ind["swing"]["btm" if d == 1 else "top"][j - 1] if j else np.nan
    if np.isfinite(lvl):
        pools.append(("swing", float(lvl), "swing"))
    out = []
    for name, lvl, *since in pools:
        if fresh and since:
            k = since[0]
            start = int(ind["swing"][("btm" if d == 1 else "top") + "_since"][j - 1]) if k == "swing" else ind[k][j]
            if np.isfinite(start):
                seg = ind["low"][int(start):j] if d == 1 else ind["high"][int(start):j]
                if ((seg < lvl) if d == 1 else (seg > lvl)).any():
                    continue  # already swept: no stops left there
        out.append((name, lvl))
    return out


def confirm(ind: dict, b: Bars, i: int, d: int, entry: float, stop: float, lookback: int = 20) -> dict[str, Any]:
    """Which SMC confirmations agree with a direction-d trade planned at bar i (entry/stop prices)."""
    sw, it = ind["swing"], ind["internal"]
    s = slice(max(0, i - lookback), i + 1)
    ib = np.flatnonzero(it["event"][s] == d)
    sweep = None
    for j in range(i, max(0, i - lookback) - 1, -1):  # newest sweep: wick through a pool, close back inside
        for name, lvl in _pools(ind, j, d):
            if (b["low"][j] < lvl < b["close"][j]) if d == 1 else (b["high"][j] > lvl > b["close"][j]):
                sweep = name
                break
        if sweep:
            break
    eq_mid = (sw["trail_up"][i] + sw["trail_dn"][i]) / 2
    risk = abs(entry - stop)
    weak = sw["trail_up"][i] if d == 1 else sw["trail_dn"][i]  # weak high/low when the swing trend agrees
    bsl = [p for _, p in _pools(ind, i, -d) if (p - entry) * d > 0]  # opposite-side pools ahead = draw on liquidity
    near = min(bsl, key=lambda p: abs(p - entry)) if bsl else None
    return {
        "swing_trend": bool(sw["trend"][i] == d),
        "internal_break": ("CHoCH" if it["kind"][s][ib[-1]] else "BOS") if len(ib) else "none",
        "fvg": _active(ind["fvgs"], i, d, entry),
        "ob": _active(sw["obs"] + it["obs"], i, d, entry),
        "discount": bool((entry - eq_mid) * d < 0),
        "sweep": sweep or "none",
        "killzone": any(a <= ind["hour"][i] < z for a, z in KILLZONES),
        "weak_extreme_r": round(float((weak - entry) * d / risk), 2) if sw["trend"][i] == d and risk else None,
        "liq_ahead_r": round(float((near - entry) * d / risk), 2) if near is not None and risk else None,
    }


def passes(chk: dict[str, Any], need: tuple) -> bool:
    """`need` names: swing_trend, internal_break, choch, fvg, ob, poi (= fvg or ob), discount, sweep, killzone."""
    test = {"swing_trend": chk["swing_trend"], "internal_break": chk["internal_break"] != "none",
            "choch": chk["internal_break"] == "CHoCH", "fvg": chk["fvg"], "ob": chk["ob"], "poi": chk["fvg"] or chk["ob"],
            "discount": chk["discount"], "sweep": chk["sweep"] != "none", "killzone": chk["killzone"]}
    return all(test[k] for k in need)


def _selfcheck() -> None:
    rng = np.random.default_rng(1)
    n = 600
    c = 1.1 + np.cumsum(rng.normal(0, 0.0006, n))
    o = np.r_[c[0], c[:-1]]
    h, l = np.maximum(o, c) + 0.0003, np.minimum(o, c) - 0.0003
    b = {"t": 1_780_000_000 + np.arange(n) * 900, "open": o, "high": h, "low": l, "close": c}
    ind = compute(b)
    sw, it = ind["swing"], ind["internal"]
    assert (sw["event"] != 0).sum() < (it["event"] != 0).sum(), "internal structure must break more often than swing"
    for z in ind["fvgs"]:
        i = z["formed"]
        assert z["top"] > z["bottom"] and (b["low"][i] > b["high"][i - 2] if z["dir"] == 1 else b["high"][i] < b["low"][i - 2])
    for ob in sw["obs"] + it["obs"]:
        assert ob["bar"] < ob["formed"] and ob["top"] >= ob["bottom"]
    assert np.all(sw["trail_up"] >= sw["trail_dn"])
    # causality: truncating the future must not change the state at bar 400
    cut = {k: v[:401] for k, v in b.items()}
    ind2 = compute(cut)
    assert ind2["swing"]["trend"][400] == sw["trend"][400] and ind2["internal"]["event"][400] == it["event"][400]
    i = 450
    chk = confirm(ind, b, i, 1, float(c[i] - 0.001), float(c[i] - 0.003))
    assert set(chk) >= {"fvg", "ob", "discount", "sweep", "killzone"} and passes(chk, ())
    # fresh pools: previous-day low 1.0 valid from bar 2; bar 3 trades below it -> swept, gone for fresh=True at bar 5
    nan5 = np.full(6, np.nan)
    fake = {"eq": [], "pdl": np.full(6, 1.0), "pdh": nan5, "asia_lo": nan5, "asia_hi": nan5, "day_start": np.full(6, 2),
            "asia_start": nan5, "swing": {"btm": nan5, "top": nan5}, "low": np.array([1.1, 1.1, 1.05, 0.99, 1.05, 1.05]),
            "high": np.full(6, 1.2)}
    assert _pools(fake, 5, 1) == [("pd", 1.0)] and _pools(fake, 5, 1, fresh=True) == [] and _pools(fake, 3, 1, fresh=True) == [("pd", 1.0)]
    print("indicators ok:", (sw["event"] != 0).sum(), "swing breaks,", (it["event"] != 0).sum(), "internal,",
          len(ind["fvgs"]), "FVGs,", len(sw["obs"]) + len(it["obs"]), "OBs,", len(ind["eq"]), "EQH/EQL; sample", chk)


if __name__ == "__main__":
    _selfcheck()
