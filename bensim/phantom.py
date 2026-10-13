"""Phantom Trading (1.0 + 2.0 core lessons) -- setup database. Rules: docs/phantom_trading.md (section numbers in [ ]).

For every 4H order-block zone: its quality features, how price returned into it, and the outcome of two entries
-- CONFIRMATION (tap -> 15M break of structure -> limit at the newest 15M order block) [4] and RISK (limit on the zone
itself) [4] -- with several exits (fixed R, previous high = liquidity [2], 1H structure trailing [5]), net of costs.

No lookahead: structure events are causal (a swing needs later bars to be confirmed, a break needs a close), every
feature uses only bars closed before the moment it describes, fills/exits are simulated on M5 bars after the order.

    python -m bensim.phantom --start 2023-10-01 --end 2026-10-12 --out data/phantom_setups.csv [--symbols ...]
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import bennett_smc as smc
from . import bennett_strategy as bs
from .backtest import Exec, _spread, _usd_per_unit_move, load_m5, resample
from .strategy import pip

Bars = dict[str, np.ndarray]
H4, M15, H1, M5 = 14400, 900, 3600, 300
FULL_BODY = 0.6  # "strong full-bodied candle" [2]: body >= 60% of the range
RETURN_WINDOW_DAYS = 30  # a zone not revisited within this long is dropped
CONFIRM_H = 12  # hours after the tap to wait for the lower-TF break of structure [4]
FILL_H = 6  # hours to wait for the limit at the new lower-TF order block to fill
HOLD_DAYS_FIXED, HOLD_DAYS_TRAIL = 10, 30


def _structure(b: Bars) -> dict:
    """Type-2 structure [1, 4]: swings mapped on wicks, a break = a body CLOSE beyond (min_break 0)."""
    return smc.market_structure(b, None, seed=10)


def order_block(b: Bars, origin: int, brk: int, d: int) -> tuple[int, float, float] | None:
    """[2, 6] The last opposite candle at/before the origin of the leg that broke structure, REFINED forward to the
    candle right before the first full-bodied candle that engulfs it in the trade direction ("price doesn't know
    time"); zone = that candle's range plus the next candle's wick if it extends the stop side [6]."""
    o, h, lo, c = b["open"], b["high"], b["low"], b["close"]
    k = None
    for j in range(origin, max(origin - 6, -1), -1):  # the opposite candle that originated the move
        if (c[j] < o[j]) if d == 1 else (c[j] > o[j]):
            k = j
            break
    if k is None:
        return None
    j = k
    while j + 1 < brk:
        n = j + 1
        rng, body = h[n] - lo[n], abs(c[n] - o[n])
        strong = rng > 0 and body / rng >= FULL_BODY and ((c[n] > o[n]) if d == 1 else (c[n] < o[n]))
        if strong and ((c[n] > h[j]) if d == 1 else (c[n] < lo[j])):
            break
        j = n
    if j + 1 >= brk:  # no momentum candle found inside the leg -> keep the unrefined block
        j = k
    top, bot = float(h[j]), float(lo[j])
    if j + 1 < len(lo):
        bot, top = (min(bot, float(lo[j + 1])), top) if d == 1 else (bot, max(top, float(h[j + 1])))
    return j, bot, top


def _origin(b: Bars, ms: dict, ev: int, d: int) -> int:
    """The origin of the leg that broke structure = the protected level set at the break [10]."""
    lvl = ms["prot"][ev]
    x = b["low"] if d == 1 else b["high"]
    hits = np.flatnonzero(x[:ev + 1] == lvl)
    return int(hits[-1]) if len(hits) else ev


def _imbalances(b: Bars, a: int, z: int, d: int) -> int:
    """[3] 3-candle gaps in the departure leg."""
    h, lo = b["high"], b["low"]
    return int(sum(1 for i in range(a + 2, z + 1) if ((lo[i] > h[i - 2]) if d == 1 else (h[i] < lo[i - 2]))))


def _trend_at(ms: dict, t: np.ndarray, sec: int, when: int) -> int:
    """Trend of a timeframe on its last bar CLOSED by `when`."""
    i = int(np.searchsorted(t + sec, when, side="right")) - 1
    return int(ms["trend"][i]) if i >= 0 else 0


def _sim(sym: str, m5: Bars, k: int, d: int, entry: float, stop: float, ex: Exec, hold_days: int,
         target: float | None = None, trail: list | None = None) -> dict:
    """M5 simulation from bar k (the fill bar is excluded: same-bar exits are not knowable). Bid data; longs exit on
    the bid, shorts on the ask (bid + spread). Stop first on a same-bar tie. trail = [(time, new_stop), ...]."""
    risk = (entry - stop) * d
    end = min(len(m5["t"]) - 1, k + hold_days * 288)
    st, ti = stop, 0
    trail = trail or []
    fav = 0.0
    for j in range(k + 1, end + 1):
        tj = int(m5["t"][j])
        while ti < len(trail) and trail[ti][0] <= tj:
            if (trail[ti][1] - st) * d > 0:
                st = trail[ti][1]
            ti += 1
        s = _spread(sym, tj, ex)
        hi, lo = m5["high"][j], m5["low"][j]
        adv = (lo <= st) if d == 1 else (hi + s >= st)
        best = (hi - entry) if d == 1 else (entry - lo - s)
        if adv:
            return {"exit_t": tj, "r": (st - entry) * d / risk, "mfe": max(fav, best / risk), "how": "stop"}
        fav = max(fav, best / risk)
        if target is not None and ((hi >= target) if d == 1 else (lo + s <= target)):
            return {"exit_t": tj, "r": (target - entry) * d / risk, "mfe": fav, "how": "target"}
    px = m5["close"][end] + (0 if d == 1 else _spread(sym, int(m5["t"][end]), ex))
    return {"exit_t": int(m5["t"][end]), "r": (px - entry) * d / risk, "mfe": fav, "how": "time"}


def _cost_r(sym: str, entry: float, risk: float, ex: Exec) -> float:
    """Commission (round turn per lot) + slippage on both sides, in R."""
    usd_per_lot_per_unit = _usd_per_unit_move(sym, entry) * (100.0 if sym == "XAUUSD" else 100_000.0)
    return (ex.commission_per_lot_rt / usd_per_lot_per_unit + 2 * ex.slippage_pips * pip(sym)) / risk


def _fill(m5: Bars, sym: str, ex: Exec, t0: int, t1: int, d: int, limit: float, kill: float) -> int | None:
    """First M5 bar in [t0, t1) where a limit fills (buy: ask <= limit); None if not filled or if a close beyond
    `kill` (the order block / zone is broken) comes first."""
    a, z = int(np.searchsorted(m5["t"], t0)), int(np.searchsorted(m5["t"], t1))
    for j in range(a, min(z, len(m5["t"]))):
        s = _spread(sym, int(m5["t"][j]), ex)
        if (m5["low"][j] + s <= limit) if d == 1 else (m5["high"][j] >= limit):
            return j
        if (m5["close"][j] < kill) if d == 1 else (m5["close"][j] > kill):
            return None
    return None


def scan(args: tuple) -> pd.DataFrame:
    sym, start, end = args
    ex = Exec()
    p = pip(sym)
    m5 = load_m5(sym, (pd.Timestamp(start, tz="UTC") - pd.Timedelta(days=120)).isoformat(), end)
    if len(m5["t"]) < 10_000:
        return pd.DataFrame()
    m15, h1 = resample(m5, M15), resample(m5, H1)
    h4, d1 = bs.agg(h1, H4), bs.agg(h1, 86400)
    ms4, ms15, ms1, msd = _structure(h4), _structure(m15), _structure(h1), _structure(d1)
    a15 = smc.atr(m15)
    a4 = smc.atr(h4)
    t_start = pd.Timestamp(start, tz="UTC").timestamp()
    ms5 = _structure(m5)

    def events(b, ms, sec):
        ev = [(i, dd) for i, _, dd, _, _ in ms["events"]]
        return ev, (np.array([int(b["t"][i]) + sec for i, _ in ev]) if ev else np.zeros(0, np.int64))
    # confirmation two timeframes down, as in his examples: 4H zone -> 15M break [4], 1H zone -> 5M break [5]
    conf = {"4H": (m15, ms15, *events(m15, ms15, M15), M15), "1H": (m5, ms5, *events(m5, ms5, M5), M5)}
    ev1 = [(int(h1["t"][i]) + H1, dd, float(ms1["prot"][i])) for i, _, dd, _, _ in ms1["events"]]
    rows, t0 = [], time.time()
    a1 = smc.atr(h1)
    # zones on the 4H [4, 11] and on the 1H (his GBPJPY example: the 1H order block was the point of interest [5])
    zone_tfs = [("4H", h4, ms4, a4, H4), ("1H", h1, ms1, a1, H1)]
    for tf_name, zt, msz, az, zsec in zone_tfs:
        rows += _zones(sym, tf_name, zt, msz, az, zsec, m5, m15, a15, d1, msd, ms4, h4, conf[tf_name], ev1, ex, p, t_start)
    print(f"{sym}: {len(rows)} zones touched, {sum('conf_trail_r' in r for r in rows)} confirmation trades, "
          f"{sum('risk_trail_r' in r for r in rows)} risk trades ({time.time() - t0:.0f}s)", flush=True)
    return pd.DataFrame(rows)


def _zones(sym, tf_name, h4, ms4, a4, zsec, m5, m15, a15, d1, msd, ms_h4, h4_bars, conf_tf, ev1, ex, p, t_start) -> list:
    """All order-block zones of one timeframe and their returns / entries / exits (see scan)."""
    cb, cms, cev, cev_t, csec = conf_tf
    rows = []
    streak, last_dir = 0, 0
    for ev, kind, d, lvl, _piv in ms4["events"]:
        streak = streak + 1 if (kind == "BOS" and d == last_dir) else (0 if kind == "CHoCH" else 1)
        last_dir = d
        created = int(h4["t"][ev]) + zsec  # the close that broke structure
        if created < t_start:
            continue
        origin = _origin(h4, ms4, ev, d)
        zb = order_block(h4, origin, ev, d)
        if zb is None:
            continue
        zbar, bot, top = zb
        near, far = (top, bot) if d == 1 else (bot, top)
        row = {"symbol": sym, "zone_tf": tf_name, "dir": d, "created": created, "kind": kind, "model": "reversal" if kind == "CHoCH" else "continuation",
               "continuation_n": streak if kind == "BOS" else 0, "zone_pips": (top - bot) / p, "zone_atr": (top - bot) / a4[ev],
               "depart_atr": abs(h4["close"][ev] - near) / a4[ev], "depart_bars": ev - zbar, "imbalances": _imbalances(h4, zbar, ev, d),
               "zone_hour": pd.Timestamp(int(h4["t"][zbar]), unit="s").hour, "d1_trend_at_creation": _trend_at(msd, d1["t"], 86400, created)}
        # [8, 8b] the return: first M15 touch of the near edge within RETURN_WINDOW_DAYS
        i0 = int(np.searchsorted(m15["t"], created))
        i1 = int(np.searchsorted(m15["t"], created + RETURN_WINDOW_DAYS * 86400))
        if i0 >= len(m15["t"]) - 2:
            continue
        seg_hit = (m15["low"][i0:i1] <= near) if d == 1 else (m15["high"][i0:i1] >= near)
        hits = np.flatnonzero(seg_hit)
        if not len(hits):
            continue
        touch = i0 + int(hits[0])
        touch_t = int(m15["t"][touch])
        seg = slice(i0, touch + 1)
        peak = i0 + int(np.argmax(m15["high"][seg]) if d == 1 else np.argmin(m15["low"][seg]))
        dep_bars, ret_bars = max(1, peak - i0 + (ev - zbar) * (zsec // M15)), max(1, touch - peak)
        rl = {k: v[peak:touch + 1] for k, v in m15.items()}
        sh, sl = smc.swing_points(rl, 2) if touch - peak >= 5 else ([], [])
        last = slice(max(peak, touch - 3), touch + 1)
        row.update({"touch_t": touch_t, "wait_h": (touch_t - created) / 3600, "return_bars": ret_bars,
                    "return_ratio": ret_bars / dep_bars, "return_swings": len(sh if d == 1 else sl),
                    "last_leg_atr": float(np.max(np.abs(m15["close"][last] - m15["open"][last])) / a15[touch]),
                    "peak_r_dist": abs((m15["high"][peak] if d == 1 else m15["low"][peak]) - near) / max(top - bot, 1e-9),
                    "d1_trend": _trend_at(msd, d1["t"], 86400, touch_t), "h4_trend": _trend_at(ms_h4, h4_bars["t"], H4, touch_t),
                    "touch_hour": pd.Timestamp(touch_t, unit="s").hour})
        target = float(m15["high"][peak] if d == 1 else m15["low"][peak])  # previous high = liquidity [2]
        # RISK entry [4]: the limit on the zone's near edge, stop beyond the far edge + 1 pip
        j = _fill(m5, sym, ex, created, created + RETURN_WINDOW_DAYS * 86400, d, near, far - d * p)
        if j is not None:
            stop = far - d * p
            risk = (near - stop) * d
            c = _cost_r(sym, near, risk, ex)
            tr = [(t, pr - d * p) for t, dd, pr in ev1 if dd == d and t > int(m5["t"][j])]
            for name, kw in (("2R", {"target": near + d * 2 * risk}), ("3R", {"target": near + d * 3 * risk}),
                             ("prevhigh", {"target": target}), ("trail", {"trail": tr})):
                hold = HOLD_DAYS_TRAIL if name == "trail" else HOLD_DAYS_FIXED
                o = _sim(sym, m5, j, d, near, stop, ex, hold, **kw)
                row[f"risk_{name}_r"] = o["r"] - c
            row["risk_stop_pips"] = risk / p
            row["risk_target_rr"] = (target - near) * d / risk
        # CONFIRMATION entry [4]: after the tap, a 15M break in the trade direction, then the newest 15M OB
        k = int(np.searchsorted(cev_t, touch_t + csec))
        ct = int(np.searchsorted(cb["t"], touch_t))
        conf = None
        while k < len(cev) and cev_t[k] <= touch_t + CONFIRM_H * 3600:
            i15, dd = cev[k]
            broke = (cb["close"][ct:i15 + 1] < far) if d == 1 else (cb["close"][ct:i15 + 1] > far)
            if broke.any():  # [4] price closed through the zone first: invalid
                break
            if dd == d:
                conf = i15
                break
            k += 1
        if conf is not None:
            ob15 = order_block(cb, _origin(cb, cms, conf, d), conf, d)
            if ob15 is not None:
                _, b15, t15 = ob15
                lim, stop = (t15, b15 - p) if d == 1 else (b15, t15 + p)  # [4] stop beyond the block + 1 pip
                t_conf = int(cb["t"][conf]) + csec
                j = _fill(m5, sym, ex, t_conf, t_conf + FILL_H * 3600, d, lim, stop)
                if j is not None and (lim - stop) * d > 0:
                    risk = (lim - stop) * d
                    c = _cost_r(sym, lim, risk, ex)
                    tr = [(t, pr - d * p) for t, dd, pr in ev1 if dd == d and t > int(m5["t"][j])]
                    for name, kw in (("2R", {"target": lim + d * 2 * risk}), ("3R", {"target": lim + d * 3 * risk}),
                                     ("5R", {"target": lim + d * 5 * risk}), ("prevhigh", {"target": target}),
                                     ("trail", {"trail": tr})):
                        if name == "prevhigh" and (target - lim) * d <= 0:
                            continue
                        hold = HOLD_DAYS_TRAIL if name == "trail" else HOLD_DAYS_FIXED
                        o = _sim(sym, m5, j, d, lim, stop, ex, hold, **kw)
                        row[f"conf_{name}_r"] = o["r"] - c
                        if name == "trail":
                            row["conf_trail_mfe"] = o["mfe"]
                    row.update({"conf_stop_pips": risk / p, "conf_target_rr": (target - lim) * d / risk,
                                "conf_fill_t": int(m5["t"][j])})
        rows.append(row)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--symbols", default="EURUSD,GBPUSD,USDJPY,AUDUSD,USDCHF,USDCAD")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    syms = [s for s in a.symbols.split(",") if s]
    with ProcessPoolExecutor(a.workers) as pool:
        parts = list(pool.map(scan, [(s, a.start, a.end) for s in syms]))
    df = pd.concat([x for x in parts if len(x)], ignore_index=True).sort_values("created")
    df.to_csv(a.out, index=False)
    print(f"{len(df)} zones -> {a.out}")


if __name__ == "__main__":
    main()
