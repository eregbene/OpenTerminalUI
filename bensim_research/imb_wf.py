"""Monthly walk-forward for the imbalance-video strategy (bensim_research/imb_zone.py).

Each month the rule set is chosen ONLY from trades that closed before the month starts (trailing
24 months), then traded blind for that month. This is "improve it month by month" without seeing the
future. A hindsight pick (best on calendar 2025 itself) is reported next to it to show the gap.

Candidate filters (fixed before running; from the video, the web search and the 3-year test):
  tf      M30 / H1 / H4                      timeframe of the zone
  tmode   near / second / R3                 target: nearest opposite zone, the one after it, fixed 3R
  minmult 0 / 8 / 15 / 25                    stop must be >= this many round-trip spreads (wick-outs, cost share)
  extreme 0/1                                video: only the extreme untouched zone
  htf     0/1                                web: zone direction agrees with HTF structure (M30,H1->H4, H4->D1)
  kz      0/1                                web: imbalance printed in a killzone (07-10 or 12-15 UTC)
  disp    0/1                                web: first displacement candle body >= 1 ATR
Selection score: t-stat of net R over the trailing window, at least 40 trades.

python -m bensim_research.imb_wf
"""
from __future__ import annotations

import itertools
import json
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import data as D
from . import features as F
from . import imb_zone as I
from .backtest import _costs
from .run import OUT, _low_priority

TFS = ("M30", "H1", "H4")
HTF = {"M30": "H4", "H1": "H4", "H4": "D1"}
GRID = {"tf": TFS, "tmode": ("near", "second", "R3"), "minmult": (0, 8, 15, 25),
        "extreme": (0, 1), "htf": (0, 1), "kz": (0, 1), "disp": (0, 1)}
TRAIN_MONTHS, MIN_TRADES = 24, 40
DEST = I.DEST


