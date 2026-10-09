"""Phases 3-8: deterministic, causal market-structure features on a single timeframe.

Every per-bar array value at index i uses only bars <= i (known at that bar's close). Pivots are
only recorded at their confirmation bar (pivot + k). Forward-looking FVG lifecycle fields
(`fvg_lifecycle`) are research labels and are never read by the signal code.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from .data import Bars


def atr(b: Bars, n: int = 14) -> np.ndarray:
    prev = np.r_[b.c[0], b.c[:-1]]
    tr = np.maximum(b.h - b.l, np.maximum(np.abs(b.h - prev), np.abs(b.l - prev)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().to_numpy()


def efficiency_ratio(c: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(c), np.nan)
    if len(c) <= n:
        return out
    net = np.abs(c[n:] - c[:-n])
    path = pd.Series(np.abs(np.diff(c))).rolling(n).sum().to_numpy()[n - 1 :]
    out[n:] = net / np.maximum(path, 1e-12)
    return out


# ---------------------------------------------------------------- Phase 3: swings / structure


def pivots(x: np.ndarray, k: int) -> np.ndarray:
    """Pivot (fractal) indexes j: x[j] > max(x[j-k:j]) and x[j] >= max(x[j+1:j+k+1]). Known at j+k."""
    n = len(x)
    if n < 2 * k + 1:
        return np.zeros(0, dtype=np.int64)
    w = sliding_window_view(x, k)
    j = np.arange(k, n - k)
    ok = (x[j] > w[j - k].max(1)) & (x[j] >= w[j + 1].max(1))
    return j[ok]


@dataclass
class Structure:
    k: int
    trend: np.ndarray  # +1 bull / -1 bear / 0 unknown, after bar i close
    brk: np.ndarray  # break at bar i: +1 BOS up, +2 MSS up, -1 BOS down, -2 MSS down
    sh: np.ndarray  # latest confirmed swing high price as of bar i
    sl: np.ndarray
    sh_lbl: np.ndarray  # +1 HH, -1 LH (label of the latest swing high)
    sl_lbl: np.ndarray  # +1 HL, -1 LL
    ph: np.ndarray  # pivot-high bar indexes (confirmed at +k)
    pl: np.ndarray


def structure(b: Bars, k: int) -> Structure:
    n = len(b)
    ph, pl = pivots(b.h, k), pivots(-b.l, k)
    conf_h = dict(zip((ph + k).tolist(), ph.tolist()))
    conf_l = dict(zip((pl + k).tolist(), pl.tolist()))
    trend = np.zeros(n, np.int8)
    brk = np.zeros(n, np.int8)
    sh = np.full(n, np.nan)
    sl = np.full(n, np.nan)
    sh_lbl = np.zeros(n, np.int8)
    sl_lbl = np.zeros(n, np.int8)
    t = 0
    last_h = last_l = np.nan
    lh = ll = 0
    h_live = l_live = False
    h, l, c = b.h, b.l, b.c
    for i in range(n):
        j = conf_h.get(i)
        if j is not None:
            lh = 1 if not np.isnan(last_h) and h[j] > last_h else (-1 if not np.isnan(last_h) else 0)
            last_h, h_live = h[j], True
        j = conf_l.get(i)
        if j is not None:
            ll = 1 if not np.isnan(last_l) and l[j] > last_l else (-1 if not np.isnan(last_l) else 0)
            last_l, l_live = l[j], True
        if h_live and c[i] > last_h:
            brk[i] = 2 if t == -1 else 1
            t, h_live = 1, False
        elif l_live and c[i] < last_l:
            brk[i] = -2 if t == 1 else -1
            t, l_live = -1, False
        trend[i], sh[i], sl[i], sh_lbl[i], sl_lbl[i] = t, last_h, last_l, lh, ll
    return Structure(k, trend, brk, sh, sl, sh_lbl, sl_lbl, ph, pl)


# ---------------------------------------------------------------- Phase 4-5: FVG detection + lifecycle


def fvgs(b: Bars, a: np.ndarray) -> pd.DataFrame:
    """All 3-candle FVGs. Row index j = third candle; known at close of j."""
    h, l, o, c = b.h, b.l, b.o, b.c
    j = np.arange(2, len(b))
    bull = h[j - 2] < l[j]
    bear = l[j - 2] > h[j]
    rows = []
    for d, m in ((1, bull), (-1, bear)):
        jj = j[m]
        top = np.where(d == 1, l[jj], l[jj - 2])
        bot = np.where(d == 1, h[jj - 2], h[jj])
        ref_atr = a[jj - 2]
        rows.append(
            pd.DataFrame(
                {
                    "j": jj,
                    "dir": d,
                    "top": top,
                    "bottom": bot,
                    "size": top - bot,
                    "size_atr": (top - bot) / ref_atr,
                    "disp_atr": np.abs(c[jj - 1] - o[jj - 1]) / ref_atr,
                    "body_ratio": np.abs(c[jj - 1] - o[jj - 1]) / np.maximum(h[jj - 1] - l[jj - 1], 1e-12),
                    "atr": ref_atr,
                    "t_known": b.known_at[jj],
                }
            )
        )
    return pd.concat(rows).sort_values("j", kind="stable").reset_index(drop=True)


def fvg_lifecycle(b: Bars, f: pd.DataFrame, horizon: int = 200, react_atr: float = 1.0) -> pd.DataFrame:
    """Research labels (FORWARD-LOOKING, never used by signals) for every FVG, on its own timeframe.

    touch/ce/full/invalid = bars after creation until first touch / 50% / full fill / body close
    through the far edge (NaN = not within horizon). respected = after first touch price travelled
    react_atr*ATR in the FVG direction before any close through the far edge."""
    n = len(b)
    out = {k: np.full(len(f), np.nan) for k in ("touch", "ce", "full", "invalid", "max_mitig", "mfe_atr", "mae_atr")}
    out["respected"] = np.zeros(len(f), dtype=bool)
    for r, (j, d, top, bot, size, a) in enumerate(f[["j", "dir", "top", "bottom", "size", "atr"]].itertuples(index=False)):
        j = int(j)
        s, e = j + 1, min(n, j + 1 + horizon)
        if s >= e:
            continue
        hh, ll, cc = b.h[s:e], b.l[s:e], b.c[s:e]
        if d == 1:
            pen = top - ll  # penetration depth into the gap
            touch = ll <= top
            ce = ll <= (top + bot) / 2
            full = ll <= bot
            inv = cc < bot
        else:
            pen = hh - bot
            touch = hh >= bot
            ce = hh >= (top + bot) / 2
            full = hh >= top
            inv = cc > top
        for key, arr in (("touch", touch), ("ce", ce), ("full", full), ("invalid", inv)):
            if arr.any():
                out[key][r] = int(np.argmax(arr)) + 1
        out["max_mitig"][r] = float(np.clip(pen.max() / max(size, 1e-12), 0, 1))
        if touch.any():
            t0 = int(np.argmax(touch))
            entry = top if d == 1 else bot
            fav = (hh[t0:] - entry) if d == 1 else (entry - ll[t0:])
            adv = (entry - ll[t0:]) if d == 1 else (hh[t0:] - entry)
            inv_after = inv[t0:]
            stop_at = int(np.argmax(inv_after)) if inv_after.any() else len(inv_after)
            out["mfe_atr"][r] = fav[: stop_at + 1].max() / a
            out["mae_atr"][r] = adv[: stop_at + 1].max() / a
            hit = fav[:stop_at] >= react_atr * a if stop_at > 0 else np.zeros(0, bool)
            out["respected"][r] = bool(hit.any())
    return f.assign(**out)


# ---------------------------------------------------------------- Phase 6: liquidity


@dataclass
class Liquidity:
    sweep_lo: np.ndarray  # tier of sell-side liquidity swept (low pierced, close back above) at bar i, 0 none
    sweep_hi: np.ndarray
    sweep_lo_px: np.ndarray  # extreme of the sweeping bar
    sweep_hi_px: np.ndarray
    above: dict  # bar index -> sorted untaken buy-side levels above close (snapshots on request)
    below: dict


def liquidity(b: Bars, hi_levels: list, lo_levels: list, snap: set, a: np.ndarray, max_age: int = 600, eq_tol: float = 0.1) -> Liquidity:
    """Walk bars, activating levels once known (known_time <= bar open). A level pierced by a bar that
    closes back on the original side = sweep (level consumed); pierced and closed beyond = taken.
    Levels: (known_time, price, tier). Equal highs/lows (within eq_tol*ATR) get tier +1."""
    n = len(b)
    hi_levels = sorted(hi_levels)
    lo_levels = sorted(lo_levels)
    sw_lo = np.zeros(n, np.int8)
    sw_hi = np.zeros(n, np.int8)
    sw_lo_px = np.full(n, np.nan)
    sw_hi_px = np.full(n, np.nan)
    above: dict = {}
    below: dict = {}
    act_h: list = []  # [price, tier, bar_activated]
    act_l: list = []
    ph = pl = 0
    for i in range(n):
        t0 = b.t[i]
        while ph < len(hi_levels) and hi_levels[ph][0] <= t0:
            _, px, tier = hi_levels[ph]
            for lv in act_h:
                if abs(lv[0] - px) <= eq_tol * a[i]:
                    lv[1] = max(lv[1], tier) + 1
                    tier += 1
                    break
            act_h.append([px, tier, i])
            ph += 1
        while pl < len(lo_levels) and lo_levels[pl][0] <= t0:
            _, px, tier = lo_levels[pl]
            for lv in act_l:
                if abs(lv[0] - px) <= eq_tol * a[i]:
                    lv[1] = max(lv[1], tier) + 1
                    tier += 1
                    break
            act_l.append([px, tier, i])
            pl += 1
        if act_h:
            keep = []
            for lv in act_h:
                if b.h[i] > lv[0]:
                    if b.c[i] < lv[0]:
                        sw_hi[i] = max(sw_hi[i], lv[1])
                        sw_hi_px[i] = b.h[i]
                elif i - lv[2] <= max_age:
                    keep.append(lv)
            act_h = keep
        if act_l:
            keep = []
            for lv in act_l:
                if b.l[i] < lv[0]:
                    if b.c[i] > lv[0]:
                        sw_lo[i] = max(sw_lo[i], lv[1])
                        sw_lo_px[i] = b.l[i]
                elif i - lv[2] <= max_age:
                    keep.append(lv)
            act_l = keep
        if i in snap:
            above[i] = sorted(lv[0] for lv in act_h if lv[0] > b.c[i])[:4]
            below[i] = sorted((lv[0] for lv in act_l if lv[0] < b.c[i]), reverse=True)[:4]
    return Liquidity(sw_lo, sw_hi, sw_lo_px, sw_hi_px, above, below)


def pivot_levels(b: Bars, st: Structure, tier: int) -> tuple[list, list]:
    """Swing levels as (known_time, price, tier); known at the confirmation bar's close."""
    kt = b.known_at
    n = len(b)
    hi = [(int(kt[j + st.k]), float(b.h[j]), tier) for j in st.ph if j + st.k < n]
    lo = [(int(kt[j + st.k]), float(b.l[j]), tier) for j in st.pl if j + st.k < n]
    return hi, lo


