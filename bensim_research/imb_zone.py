"""'Imbalance -> imbalanced zone' strategy, transcribed from D:/Downloads/imbalance.mp4 (+ Imbalance.mp3).

Rules as taught (video timestamps), made mechanical. Fixed BEFORE any backtest; see docs/imbalance-video-strategy.md.
  * Imbalance = open price range left by one large candle or a run of large candles with no wicks
    pulling back through them (0:31-1:19). Here: a chain of consecutive same-direction 3-candle gaps.
  * Zone = the base/indecision candle the run started from (1:30, 5:11, 7:00). Demand = [low, high]
    of the base candle under a bullish run; supply mirrored. Price is expected to fill the imbalance
    and react at the zone, not inside the gap (1:25-1:43).
  * Structure decides direction (5:41, 8:58): the run must break the last confirmed swing (BOS) before
    price ever returns to the zone. The zone's own BOS is the direction; a deeper pullback that makes
    lower TF lows does not cancel it (AUDUSD daily example 4:43-4:55 buys after such a pullback).
  * Only untouched zones count; a retested zone is efficient and removed (10:03, 10:43).
  * Trade the extreme: skip a zone if an older untouched same-direction zone lies beyond it (3:48-4:08, 12:03).
  * Entry = limit at the zone's near edge, stop beyond the zone (7:11), target = the nearest untouched
    opposite zone (7:22-7:40, 9:10, 11:21). No opposite zone -> no trade. Reward:risk >= 1 sanity floor.

Fills on the FOREXSB M15 base (UTC), same conservative rules as the Bensim engine: a limit fill bar can
stop out but not take profit; a bar touching both stop and target counts as a stop; costs = base scenario.

python -m bensim_research.imb_zone [--workers 3]
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import analytics as A
from . import data as D
from . import features as F
from .backtest import _costs
from .run import ART, OUT, _low_priority

TFS = ("M30", "H2", "D1")  # the timeframes used in the video
HOLD = int(pd.Timestamp("2024-01-01", tz="UTC").timestamp())
MAX_HOLD, ZONE_LIFE, BUF = 200, 500, 0.1  # TF bars a trade may run / a zone stays live; stop buffer in ATR
DEST = ART / "imb_zone"
NEVER = np.iinfo(np.int64).max


def zones(b: D.Bars, a: np.ndarray, st: F.Structure, base: D.Bars) -> pd.DataFrame:
    """Every imbalanced zone on TF bars `b` with its BOS / first-retest timing (known-at semantics)."""
    fv = F.fvgs(b, a)
    rows = []
    for d in (1, -1):
        j = np.sort(fv.j.to_numpy()[fv.dir.to_numpy() == d])
        if not len(j):
            continue
        starts = j[np.r_[True, np.diff(j) > 1]]  # chained gaps = one run with no wick pulled back through it
        for j0 in starts:
            B = j0 - 2
            if B < 50:
                continue
            prox, dist = (b.h[B], b.l[B]) if d == 1 else (b.l[B], b.h[B])
            lvl = st.sh[B] if d == 1 else st.sl[B]  # last confirmed swing on the far side when the base printed
            after = slice(j0 + 1, min(len(b), j0 + 1 + ZONE_LIFE))
            touch = (b.l[after] <= prox) if d == 1 else (b.h[after] >= prox)
            ti = j0 + 1 + int(np.argmax(touch)) if touch.any() else NEVER
            bos_i = NEVER
            if not np.isnan(lvl) and (lvl - prox) * d > 0:
                seg = slice(B + 1, min(len(b), j0 + 1 + ZONE_LIFE))
                brk = (b.c[seg] > lvl) if d == 1 else (b.c[seg] < lvl)
                bos_i = B + 1 + int(np.argmax(brk)) if brk.any() else NEVER
            rows.append({"dir": d, "B": B, "j0": j0, "prox": prox, "dist": dist, "atr": a[B], "level": lvl,
                         "t_known": int(b.known_at[j0]), "bos_i": bos_i, "touch_i": ti,
                         "t_bos": int(b.known_at[bos_i]) if bos_i != NEVER else NEVER,
                         "t_end": int(b.known_at[min(len(b) - 1, j0 + ZONE_LIFE)])})
    z = pd.DataFrame(rows)
    if z.empty:
        return z
    # first retest in real time (M15 base), used for "untouched" checks at any instant
    t_mit = np.full(len(z), NEVER, dtype=np.int64)
    for r, (d, prox, tk, te) in enumerate(z[["dir", "prox", "t_known", "t_end"]].itertuples(index=False)):
        s, e = int(np.searchsorted(base.t, tk)), int(np.searchsorted(base.t, te))
        m = (base.l[s:e] <= prox) if d == 1 else (base.h[s:e] >= prox)
        if m.any():
            t_mit[r] = int(base.t[s + int(np.argmax(m))])
    z["t_mit"] = t_mit
    z["armed"] = (z.bos_i < z.touch_i) & (z.bos_i != NEVER)  # broke structure before price came back
    return z.sort_values("t_known", kind="stable").reset_index(drop=True)


def simulate(base: D.Bars, t_e: int, d: int, entry: float, stop: float, target: float, P: int, rt: float, slip: float):
    """Limit at `entry` filled on the base bar at t_e; returns (entry, k, exit_px, reason, bars, gross_r, cost_r) or None."""
    k = int(np.searchsorted(base.t, t_e))
    if (base.o[k] - stop) * d <= 0:
        return None
    risk = (entry - stop) * d  # position sized on the PLANNED stop distance; a better (gap) fill is a bonus, not a smaller R
    entry = min(base.o[k], entry) if d == 1 else max(base.o[k], entry)
    end = min(len(base), int(np.searchsorted(base.t, t_e + MAX_HOLD * P)))
    hi, lo = (base.h, base.l) if d == 1 else (-base.l, -base.h)
    hit_s = lo[k:end] <= stop * d
    hit_t = np.r_[False, hi[k + 1:end] >= target * d]  # no profit on the limit fill bar
    si = int(np.argmax(hit_s)) if hit_s.any() else NEVER
    ti = int(np.argmax(hit_t)) if hit_t.any() else NEVER
    if si == ti == NEVER:
        x, why_x, xi = float(base.c[end - 1]), "time", end - 1 - k
    elif si <= ti:
        x, why_x, xi = stop, "stop", si
    else:
        x, why_x, xi = target, "target", ti
    return entry, k, x, why_x, xi, (x - entry) * d / risk, (rt + slip * (why_x == "stop")) / risk


def trades(symbol: str, tf: str, b: D.Bars, base: D.Bars, rules: dict) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    a = F.atr(b)
    st = F.structure(b, 2)
    z = zones(b, a, st, base)
    rt, slip = _costs(symbol, "base")
    P = D.TF_SECONDS[tf]
    out, why = [], {}

    def skip(k: str) -> None:
        why[k] = why.get(k, 0) + 1

    if z.empty:
        return pd.DataFrame(), why, z
    zd, zp, zx, zk, zm, zb, ze = (z[c].to_numpy() for c in ("dir", "prox", "dist", "t_known", "t_mit", "t_bos", "t_end"))
    for r in np.flatnonzero(z.armed.to_numpy()):
        d, prox, dist, R_atr = int(zd[r]), float(zp[r]), float(zx[r]), float(z.atr[r])
        t_e = int(zm[r])  # the first retest is the entry (a limit resting at the near edge)
        if t_e == NEVER:
            skip("zone never retested")
            continue
        if t_e < zb[r]:
            skip("retested before BOS")
            continue
        bi = int(D.last_closed(b, np.array([t_e]))[0])
        if rules["trend"] and (bi < 0 or st.trend[bi] != d):
            skip("TF trend disagrees at retest")
            continue
        live = (zk <= t_e) & (zm > t_e) & (ze >= t_e)  # known, untouched and not expired
        if rules["extreme"] and ((zd == d) & live & (zb <= t_e) & ((dist - zp) * d > 0)).any():
            skip("not the extreme zone")
            continue
        entry = prox
        stop = dist - d * BUF * R_atr
        opp = (zd == -d) & live & ((zp - entry) * d > 0)
        if not opp.any():
            skip("no opposite imbalance zone to target")
            continue
        target = float(zp[opp].min() if d == 1 else zp[opp].max())
        risk = (entry - stop) * d
        if (target - entry) * d < risk:
            skip("target nearer than 1R")
            continue
        res = simulate(base, t_e, d, entry, stop, target, P, rt, slip)
        if res is None:
            skip("gapped through stop at entry")
            continue
        entry, k, x, why_x, xi, gross, cost = res
        risk = (entry - stop) * d
        out.append({"symbol": symbol, "tf": tf, "dir": d, "t_entry": int(base.t[k]), "t_exit": int(base.t[k + xi]) + 900,
                    "entry": entry, "stop": stop, "target": target, "exit": x, "rr": (target - entry) * d / risk,
                    "risk_atr": risk / R_atr, "r_gross": gross, "cost_r": cost, "r": gross - cost, "exit_reason": why_x,
                    "zone_prox": prox, "zone_dist": dist, "t_zone": int(zk[r]), "t_bos": int(zb[r])})
    return pd.DataFrame(out), why, z


VARIANTS = {  # primary = the video's rules; the others are diagnostics showing what each rule contributes
    "video rules": {"trend": False, "extreme": True},
    "diag: no extreme rule": {"trend": False, "extreme": False},
    "diag: + current-trend filter": {"trend": True, "extreme": True},
}


def load_base(symbol: str) -> D.Bars:
    """FOREXSB M15 with >4-day holes (EURUSD 2022-10..2024-08) patched by FOREXSB M30 bars.
    ponytail: patched spans resolve fills on 30m bars; fine for M30+ setups, not for M15 work."""
    base = D.load(symbol, "M15")[0]
    m30, _ = D.clean(D._fetch(symbol, "M30", "FOREXSB", None), "M30")
    keep = m30.t < D.END_TS
    m30 = D.Bars("M15", m30.t[keep], m30.o[keep], m30.h[keep], m30.l[keep], m30.c[keep], m30.v[keep])
    return D._fill_gaps(base, m30)[0]


def run_symbol(args: tuple) -> tuple[list, dict]:
    symbol, tfs = args
    t0 = time.time()
    patched = load_base(symbol)
    raw = D.load(symbol, "M15")[0]  # M15 setups need true 15m bars: no 30m patch (EURUSD gap = no trades)
    frames, reasons = [], {}
    for tf in tfs:
        base = raw if tf == "M15" else patched
        b = base if tf == "M15" else D.resample(base, tf)
        for name, rules in VARIANTS.items():
            t, why, _ = trades(symbol, tf, b, base, rules)
            if not t.empty:
                frames.append(t.assign(variant=name))
            reasons[f"{tf}|{name}"] = why
    print(f"{symbol}: {sum(len(f) for f in frames)} trades ({time.time() - t0:.0f}s)", flush=True)
    return frames, {symbol: reasons}


def summary(t: pd.DataFrame) -> dict:
    m = {k: v for k, v in A.metrics(t).items() if k in ("trades", "win_rate", "expectancy_r", "expectancy_gross_r", "avg_cost_r", "profit_factor", "t_stat", "net_r", "max_dd_r", "trades_per_week")}
    if len(t):
        m["avg_rr"] = round(float(t.rr.mean()), 2)
        m["exits"] = t.exit_reason.value_counts().to_dict()
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--symbols", default=",".join(D.SYMBOLS))
    ap.add_argument("--tfs", default=",".join(TFS))
    ap.add_argument("--since", default="", help="only trades entered on/after this UTC date, e.g. 2023-08-14")
    ap.add_argument("--tag", default="", help="suffix for the output files")
    args = ap.parse_args()
    _low_priority()
    DEST.mkdir(parents=True, exist_ok=True)
    frames, reasons = [], {}
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_low_priority) as ex:
        for f, rsn in ex.map(run_symbol, [(s, args.tfs.split(",")) for s in args.symbols.split(",")]):
            frames += f
            reasons.update(rsn)
    tr = pd.concat(frames, ignore_index=True)
    if args.since:
        tr = tr[tr.t_entry >= int(pd.Timestamp(args.since, tz="UTC").timestamp())]
    tag = f"_{args.tag}" if args.tag else ""
    tr.to_csv(DEST / f"trades{tag}.csv.gz", index=False)
    rep: dict = {"rules": "see bensim_research/imb_zone.py docstring", "no_trade_reasons": reasons}
    for (v, tf), g in tr.groupby(["variant", "tf"]):
        rep.setdefault(v, {})[tf] = {"all": summary(g), "dev": summary(g[g.t_entry < HOLD]), "holdout": summary(g[g.t_entry >= HOLD]),
                                     "by_symbol": {s: summary(x) for s, x in g.groupby("symbol")},
                                     "by_dir": {int(s): summary(x) for s, x in g.groupby("dir")}}
    (OUT / f"imb_zone_report{tag}.json").write_text(json.dumps(rep, indent=1, default=str))
    print("wrote", OUT / f"imb_zone_report{tag}.json")


if __name__ == "__main__":
    main()