def pool(symbol: str, base: D.Bars | None = None) -> pd.DataFrame:
    """Every first-retest trade of every armed zone, all target modes, with filter flags (no filters applied)."""
    base = base if base is not None else I.load_base(symbol)
    rt, slip = _costs(symbol, "base")
    rows = []
    for tf in TFS:
        b = D.resample(base, tf)
        a, st = F.atr(b), F.structure(b, 2)
        hb = D.resample(base, HTF[tf])
        sth = F.structure(hb, 3)
        z = I.zones(b, a, st, base)
        if z.empty:
            continue
        P = D.TF_SECONDS[tf]
        zd, zp, zx, zk, zm, zb, ze = (z[c].to_numpy() for c in ("dir", "prox", "dist", "t_known", "t_mit", "t_bos", "t_end"))
        for r in np.flatnonzero(z.armed.to_numpy()):
            d, t_e = int(zd[r]), int(zm[r])
            if t_e == I.NEVER or t_e < zb[r]:
                continue
            live = (zk <= t_e) & (zm > t_e) & (ze >= t_e)
            B, j0 = int(z.B[r]), int(z.j0[r])
            run = 1
            while j0 + run < len(b) and ((b.l[j0 + run] > b.h[j0 + run - 2]) if d == 1 else (b.h[j0 + run] < b.l[j0 + run - 2])):
                run += 1
            hi_ = int(D.last_closed(hb, np.array([t_e]))[0])
            flags = {"extreme": int(not ((zd == d) & live & (zb <= t_e) & ((zx[r] - zp) * d > 0)).any()),
                     "htf": int(hi_ >= 0 and sth.trend[hi_] == d),
                     "kz": int(((b.t[B + 1] % 86400) // 3600) in (7, 8, 9, 12, 13, 14)),
                     "disp": int(abs(b.c[B + 1] - b.o[B + 1]) >= a[B])}
            entry, stop = float(zp[r]), float(zx[r]) - d * I.BUF * float(a[B])
            risk = (entry - stop) * d
            opp = np.sort(np.abs(zp[(zd == -d) & live & ((zp - entry) * d > 0)] - entry))
            targets = {"R3": entry + d * 3 * risk}
            if len(opp) >= 1 and opp[0] >= risk:
                targets["near"] = entry + d * opp[0]
            far = opp[opp >= risk]
            if len(far) >= 2:
                targets["second"] = entry + d * far[1]
            for tmode, target in targets.items():
                res = I.simulate(base, t_e, d, entry, stop, target, P, rt, slip)
                if res is None:
                    continue
                e2, k, x, why_x, xi, gross, cost = res
                rows.append({"symbol": symbol, "tf": tf, "tmode": tmode, "dir": d, "t_entry": int(base.t[k]), "entry": e2,
                             "stop": stop, "target": target, "exit_px": x, "zone_lo": min(zp[r], zx[r]), "zone_hi": max(zp[r], zx[r]),
                             "t_base": int(b.t[B]), "level": float(z.level[r]),
                             "t_exit": int(base.t[k + xi]) + 900, "minmult_val": (e2 - stop) * d / rt,
                             "r_gross": gross, "cost_r": cost, "r": gross - cost, "exit_reason": why_x, **flags,
                             "rr": (target - e2) * d / ((e2 - stop) * d), "zone_atr": (b.h[B] - b.l[B]) / a[B],
                             "run_gaps": run, "age_bars": (t_e - int(zk[r])) / P, "bos_bars": (int(zb[r]) - int(zk[r])) / P,
                             "form_hour": int((b.t[B + 1] % 86400) // 3600)})
    print(symbol, len(rows), flush=True)
    return pd.DataFrame(rows)


def _t(r: np.ndarray) -> float:
    return float(r.mean() / r.std(ddof=1) * np.sqrt(len(r))) if len(r) > 2 and r.std() > 0 else -np.inf


def main() -> None:
    _low_priority()
    path = DEST / "wf_pool.csv.gz"
    if path.exists():
        p = pd.read_csv(path)
    else:
        with ProcessPoolExecutor(max_workers=3, initializer=_low_priority) as ex:
            p = pd.concat(ex.map(pool, D.SYMBOLS), ignore_index=True)
        p.to_csv(path, index=False)
    p = p.sort_values("t_entry").reset_index(drop=True)
    configs = [dict(zip(GRID, v)) for v in itertools.product(*GRID.values())]
    sel = {}
    for i, c in enumerate(configs):
        m = (p.tf == c["tf"]) & (p.tmode == c["tmode"]) & (p.minmult_val >= c["minmult"])
        for f in ("extreme", "htf", "kz", "disp"):
            if c[f]:
                m &= p[f] == 1
        sel[i] = p[m]
    ts = lambda s: int(pd.Timestamp(s, tz="UTC").timestamp())  # noqa: E731
    months = pd.date_range("2025-01-01", "2026-07-01", freq="MS", tz="UTC")
    name = lambda c: " ".join(f"{k}={v}" for k, v in c.items())  # noqa: E731
    wf, picks = [], []
    for ms in months:
        m0, m1 = int(ms.timestamp()), int((ms + pd.offsets.MonthBegin(1)).timestamp())
        tr0 = int((ms - pd.DateOffset(months=TRAIN_MONTHS)).timestamp())
        best, score = None, -np.inf
        for i, t in sel.items():
            r = t.r.to_numpy()[(t.t_entry.to_numpy() >= tr0) & (t.t_exit.to_numpy() < m0)]
            if len(r) >= MIN_TRADES and _t(r) > score:
                best, score = i, _t(r)
        t = sel[best]
        live = t[(t.t_entry >= m0) & (t.t_entry < m1)]
        wf.append(live.assign(month=ms.strftime("%Y-%m")))
        picks.append({"month": ms.strftime("%Y-%m"), "picked": name(configs[best]), "train_t": round(score, 2),
                      "trades": len(live), "net_R": round(float(live.r.sum()), 2), "avg_R": round(float(live.r.mean()), 3) if len(live) else None})
        print(picks[-1], flush=True)
    wf = pd.concat(wf, ignore_index=True)

    def summ(t: pd.DataFrame) -> dict:
        r = t.r.to_numpy()
        return {"trades": len(r), "net_R": round(float(r.sum()), 1), "avg_R": round(float(r.mean()), 3) if len(r) else None,
                "win%": round(100 * float((t.exit_reason == "target").mean()), 1) if len(r) else None, "t": round(_t(r), 2) if len(r) > 2 else None}

    y25 = (ts("2025-01-01"), ts("2026-01-01"))
    y26 = (ts("2026-01-01"), ts("2026-08-01"))
    inwin = lambda t, w: t[(t.t_entry >= w[0]) & (t.t_entry < w[1])]  # noqa: E731
    hind = max(sel, key=lambda i: _t(inwin(sel[i], y25).r.to_numpy()) if len(inwin(sel[i], y25)) >= MIN_TRADES else -np.inf)
    video = {"tf": "H4", "tmode": "near", "minmult": 0, "extreme": 1, "htf": 0, "kz": 0, "disp": 0}
    vid = configs.index(video)
    rep = {"walk_forward_2025": summ(wf[wf.month < "2026"]), "walk_forward_2026_jan_jul": summ(wf[wf.month >= "2026"]),
           "monthly_picks": picks,
           "hindsight_best_2025": {"config": name(configs[hind]), "in_2025_(hindsight)": summ(inwin(sel[hind], y25)),
                                   "same_rules_2026_jan_jul": summ(inwin(sel[hind], y26))},
           "video_rules_H4_fixed": {"2025": summ(inwin(sel[vid], y25)), "2026_jan_jul": summ(inwin(sel[vid], y26))},
           "configs_tested": len(configs)}
    (OUT / "imb_wf_report.json").write_text(json.dumps(rep, indent=1))
    wf.to_csv(DEST / "wf_trades.csv.gz", index=False)
    print(json.dumps({k: v for k, v in rep.items() if k != "monthly_picks"}, indent=1))


if __name__ == "__main__":
    main()
