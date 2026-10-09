"""donchian_trend_follow (backend/mt5_strategies/families/donchian_trend_follow.py) as a research replay,
plus two pre-declared combinations with the imbalance-video strategy (bensim_research/imb_zone.py).

Donchian, matching the live evaluator's defaults (BREAK_AND_GO mode):
  * M15 close beyond the 20-bar channel built from the bars before it, by >= 0.3 ATR(M15)
  * volatility expansion: ATR(M15) / SMA50(ATR) >= 1.1   (live also accepts a HIGH_VOLATILITY_EXPANSION regime label: not modelled)
  * H1 trend agrees (H1 swing structure, the research stand-in for ctx.htf_trend_h1)
  * market entry at the next bar's open; stop = opposite channel edge clamped to [1.2, 3.0] ATR;
    target = 4 ATR (MT5_STRUCTURAL_TP_ENABLED is off by default -> the flat fallback); one open trade per symbol
Combinations (fixed before running):
  A "filter": 1-hour imbalance-zone trades taken only in the direction of the latest Donchian signal (last 24h)
  B "IMB entry": after a Donchian signal, a limit at the imbalance zone left by the breakout run (M15 zone whose
     run started within the last 5 bars), stop under the zone, same 4-ATR target price; valid 48 bars
Fills/costs: same conservative rules as the rest of bensim_research (same-bar stop+target = stop, base costs).

python -m bensim_research.donchian --start 2026-08-01 --end 2026-10-01
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from . import data as D
from . import features as F
from . import imb_zone as I
from .backtest import _costs
from .imb_month import live_base
from .run import OUT

N, MIN_BRK, MIN_EXP, STOP_MIN, STOP_MAX, TGT, HOLD = 20, 0.3, 1.1, 1.2, 3.0, 4.0, 200
COMBO_WAIT, FILTER_WINDOW = 48, 86400


def signals(base: D.Bars) -> pd.DataFrame:
    """Every Donchian signal bar (index into base M15) with direction, entry, stop, target."""
    h, l, c = base.h, base.l, base.c
    a = F.atr(base)
    exp = a / pd.Series(a).rolling(50).mean().to_numpy()
    hi = pd.Series(h).rolling(N).max().shift(1).to_numpy()
    lo = pd.Series(l).rolling(N).min().shift(1).to_numpy()
    h1 = D.resample(base, "H1")
    st = F.structure(h1, 2)
    hi1 = D.last_closed(h1, base.known_at)
    trend = np.where(hi1 >= 0, st.trend[np.maximum(hi1, 0)], 0)
    rows = []
    for i in range(N + 50, len(base) - 1):
        for d, edge, opp in ((1, hi[i], lo[i]), (-1, lo[i], hi[i])):
            if (c[i] - edge) * d < MIN_BRK * a[i] or not exp[i] >= MIN_EXP or trend[i] != d:
                continue
            entry = base.o[i + 1]
            dist = np.clip(abs(entry - opp), STOP_MIN * a[i], STOP_MAX * a[i])
            rows.append({"i": i, "t_signal": int(base.known_at[i]), "dir": d, "entry": entry, "stop": entry - d * dist,
                         "target": entry + d * TGT * a[i], "atr": a[i]})
    return pd.DataFrame(rows)


def run_trade(base, k: int, d: int, entry: float, stop: float, target: float, rt: float, slip: float, market: bool) -> dict:
    """Trade from base bar k. market=True: filled at k's open, both sides live on bar k."""
    end = min(len(base), k + HOLD)
    hi, lo = (base.h, base.l) if d == 1 else (-base.l, -base.h)
    hit_s = lo[k:end] <= stop * d
    hit_t = hi[k:end] >= target * d
    if not market:
        hit_t[0] = False
    si = int(np.argmax(hit_s)) if hit_s.any() else I.NEVER
    ti = int(np.argmax(hit_t)) if hit_t.any() else I.NEVER
    if si == ti == I.NEVER:
        x, why, xi = float(base.c[end - 1]), "time", end - 1 - k
    elif si <= ti:
        x, why, xi = stop, "stop", si
    else:
        x, why, xi = target, "target", ti
    risk = (entry - stop) * d
    cost = (rt + slip * (int(market) + (why == "stop"))) / risk
    return {"t_entry": int(base.t[k]), "t_exit": int(base.t[k + xi]) + 900, "entry": entry, "stop": stop, "target": target,
            "exit_reason": why, "r_gross": (x - entry) * d / risk, "cost_r": cost, "r": (x - entry) * d / risk - cost,
            "open_at_data_end": k + HOLD > len(base) and why == "time"}


