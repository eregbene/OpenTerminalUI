"""Pattern analysis of the live strategies' trades (user ask 2026-10-22 after the XAUUSD imbalance trade).

    python -m bensim.pattern_check [start]          # default 3 months

Per trade (fib618, imbalance book, Bennett -- the ones whose setups carry checks): every setup check, H4/H1/M15
agreement, MFE / MAE in R (max in favour / against before the exit), whether it reached the first liquidity the
setup marked (liq_ahead_r), and the result. Then R by each check, and the same trades under three exits:
as traded / close ALL at the first liquidity / close HALF there and let the rest run as traded.
Look-only: nothing here is tuned or switched on; a pattern must hold in both halves to be worth a real test.
"""
from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import bennett_strategy as bstrat
from . import books
from .backtest import Exec, load_m5, run_symbol
from .exit_check import _tf_trend
from .live import LIVE_SYMBOLS
from .weekly_check import LIVE as FIB_LIVE

START, END = (sys.argv[1] if len(sys.argv) > 1 else "2026-07-22"), "2026-10-22"
BOOKS = {"fib618": (LIVE_SYMBOLS, FIB_LIVE, "imbalance"), "imbalance": (books.SYMBOLS, books.IMB, "imbalance"),
         "Bennett": (bstrat.LIVE_SYMBOLS, bstrat.LIVE, "bennett")}


def path(args) -> pd.DataFrame:
    sym, tr = args
    m5 = load_m5(sym, (pd.Timestamp(START) - pd.Timedelta(days=90)).isoformat(), END)
    tfs = [_tf_trend(m5, s) for s in (14400, 3600, 900)]
    at = lambda tf, now: int(tf[1][max(0, np.searchsorted(tf[0], now, side="right") - 1)])  # noqa: E731
    rows = []
    for x in tr.itertuples():
        d, risk = x.dir, abs(x.entry - x.sl)
        k, xi = int(np.searchsorted(m5["t"], x.t_entry - 300)), int(np.searchsorted(m5["t"], x.t_exit - 300))
        last = xi if x.exit_reason != "stop" else xi - 1  # the stop bar is ambiguous: don't credit it
        hi, lo = m5["high"][k + 1:last + 1], m5["low"][k + 1:last + 1]
        mfe = ((hi.max() - x.entry) if d == 1 else (x.entry - lo.min())) / risk if len(hi) else 0.0
        mae = ((x.entry - lo.min()) if d == 1 else (hi.max() - x.entry)) / risk if len(hi) else 0.0
        agree = sum(at(tf, int(x.t_entry)) == d for tf in tfs)
        rows.append({"mfe": mfe, "mae": mae, "agree": agree, "hours": (x.t_exit - x.t_entry) / 3600})
    return tr.reset_index(drop=True).join(pd.DataFrame(rows))


def stat(r: pd.Series) -> str:
    t = r.mean() / r.std() * np.sqrt(len(r)) if len(r) > 2 and r.std() > 0 else 0.0
    return f"n {len(r):4}  win {(r > 0).mean():4.0%}  avg {r.mean():+.3f}R  total {r.sum():+6.1f}R  t {t:+.1f}"


def main() -> None:
    yrs = pd.date_range(START, END, freq="365D").strftime("%Y-%m-%d").tolist() + [END]
    jobs, names = [], []
    for name, (syms, p, kind) in BOOKS.items():
        for s in syms:
            for a, b in zip(yrs[:-1], yrs[1:]):
                jobs.append((s, a, b, p, Exec(), kind))
                names.append(name)
    with ProcessPoolExecutor(3) as pool:
        res = list(pool.map(run_symbol, jobs))
    per: dict[tuple, list] = {}
    for n, j, t in zip(names, jobs, res):
        if not t.empty:
            per.setdefault((j[0], n), []).append(t.assign(book=n))
    with ProcessPoolExecutor(3) as pool:
        tr = pd.concat(list(pool.map(path, [(s, pd.concat(v)) for (s, _), v in per.items()])), ignore_index=True)
    tr["liq"] = pd.to_numeric(tr.get("chk_liq_ahead_r"), errors="coerce")
    hit = tr.liq.notna() & (tr.mfe >= tr.liq)
    tr["r_liq_all"] = np.where(hit, tr.liq - tr.commission_r, tr.r)
    tr["r_liq_half"] = np.where(hit, 0.5 * (tr.liq - tr.commission_r) + 0.5 * tr.r, tr.r)
    tr.to_csv(f"logs/pattern_check_{START}.csv", index=False)

    print(f"{START} .. {END}: {len(tr)} trades")
    for name, g in [("ALL", tr)] + list(tr.groupby("book")):
        print(f"\n### {name}: {stat(g.r)}   median MFE {g.mfe.median():.2f}R, losers that were +1R first: "
              f"{int(((g.r < 0) & (g.mfe >= 1)).sum())}/{int((g.r < 0).sum())}")
        print(f"   exits  as traded {g.r.sum():+6.1f}R | close ALL at 1st liquidity {g.r_liq_all.sum():+6.1f}R | "
              f"HALF at 1st liquidity {g.r_liq_half.sum():+6.1f}R")
        print("   H4/H1/M15 agree: " + " | ".join(f"{a}: {stat(g.r[g.agree == a])}" for a in range(4) if (g.agree == a).any()))
        for col in sorted(c for c in g.columns if c.startswith("chk_")):
            v = g[col]
            if v.dropna().empty or col in ("chk_liq_ahead_r", "chk_weak_extreme_r", "chk_fib_pos"):
                continue
            vals = v.astype(str)
            if vals.nunique() > 6 or vals.nunique() < 2:
                continue
            print(f"   {col[4:]:15} " + " | ".join(f"{val}: {stat(g.r[vals == val])}" for val in sorted(vals.unique())))
        if g.liq.notna().any():
            b = pd.cut(g.liq, [0, 1, 2, 3, 5, 99])
            print("   first liquidity at: " + " | ".join(f"{iv}: n{len(x)} avg{x.r.mean():+.2f}R" for iv, x in g.groupby(b, observed=True)))


if __name__ == "__main__":
    main()
