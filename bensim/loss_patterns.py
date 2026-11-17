"""Where do the losses come from? 5-year breakdown of the signal-search trades by market condition.

    python -m bensim.loss_patterns          # needs data/signal_search.pkl (python -m bensim.signal_search build)

Two populations: (1) every H1 close, long AND short (what a blind entry earns in each condition), (2) the direction
model's own walk-forward trades (top 3%). Per condition: trades, avg R, win %, and in how many years 2022-2026 the
condition was positive -- a pattern only counts if it holds year after year. Conditions are read in the trade's
direction ("trend agrees" = H4 swing trend points the way of the trade, etc.).
"""
from __future__ import annotations

import pickle

import numpy as np
import pandas as pd

from .signal_search import PATH, run

YEARS = range(2022, 2027)


def _conditions(x: pd.DataFrame) -> dict[str, pd.Series]:
    d = x.dir
    hr = x.hour
    return {
        "session (UTC)": pd.cut(hr, [-1, 6, 11, 16, 20, 23], labels=["Asia 00-07", "London 07-12", "NY/overlap 12-17", "NY late 17-21", "close 21-24"]),
        "pair": x.symbol,
        "weekday": x.weekday.map(dict(enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]))),
        "H4 trend vs trade": np.sign(x.tr_h4 * d).map({1: "agrees", 0: "none", -1: "against"}),
        "D1 trend vs trade": np.sign(x.tr_d1 * d).map({1: "agrees", 0: "none", -1: "against"}),
        "last 24h move vs trade": pd.cut(x.ret_24 * d, [-np.inf, -2, -0.5, 0.5, 2, np.inf],
                                         labels=["big against (<-2 ATR)", "against", "flat", "with", "big with (>2 ATR)"]),
        "volatility (ATR / 100h avg)": pd.cut(x.atr_ratio, [0, 0.8, 1.0, 1.2, 1.5, np.inf], labels=["<0.8 quiet", "0.8-1.0", "1.0-1.2", "1.2-1.5", ">1.5 wild"]),
        "room to prev-day level ahead": pd.cut(np.where(d > 0, x.to_pdh, x.to_pdl), [-np.inf, 0, 1, 2, 4, np.inf],
                                               labels=["already beyond", "<1 ATR", "1-2 ATR", "2-4 ATR", ">4 ATR"]),
        "sweep last 3h vs trade": np.sign(x.sweep3 * d).map({1: "sweep for us", 0: "none", -1: "sweep against"}),
        "dollar (24h, other pairs) vs trade": pd.cut(x.usd_24_pair * d, [-np.inf, -0.5, 0.5, np.inf], labels=["against", "neutral", "with"]),
    }


def table(x: pd.DataFrame, title: str) -> None:
    print(f"\n######## {title}: {len(x)} trades, avg {x.r.mean():+.3f}R, win {np.mean(x.r > 0) * 100:.1f}%")
    for name, key in _conditions(x).items():
        g = x.assign(k=key.astype(str)).groupby("k", observed=True)
        yr = x.assign(k=key.astype(str)).pivot_table(index="k", columns="year", values="r", aggfunc="mean", observed=True)
        rows = []
        for k, s in g.r:
            ys = yr.loc[k, [y for y in YEARS if y in yr.columns]].dropna()
            rows.append((k, len(s), s.mean(), np.mean(s > 0) * 100, f"{int((ys > 0).sum())}/{len(ys)}",
                         " ".join(f"{v:+.2f}" for v in ys)))
        print(f"\n  {name}")
        print(f"    {'':28} {'trades':>7} {'avg R':>7} {'win%':>5} {'yrs+':>5}   per year {list(YEARS)}")
        for k, n, m, w, yp, yy in sorted(rows, key=lambda r: r[2]):
            flag = "  <-- loses every year" if yp.startswith("0/") else ("  <-- positive every year" if yp.split("/")[0] == yp.split("/")[1] else "")
            print(f"    {k[:28]:28} {n:7} {m:+7.3f} {w:5.1f} {yp:>5}   {yy}{flag}")


def main() -> None:
    with open(PATH, "rb") as fh:
        f = pickle.load(fh)
    f = f[np.isfinite(f.r_long) & np.isfinite(f.r_short)]
    blind = pd.concat([f.assign(dir=1, r=f.r_long), f.assign(dir=-1, r=f.r_short)], ignore_index=True)
    table(blind[blind.year.isin(YEARS)], "BLIND entries, every H1 close, long and short")
    tt = run(0.03, diff=True)  # walk-forward 2023-2026, same as signal_search --diff
    m = tt.merge(f.drop(columns=["year"]), on=["t", "symbol"], how="left")
    table(m, "DIRECTION MODEL trades (top 3%, unseen years 2023-2026)")


if __name__ == "__main__":
    main()
