"""STRUCTURE FLOW (sflow) -- JeaFx "Master Market Structure in 68 Minutes", rules in docs/jeafx_market_structure.md.

    python -m bensim.sflow --start 2026-07-19 --end 2026-10-18          # 3-month test, 7 pairs, (H1,M5) + (H4,M15)

Structure (closed HTF bars): a BOS needs a CLOSE beyond the level. Up: the level to beat is the impulse high, valid only
once confirmed (k bars without a higher high AND a pull-back of >= 1 ATR = a real correction, not internal); the protected low = the lowest low between the
broken level and the BOS close. A close under the protected low flips to down (no confirmation needed). A wick
beyond a confirmed level without a close = sweep, the level stays. Each BOS gives ONE zone: the base candle of the
impulse (last opposite candle at or up to 3 bars before the protected swing), box = swing extreme .. that candle's far side,
valid only if no wick traded back into it from 2 bars after the zone candle up to the BOS (open imbalance).

Entries (fills/exits on M5 bid data with spread, stop first on ties, commission + slippage as backtest.Exec):
  A  limit at the zone's near edge, stop = protected swing -/+ 0.1 ATR, target = the impulse extreme at fill (>= 1R)
  B  zone tapped -> first LTF BOS in the trade direction within 24 HTF bars -> limit at that LTF zone, same stop
     (beyond the HTF zone) and target as A
  C  sell-to-buy: HTF up, LTF BOS down above the HTF demand -> sell at the LTF supply, stop above it, target = the
     HTF demand's near edge (>= 1.5R); mirror in HTF down
  D  (2026-10-19, user idea, frozen before testing) at the BOS close, market AGAINST the break, target = the zone's
     near edge, stop = beyond the impulse extreme + 0.1 ATR, >= 1R, closed after 5 days
Placebo (= "R", the zone-failure trade, frozen 2026-10-19): every A/B fill replayed opposite, same distances.
--source fxcm: FXCM M5 2012+ (data/bars/fxcm), FXCM's own spread, no commission -- the untouched-data check.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import fxcm_history as fxcm
from .backtest import CURRENCIES, Exec, load_m5, resample
from .strategy import pip
from .phantom import _cost_r, _sim
from .strategy import atr
from .backtest import _spread

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD", "XAUUSD"]
PAIRS = {"H1/M5": (3600, 300), "H4/M15": (14400, 900)}
K = 3  # bars without a new extreme that confirm an impulse high/low
PULLBACK_ATR = 1.0  # ...and the pull-back must be this many ATR: smaller dips are internal structure (his 33:00 mistake)
WAIT_DAYS, HOLD_DAYS, B_WINDOW = 5, 10, 24


def structure(b: dict, k: int = K, state: list | None = None) -> list[dict]:
    """BOS events on closed bars: i (BOS bar), d, origin (protected swing bar), zone (top, bot) or None.
    state (optional list): filled with (phase, consol) after every bar's close -- consol = both live levels only WICKED
    since the last BOS (his "no man's land", 29:20-30:40); phase 0 before the first BOS."""
    h, lo, c, o = b["high"], b["low"], b["close"], b["open"]
    n = len(c)
    if n < 25:
        return []
    up_i, dn_i = int(np.argmax(h[:20])), int(np.argmin(lo[:20]))  # range before any trend: both levels live
    d, up_ok, dn_ok, ev = 0, True, True, []
    sw_up = sw_dn = False
    if state is not None:
        state.extend([(0, False)] * 20)
    av = atr(b)
    for i in range(20, n):  # a level is confirmed only after a REAL pull-back: k bars and >= PULLBACK_ATR x ATR away
        if d == 1 and not up_ok and i - up_i > k and h[up_i] - lo[up_i:i + 1].min() >= PULLBACK_ATR * av[i]:
            up_ok = True
        if d == -1 and not dn_ok and i - dn_i > k and h[dn_i:i + 1].max() - lo[dn_i] >= PULLBACK_ATR * av[i]:
            dn_ok = True
        bull = up_ok and c[i] > h[up_i]
        bear = dn_ok and c[i] < lo[dn_i]
        if bull or bear:
            nd = 1 if bull else -1
            a = up_i if bull else dn_i
            origin = a + int(np.argmin(lo[a:i + 1]) if bull else np.argmax(h[a:i + 1]))
            ext = origin + int(np.argmax(h[origin:i + 1]) if bull else np.argmin(lo[origin:i + 1]))
            ev.append({"i": i, "d": nd, "origin": origin, "zone": _zone(b, origin, i, nd)})
            d = nd
            if bull:
                dn_i, up_i, dn_ok, up_ok = origin, ext, True, i - ext > k
            else:
                up_i, dn_i, up_ok, dn_ok = origin, ext, True, i - ext > k
            sw_up = sw_dn = False
            if state is not None:
                state.append((d, False))
            continue
        if up_ok and h[i] > h[up_i]:
            sw_up = True  # wick through, no close
        if dn_ok and lo[i] < lo[dn_i]:
            sw_dn = True
        if d == 1 and not up_ok and h[i] > h[up_i]:
            up_i = i  # impulse still running: the level follows it until confirmed
        if d == -1 and not dn_ok and lo[i] < lo[dn_i]:
            dn_i = i
        if state is not None:
            state.append((d, sw_up and sw_dn))
    return ev


def _zone(b: dict, origin: int, i: int, d: int) -> tuple[float, float] | None:
    h, lo, c, o = b["high"], b["low"], b["close"], b["open"]
    # the base: last opposite candle AT or BEFORE the swing (never a candle of the impulse itself -- his 46:40 / 48:20)
    opp = [j for j in range(max(0, origin - 3), origin + 1) if (c[j] < o[j] if d == 1 else c[j] > o[j])]
    z = opp[-1] if opp else origin
    if d == 1:
        top, bot = h[z], lo[origin]
        clean = origin + 2 <= i and bool(np.all(lo[origin + 2:i + 1] > top))
    else:
        top, bot = h[origin], lo[z]
        clean = origin + 2 <= i and bool(np.all(h[origin + 2:i + 1] < bot))
    return (top, bot) if clean and top > bot else None


def _fill(m5: dict, sym: str, ex: Exec, a: int, z: int, d: int, limit: float, kill: float) -> int | None:
    for j in range(a, min(z, len(m5["t"]))):
        s = _spread(sym, int(m5["t"][j]), ex)
        if (m5["low"][j] + s <= limit) if d == 1 else (m5["high"][j] >= limit):
            return j
        if (m5["close"][j] < kill) if d == 1 else (m5["close"][j] > kill):
            return None
    return None


def _trade(sym, m5, ex, kind, d, k, entry, stop, target, min_rr, t_from, hold=HOLD_DAYS) -> list[dict]:
    risk = (entry - stop) * d
    if risk <= 0 or (target - entry) * d < min_rr * risk:
        return []
    out = []
    for side, tag in ((d, kind), (-d, kind + "_placebo")):
        if tag in ("C_placebo", "D_placebo"):
            continue
        st, tg = entry - side * risk, entry + side * abs(target - entry)
        r = _sim(sym, m5, k, side, entry, st, ex, hold, target=tg)
        out.append({"symbol": sym, "kind": tag, "dir": side, "fill_t": int(m5["t"][k]), "rr": abs(target - entry) / risk,
                    "r": r["r"] - _cost_r(sym, entry, risk, ex), "how": r["how"], "from": t_from,
                    "entry": entry, "stop": st, "target": tg, "cost_r": _cost_r(sym, entry, risk, ex), "exit_t": r["exit_t"]})
    return out


def run(args: tuple) -> list[dict]:
    sym, pair, start, end, source = args
    hs, ls = PAIRS[pair]
    t0 = int(pd.Timestamp(start, tz="UTC").timestamp())
    if source == "fxcm":  # FXCM retail bid M5 2012+: its own median spread, no commission (FXCM prices are all-in)
        z = fxcm.load(sym, "M5")
        keep = (z["t"] >= t0 - 60 * 86400) & (z["t"] < int(pd.Timestamp(end, tz="UTC").timestamp()))
        m5 = {k: v[keep] for k, v in z.items()}
        ex = Exec(raw_spread_pips={sym: float(np.median(m5["spr"])) / pip(sym)}, commission_per_lot_rt=0.0)
        CURRENCIES.setdefault(sym, (sym[:3], sym[3:]))  # ponytail: non-USD-quote crosses use 1/price in _cost_r (commission is 0 here, so only slippage)
    else:
        ex = Exec()
        m5 = load_m5(sym, (pd.Timestamp(start) - pd.Timedelta(days=60)).strftime("%Y-%m-%d"), end)
    hb, lb = resample(m5, hs), resample(m5, ls)
    a_h = atr(hb)
    he = [e for e in structure(hb) if e["zone"]]
    le = [dict(e, t=int(lb["t"][e["i"]]) + ls) for e in structure(lb) if e["zone"]]
    allh = structure(hb)
    nxt = {e["i"]: next((x["i"] for x in allh if x["i"] > e["i"]), None) for e in he}
    idx = lambda t: int(np.searchsorted(m5["t"], t))  # noqa: E731
    out: list[dict] = []
    for e in he:
        t_bos = int(hb["t"][e["i"]]) + hs
        if t_bos < t0:
            continue
        d, (top, bot) = e["d"], e["zone"]
        t_next = int(hb["t"][nxt[e["i"]]]) + hs if nxt[e["i"]] is not None else int(m5["t"][-1])
        t_end = min(t_next, t_bos + WAIT_DAYS * 86400)
        near, far = (top, bot) if d == 1 else (bot, top)
        stop = far - d * 0.1 * a_h[e["i"]]
        t_org = int(hb["t"][e["origin"]])

        def target_at(k: int) -> float:  # the impulse extreme printed before the fill bar
            seg = slice(idx(t_org), k)
            return float(m5["high"][seg].max() if d == 1 else m5["low"][seg].min())

        kd = idx(t_bos)  # D: at the BOS close, trade the pull-back INTO the zone (market, against the BOS)
        if 0 < kd < len(m5["t"]):
            ext = float(hb["high"][e["origin"]:e["i"] + 1].max() if d == 1 else hb["low"][e["origin"]:e["i"] + 1].min())
            px = m5["open"][kd] + (_spread(sym, int(m5["t"][kd]), ex) if d == -1 else 0.0)  # buys pay the ask
            out += _trade(sym, m5, ex, "D", -d, kd - 1, px, ext + d * 0.1 * a_h[e["i"]], near, 1.0, t_bos, hold=WAIT_DAYS)
        k = _fill(m5, sym, ex, idx(t_bos), idx(t_end), d, near, far)  # A
        if k is not None:
            out += _trade(sym, m5, ex, "A", d, k, near, stop, target_at(k), 1.0, t_bos)
        tap = _fill(m5, sym, ex, idx(t_bos), idx(t_end), d, near, far)  # B: tap = the same touch, then LTF BOS
        if tap is not None:
            t_tap = int(m5["t"][tap])
            le_b = next((x for x in le if x["d"] == d and t_tap < x["t"] <= min(t_tap + B_WINDOW * hs, t_end)), None)
            if le_b:
                ln = le_b["zone"][0] if d == 1 else le_b["zone"][1]
                kb = _fill(m5, sym, ex, idx(le_b["t"]), idx(t_end), d, ln, far)
                if kb is not None:
                    out += _trade(sym, m5, ex, "B", d, kb, ln, stop, target_at(kb), 1.0, t_bos)
        for x in le:  # C: counter-trend LTF breaks while the HTF trend holds, aimed at the HTF zone
            if x["d"] != -d or not (t_bos < x["t"] < t_next):
                continue
            ltop, lbot = x["zone"]
            ln, lfar = (lbot, ltop) if d == 1 else (ltop, lbot)
            if (ln - near) * d <= 0:
                continue  # already at/inside the HTF zone
            nx = next((y["t"] for y in le if y["t"] > x["t"]), t_next)
            kc = _fill(m5, sym, ex, idx(x["t"]), idx(min(nx, t_next)), -d, ln, lfar)
            if kc is not None:
                out += _trade(sym, m5, ex, "C", -d, kc, ln, lfar + d * 0.1 * a_h[e["i"]], near, 1.5, t_bos)
    for r in out:
        r["pair"] = pair
    return [r for r in out if r["fill_t"] >= t0]


def _selfcheck() -> None:
    # up leg, correction, close above the high = BOS up; zone at the base of the impulse
    hi = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29,
          30, 29, 28, 27, 26, 25, 27, 29, 31, 33, 35]
    b = {"high": np.array(hi, float), "low": np.array(hi, float) - 2, "open": np.array(hi, float) - 1.5, "close": np.array(hi, float) - 0.5}
    b["close"][20:26] = b["low"][20:26] + 0.2  # bearish correction candles
    ev = structure(b)
    assert ev and ev[0]["d"] == 1, ev
    up = [e for e in ev if e["d"] == 1 and e["i"] > 25]
    assert up and up[0]["origin"] == 25, up  # protected low = the correction's low
    print("sflow selfcheck ok", up[0])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-07-19")
    ap.add_argument("--end", default="2026-10-18")
    ap.add_argument("--out", default="data/sflow_3m.csv")
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--source", default="icm", choices=["icm", "fxcm"])
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    a = ap.parse_args()
    if a.selfcheck:
        return _selfcheck()
    jobs = [(s, p, a.start, a.end, a.source) for p in PAIRS for s in a.symbols.split(",")]
    with ProcessPoolExecutor(4) as pool:
        t = pd.DataFrame([r for rs in pool.map(run, jobs) for r in rs])
    t.to_csv(a.out, index=False)
    print(f"{a.start} .. {a.end}, {len(SYMBOLS)} pairs, net of costs -> {a.out}")
    for (pair, kind), g in t.groupby(["pair", "kind"]):
        tt = g.r.mean() / g.r.std() * np.sqrt(len(g)) if len(g) > 2 else 0
        print(f"  {pair:7} {kind:10} n={len(g):4}  avg {g.r.mean():+.3f}R  total {g.r.sum():+7.1f}R  t {tt:+.1f}  "
              f"win {np.mean(g.r > 0):.0%}  rr~{g.rr.median():.1f}")
    for pair, g in t[t.kind.isin(["A", "B"])].groupby("pair"):
        print(f"  {pair} A+B by symbol:", g.groupby("symbol").r.sum().round(1).to_dict())


if __name__ == "__main__":
    main()