def period_levels(p: Bars, tier: int) -> tuple[list, list]:
    """Previous day/week high/low: known at the period close."""
    kt = p.known_at
    return [(int(kt[i]), float(p.h[i]), tier) for i in range(len(p))], [(int(kt[i]), float(p.l[i]), tier) for i in range(len(p))]


def asia_levels(base: Bars, tier: int = 2) -> tuple[list, list]:
    """Asian session (00:00-07:00 UTC) high/low, known at 07:00 UTC."""
    day = base.t // 86400
    hour = (base.t % 86400) // 3600
    m = hour < 7
    hi, lo = [], []
    if not m.any():
        return hi, lo
    df = pd.DataFrame({"d": day[m], "h": base.h[m], "l": base.l[m]}).groupby("d").agg(h=("h", "max"), l=("l", "min"))
    for d, row in df.iterrows():
        k = int(d) * 86400 + 7 * 3600
        hi.append((k, float(row.h), tier))
        lo.append((k, float(row.l), tier))
    return hi, lo


# ---------------------------------------------------------------- Phase 7: supply / demand


def sd_zones(b: Bars, a: np.ndarray, depart_atr: float = 1.5, max_run: int = 3) -> pd.DataFrame:
    """Demand = last candle before a run of <=max_run same-direction candles whose bodies sum to
    >= depart_atr*ATR; zone = [low, body-top] of that base candle. Supply mirrored. Known at the run's
    end. violated_t = first close beyond the distal edge (forward label for state-at-T queries)."""
    o, c, h, l = b.o, b.c, b.h, b.l
    body = c - o
    n = len(b)
    rows = []
    seen: set = set()
    for i in range(1, n):
        for d in (1, -1):
            s = i
            while s > 0 and i - s < max_run and np.sign(body[s]) == d:
                s -= 1
            # bars s+1..i are the run (s is first non-run bar = base)
            if s == i or np.sign(body[s]) == d:  # no run, or run longer than max_run
                continue
            dep = d * (c[i] - o[s + 1])
            if dep < depart_atr * a[s] or (s, d) in seen:
                continue
            seen.add((s, d))
            if d == 1:
                distal, proximal = l[s], max(o[s], c[s])
            else:
                distal, proximal = h[s], min(o[s], c[s])
            rows.append((s, i, d, distal, proximal, dep / a[s], int(b.known_at[i])))
    z = pd.DataFrame(rows, columns=["base", "end", "dir", "distal", "proximal", "strength", "t_known"])
    viol = np.full(len(z), np.iinfo(np.int64).max)
    first_touch = np.full(len(z), np.iinfo(np.int64).max)
    for r, (end, d, distal, prox) in enumerate(z[["end", "dir", "distal", "proximal"]].itertuples(index=False)):
        cc, hh, ll = c[end + 1 :], h[end + 1 :], l[end + 1 :]
        v = (cc < distal) if d == 1 else (cc > distal)
        tch = (ll <= prox) if d == 1 else (hh >= prox)
        if v.any():
            viol[r] = b.known_at[end + 1 + int(np.argmax(v))]
        if tch.any():
            first_touch[r] = b.t[end + 1 + int(np.argmax(tch))]
    return z.assign(violated_t=viol, first_touch_t=first_touch)