def donchian_trades(symbol: str, base: D.Bars, sig: pd.DataFrame, t0: int, t1: int) -> pd.DataFrame:
    rt, slip = _costs(symbol, "base")
    out, busy_until = [], 0
    for s in sig.itertuples(index=False):
        k = s.i + 1
        if not (t0 <= base.t[k] < t1) or base.t[k] < busy_until:
            continue
        tr = run_trade(base, k, s.dir, s.entry, s.stop, s.target, rt, slip, market=True)
        busy_until = tr["t_exit"]
        out.append({"symbol": symbol, "variant": "donchian alone", "dir": s.dir, **tr})
    return pd.DataFrame(out)


def imb_entry_trades(symbol: str, base: D.Bars, sig: pd.DataFrame, t0: int, t1: int) -> pd.DataFrame:
    """Combination B: after a Donchian signal, limit at the M15 imbalance zone the breakout run left."""
    rt, slip = _costs(symbol, "base")
    a = F.atr(base)
    z = I.zones(base, a, F.structure(base, 2), base)
    zd, zB, zj, zp, zx = (z[c].to_numpy() for c in ("dir", "B", "j0", "prox", "dist"))
    out, busy_until = [], 0
    for s in sig.itertuples(index=False):
        if not (t0 <= s.t_signal < t1) or s.t_signal < busy_until:
            continue
        m = (zd == s.dir) & (zj <= s.i) & (zj >= s.i - 5)
        if not m.any():
            continue
        r = np.flatnonzero(m)[-1]
        entry, stop = float(zp[r]), float(zx[r]) - s.dir * I.BUF * float(a[zB[r]])
        if (s.target - entry) * s.dir <= 0 or (entry - stop) * s.dir < 8 * rt:
            continue
        seg = slice(s.i + 1, min(len(base), s.i + 1 + COMBO_WAIT))
        touch = (base.l[seg] <= entry) if s.dir == 1 else (base.h[seg] >= entry)
        if not touch.any():
            continue
        k = s.i + 1 + int(np.argmax(touch))
        if (base.o[k] - stop) * s.dir <= 0:
            continue
        fill = min(base.o[k], entry) if s.dir == 1 else max(base.o[k], entry)
        tr = run_trade(base, k, s.dir, fill, stop, s.target, rt, slip, market=False)
        risk = (entry - stop) * s.dir  # size on the planned stop, as everywhere else
        tr["r_gross"] = (tr["r_gross"] * (fill - stop) * s.dir) / risk
        tr["cost_r"] = (rt + slip * (tr["exit_reason"] == "stop")) / risk
        tr["r"] = tr["r_gross"] - tr["cost_r"]
        busy_until = tr["t_exit"]
        out.append({"symbol": symbol, "variant": "donchian + IMB entry", "dir": s.dir, **tr})
    return pd.DataFrame(out)


def summ(t: pd.DataFrame) -> dict:
    t = t[~t.open_at_data_end.fillna(False).astype(bool)] if "open_at_data_end" in t else t
    t = t[~t.data_hole.fillna(False).astype(bool)] if "data_hole" in t else t
    if t.empty:
        return {"trades": 0}
    r = t.r.to_numpy()
    eq = np.cumsum(r)
    return {"trades": len(r), "win%": round(100 * float((t.exit_reason == "target").mean()), 1), "total_R": round(float(r.sum()), 1),
            "avg_R": round(float(r.mean()), 3), "gross_avg_R": round(float(t.r_gross.mean()), 3),
            "max_dd_R": round(float((np.maximum.accumulate(np.r_[0, eq])[1:] - eq).max()), 1)}


def _hist_signals(sym: str) -> tuple[str, pd.DataFrame]:
    return sym, signals(I.load_base(sym))[["t_signal", "dir"]]


