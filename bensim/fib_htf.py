"""fib618 on bigger bars and on many more pairs / years -- the two decisive tests of 2026-10-14.

    python -m bensim.fib_htf --tfs H4,D1 --source db                 # our 7 pairs (ICMarkets M5), 2021-10 .. now
    python -m bensim.fib_htf --tfs H4,D1 --source fxcm_h1            # 25 FXCM pairs, H1 bars, 2012 .. now
    python -m bensim.fib_htf --tfs M15 --source fxcm_m5              # fib618 as live (M15), 25 pairs, 2012 .. now

Same strategy code as the live engine (bensim.strategy, zone_mode="fib", no confirmations = the 5y "base"); only
the bar size changes: M15 = entry M15 / bias H1 (live), H4 = entry H4 / bias D1, D1 = entry D1 / bias D1. Channel,
plan and hold lengths count bars of the entry timeframe, as in the live strategy. Weekend (Sat/Sun UTC) bars are
dropped for H4/D1 so no Sunday-evening stub candle enters the daily series.
FXCM sources: costs = that pair's median real ask-bid spread (no extra commission: FXCM spreads are retail, already
wider than raw + $7/lot); crosses get a USD conversion through their quote currency (commission-free, so sizing only).
Nothing tuned; nothing touches the live engine.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import backtest as bt
from .config import SYMBOLS
from .strategy import Params, pip

TF = {"M15": (900, 3600), "H4": (14400, 86400), "D1": (86400, 86400)}


def _patch(source: str, drop_weekend: bool) -> None:
    """Point the backtester's data loader at FXCM files (in this process)."""
    from . import fxcm_history as fh
    for s in fh.PAIRS:
        bt.CURRENCIES.setdefault(s, (s[:3], s[3:]))
    orig = bt.load_m5

    def load(sym: str, start: str | None, end: str | None) -> dict[str, np.ndarray]:
        if source == "db":
            m = orig(sym, start, end)
        else:
            z = fh.load(sym, "M5" if source == "fxcm_m5" else "H1")
            lo = pd.Timestamp(start, tz="UTC").timestamp() if start else -np.inf
            hi = pd.Timestamp(end, tz="UTC").timestamp() if end else np.inf
            k = (z["t"] >= lo) & (z["t"] < hi)
            m = {c: z[c][k] for c in ("t", "open", "high", "low", "close")}
        if drop_weekend:
            wd = ((m["t"] // 86400) + 3) % 7  # 0 = Monday
            keep = wd < 5
            m = {c: v[keep] for c, v in m.items()}
        return m
    bt.load_m5 = load


def _job(args: tuple) -> pd.DataFrame:
    source, tf, sym, start, end, ex = args
    _patch(source, tf != "M15")
    es, bs = TF[tf]
    p = Params(zone_mode="fib", entry_sec=es, bias_sec=bs)
    t = bt.run_symbol((sym, start, end, p, ex, "imbalance"))
    return t.assign(tf=tf, source=source) if len(t) else t


def _exec(source: str, syms: list[str]) -> dict[str, bt.Exec]:
    if source == "db":
        return {s: bt.Exec() for s in syms}
    from . import fxcm_history as fh
    out = {}
    for s in syms:
        spr = float(np.median(fh.load(s, "M5" if source == "fxcm_m5" else "H1")["spr"])) / pip(s)
        out[s] = bt.Exec(raw_spread_pips={s: spr}, commission_per_lot_rt=0.0)
    return out


def report(t: pd.DataFrame) -> None:
    t = t.assign(year=pd.to_datetime(t.t_entry, unit="s").dt.year)
    for (src, tf), g in t.groupby(["source", "tf"], sort=False):
        print(f"\n== {tf} bars, source {src}: {g.symbol.nunique()} pairs")
        for name, x in (("ALL", g), ("untouched <= 2021-09", g[g.t_entry < pd.Timestamp("2021-10-01", tz="UTC").timestamp()]),
                        ("recent 2021-10 ..", g[g.t_entry >= pd.Timestamp("2021-10-01", tz="UTC").timestamp()])):
            if x.empty:
                continue
            r = x.sort_values("t_exit").r.to_numpy()
            eq = np.cumsum(r)
            pf = r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9)
            ys = x.groupby("year").r.mean()
            print(f"  {name:22} {len(r):6} tr win {np.mean(r > 0) * 100:4.1f}% avg {r.mean():+.3f}R PF {pf:4.2f} total {r.sum():+8.1f}R "
                  f"maxDD {(np.maximum.accumulate(eq) - eq).max():6.1f}R t {r.mean() / r.std() * np.sqrt(len(r)):+.2f} years+ {(ys > 0).sum()}/{len(ys)}")
        ys = g.groupby("year").r.agg(["count", "mean"])
        print("  per year: " + " ".join(f"{y}:{m:+.2f}({n})" for y, (n, m) in ys.iterrows()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tfs", default="H4,D1")
    ap.add_argument("--source", choices=["db", "fxcm_h1", "fxcm_m5"], default="db")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--workers", type=int, default=2)
    a = ap.parse_args()
    if a.source == "db":
        syms, start = SYMBOLS, a.start or "2021-11-15"
    else:
        from .fxcm_history import OUT
        syms = sorted(f.stem.split("_")[0] for f in OUT.glob(f"*_{'M5' if a.source == 'fxcm_m5' else 'H1'}.npz"))
        start = a.start or "2012-09-01"  # after warm-up of the 2012 download
    ex = _exec(a.source, syms)
    jobs = [(a.source, tf, s, start, a.end, ex[s]) for tf in a.tfs.split(",") for s in syms]
    with ProcessPoolExecutor(a.workers) as pool:  # 2 workers: the live engine keeps the CPU
        frames = [f for f in pool.map(_job, jobs) if len(f)]
    t = pd.concat(frames, ignore_index=True)
    out = f"data/fib_htf_{a.source}_{a.tfs.replace(',', '_')}.csv"
    t.to_csv(out, index=False)
    print(f"{len(t)} trades -> {out}")
    report(t)


if __name__ == "__main__":
    main()