# ---------------------------------------------------------------- Phase 8: Wyckoff regime + volatility


def regime(b: Bars, a: np.ndarray, st: Structure, n: int = 30) -> pd.DataFrame:
    """Approximate Wyckoff phase + trend/vol regime per bar (causal).

    RANGE window = last n bars with efficiency ratio < 0.3. Phase inside a range comes from the
    net move over the n bars before it: down -> ACCUMULATION, up -> DISTRIBUTION. Outside ranges:
    MARKUP/MARKDOWN from structure trend + ER >= 0.3, else TRANSITION. Events vs the prior range
    bounds: SPRING (pierce low, close inside), UPTHRUST, SOS (close above), SOW (close below)."""
    c, h, l = b.c, b.h, b.l
    er = efficiency_ratio(c, n)
    er20 = efficiency_ratio(c, 20)
    rh = pd.Series(h).rolling(n).max().to_numpy()
    rl = pd.Series(l).rolling(n).min().to_numpy()
    prior = np.full(len(c), np.nan)
    prior[2 * n :] = c[n:-n] - c[: -2 * n]
    in_range = er < 0.3
    phase = np.full(len(c), "TRANSITION", dtype=object)
    up = (st.trend == 1) & (er >= 0.3)
    dn = (st.trend == -1) & (er >= 0.3)
    phase[up] = "MARKUP"
    phase[dn] = "MARKDOWN"
    phase[in_range] = "RANGE"
    phase[in_range & (prior < -3 * a)] = "ACCUMULATION"
    phase[in_range & (prior > 3 * a)] = "DISTRIBUTION"
    prh, prl = np.r_[np.nan, rh[:-1]], np.r_[np.nan, rl[:-1]]
    prev_rng = np.r_[False, in_range[:-1]]
    event = np.full(len(c), "", dtype=object)
    event[prev_rng & (l < prl) & (c > prl)] = "SPRING"
    event[prev_rng & (h > prh) & (c < prh)] = "UPTHRUST"
    event[prev_rng & (c > prh)] = "SOS"
    event[prev_rng & (c < prl)] = "SOW"
    vol_pct = pd.Series(a).rolling(250, min_periods=50).rank(pct=True).to_numpy()
    reg = np.full(len(c), "TRANSITION", dtype=object)
    reg[(st.trend == 1) & (er20 > 0.3)] = "TREND_UP"
    reg[(st.trend == -1) & (er20 > 0.3)] = "TREND_DOWN"
    reg[er20 < 0.2] = "RANGE"
    vol = np.where(vol_pct > 0.8, "HIGH", np.where(vol_pct < 0.2, "LOW", "NORMAL"))
    # recent events (last 10 bars) as a bullish/bearish Wyckoff bias
    bull_ev = pd.Series(np.isin(event, ["SPRING", "SOS"]).astype(float)).rolling(10, min_periods=1).max().to_numpy() > 0
    bear_ev = pd.Series(np.isin(event, ["UPTHRUST", "SOW"]).astype(float)).rolling(10, min_periods=1).max().to_numpy() > 0
    wy_bias = np.where(np.isin(phase, ["ACCUMULATION", "MARKUP"]) | bull_ev, 1, 0) - np.where(np.isin(phase, ["DISTRIBUTION", "MARKDOWN"]) | bear_ev, 1, 0)
    return pd.DataFrame({"phase": phase, "event": event, "regime": reg, "vol": vol, "vol_pct": vol_pct, "er": er20, "wy_bias": wy_bias})