def full_history() -> None:
    """Combination A on 2018-2026: imbalance H1/H4 pool trades (imb_wf pool, nearest-zone target) split by
    whether the latest Donchian signal in the prior 24h points the same way."""
    from concurrent.futures import ProcessPoolExecutor

    from .run import _low_priority
    _low_priority()
    with ProcessPoolExecutor(3, initializer=_low_priority) as ex:
        sig = dict(ex.map(_hist_signals, D.SYMBOLS))
    p = pd.read_csv(I.DEST / "wf_pool.csv.gz")
    p = p[(p.tmode == "near") & p.tf.isin(["H1", "H4"])].reset_index(drop=True)
    agree = np.zeros(len(p), bool)
    for sym, g in p.groupby("symbol"):
        s = sig[sym].sort_values("t_signal")
        ts, ds = s.t_signal.to_numpy(), s.dir.to_numpy()
        te = g.t_entry.to_numpy()
        j = np.maximum(np.searchsorted(ts, te, side="right") - 1, 0)
        agree[g.index] = (np.searchsorted(ts, te, side="right") > 0) & (te - ts[j] <= FILTER_WINDOW) & (ds[j] == g.dir.to_numpy())
    p["donchian_agrees"] = agree
    hole = np.zeros(len(p), bool)
    for sym, g in p.groupby("symbol"):
        hole[g.index] = D.overlaps_hole(D.holes(I.load_base(sym)), g.t_entry.to_numpy(), g.t_exit.to_numpy())
    print(f"excluded {int(hole.sum())} of {len(p)} history trades that overlap a missing-data hole")
    p = p[~hole].reset_index(drop=True)
    p["year"] = pd.to_datetime(p.t_entry, unit="s").dt.year
    p.to_csv(I.DEST / "combo_A_full_history.csv.gz", index=False)

    def s_(g: pd.DataFrame) -> str:
        r = g.r.to_numpy()
        return f"n={len(r):5d} avg {r.mean():+.3f}R t={r.mean() / r.std(ddof=1) * np.sqrt(len(r)):+.1f}" if len(r) > 2 else f"n={len(r)}"
    for tf in ("H1", "H4"):
        x = p[p.tf == tf]
        print(f"\n{tf}: all {s_(x)} | donchian agrees {s_(x[x.donchian_agrees])} | not {s_(x[~x.donchian_agrees])}")
        for y, g in x.groupby("year"):
            print(f"   {y}: all {s_(g)} | donchian agrees {s_(g[g.donchian_agrees])}")
    h = p[(p.tf == "H1") & p.donchian_agrees]
    print("\nH1 + donchian by symbol:", {k: round(v, 3) for k, v in h.groupby("symbol").r.mean().items()})
    print("H1 + donchian by dir:", {("buy" if k == 1 else "sell"): round(v, 3) for k, v in h.groupby("dir").r.mean().items()})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-08-01")
    ap.add_argument("--end", default="2026-10-01")
    ap.add_argument("--full-history", action="store_true", help="combination A over 2018-2026 instead")
    args = ap.parse_args()
    if args.full_history:
        return full_history()
    t0, t1 = (int(pd.Timestamp(x, tz="UTC").timestamp()) for x in (args.start, args.end))
    frames, sigs = [], {}
    for sym in D.SYMBOLS:
        base = live_base(sym)
        sig = signals(base)
        sigs[sym] = sig
        frames += [donchian_trades(sym, base, sig, t0, t1), imb_entry_trades(sym, base, sig, t0, t1)]
    # combination A: imbalance H1 trades (from imb_month runs) filtered by the latest Donchian direction
    imb = pd.concat([pd.read_csv(I.DEST / f"month_{m}" / "trades.csv") for m in pd.period_range(args.start, pd.Timestamp(args.end) - pd.Timedelta(days=1), freq="M").strftime("%Y-%m")
                     if (I.DEST / f"month_{m}" / "trades.csv").exists()], ignore_index=True)
    imb = imb[(imb.tf == "H1") & (imb.t_entry >= t0) & (imb.t_entry < t1)].copy()
    keep = []
    for r in imb.itertuples(index=False):
        s = sigs[r.symbol]
        prior = s[(s.t_signal <= r.t_entry) & (s.t_signal > r.t_entry - FILTER_WINDOW)]
        keep.append(len(prior) > 0 and int(prior.dir.iloc[-1]) == r.dir)
    imb["variant"] = "imbalance H1 (video rules)"
    filt = imb[np.array(keep, bool)].assign(variant="imbalance H1 + donchian direction")
    t = pd.concat(frames + [imb, filt], ignore_index=True)
    t["data_hole"] = False
    for sym in D.SYMBOLS:  # trades crossing a missing-data span cannot be scored honestly
        m = (t.symbol == sym).to_numpy()
        t.loc[m, "data_hole"] = D.overlaps_hole(D.holes(live_base(sym)), t.t_entry.to_numpy()[m], t.t_exit.to_numpy()[m])
    print("excluded (missing-data hole):", t[t.data_hole].groupby("variant", sort=False).size().to_dict())
    t.to_csv(I.DEST / f"donchian_combo_{args.start}_{args.end}.csv", index=False)
    rep = {}
    for v, g in t.groupby("variant", sort=False):
        rep[v] = {"all": summ(g), "by_month": {m: summ(x) for m, x in g.groupby(pd.to_datetime(g.t_entry, unit="s").dt.strftime("%Y-%m"))},
                  "by_symbol": {s: summ(x)["total_R"] if summ(x)["trades"] else 0 for s, x in g.groupby("symbol")},
                  "by_dir": {("buy" if d == 1 else "sell"): summ(x) for d, x in g.groupby("dir")}}
    (OUT / f"donchian_combo_{args.start}_{args.end}.json").write_text(json.dumps(rep, indent=1))
    for v, x in rep.items():
        print(f"\n== {v}\n  all: {x['all']}\n  by month: { {m: (y['trades'], y.get('total_R')) for m, y in x['by_month'].items()} }"
              f"\n  by symbol R: {x['by_symbol']}\n  by dir: { {k: (y['trades'], y.get('total_R')) for k, y in x['by_dir'].items()} }")


if __name__ == "__main__":
    main()
