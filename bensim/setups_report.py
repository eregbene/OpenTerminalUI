"""Judge Bennett's rules on the setup database (bensim.setups): for each rule, the setups it KEEPS vs the ones it
THROWS AWAY, on the discovery years only (the confirm years stay untouched until a rule set is chosen).

    python -m bensim.setups_report data/setups_bennett_h1.csv [--split 2025-01-01] [--confirm "fvg,choch_plus"]

Outcome = R net of costs (spread + ~0.7 pip commission) for: tight stop -> fixed 3R target, wide stop -> fixed 3R,
and the engine's real exit (first liquidity paying >= 2.75R, simulated with costs; only where such a target exists).
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

COMMISSION_PIPS = 0.7
OUTCOMES = {"tight 3R": ("tight_r_fix3", "tight_stop_pips"), "wide 3R": ("wide_r_fix3", "wide_stop_pips"),
            "tight 2R": ("tight_r_fix2", "tight_stop_pips")}


def net(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for name, (r, stop) in OUTCOMES.items():
        out[name] = df[r] - (df["spread_pips"] + COMMISSION_PIPS) / df[stop]
    out["engine 2.75R+"] = df["tight_liq275_r"]  # already net, NaN where no liquidity target pays 2.75R
    return out


def stat(r: pd.Series) -> str:
    r = r.dropna()
    if len(r) < 2:
        return f"{len(r):4d}      -"
    t = r.mean() / r.std(ddof=1) * np.sqrt(len(r)) if r.std(ddof=1) > 0 else 0.0
    return f"{len(r):4d} {100 * (r > 0).mean():3.0f}% {r.mean():+.3f} t{t:+5.1f}"


def table(df: pd.DataFrame, nr: pd.DataFrame, title: str) -> None:
    cols = list(nr.columns)
    print(f"\n=== {title}   (each cell: n  win%  avg R  t)")
    print(f"{'':28}" + "".join(f"{c:>26}" for c in cols))
    print(f"{'ALL setups':28}" + "".join(f"{stat(nr[c]):>26}" for c in cols))
    for flag in sorted(c for c in df.columns if c.startswith("ok_")):
        for keep in (True, False):
            m = df[flag] == keep
            print(f"{flag[3:] + (' keeps' if keep else ' drops'):28}" + "".join(f"{stat(nr.loc[m, c]):>26}" for c in cols))


def buckets(df: pd.DataFrame, nr: pd.DataFrame, col: str, bins: list, out: str = "tight 3R") -> None:
    g = pd.cut(df[col], bins)
    print(f"\n--- {col} ({out})")
    for k, idx in df.groupby(g, observed=True).groups.items():
        print(f"  {str(k):22} {stat(nr.loc[idx, out])}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--split", default="2025-01-01", help="discovery before, confirm from this date")
    ap.add_argument("--confirm", default="", help="comma list of ok_ rules: report them ONCE on the confirm years")
    a = ap.parse_args()
    df = pd.read_csv(a.csv)
    df["year"] = pd.to_datetime(df.t, unit="s").dt.year
    cut = pd.Timestamp(a.split, tz="UTC").timestamp()
    disc = df[df.t < cut]
    nr = net(disc)
    print(f"{a.csv}: {len(df)} setups, discovery {len(disc)} (< {a.split}), confirm {len(df) - len(disc)}")
    table(disc, nr, f"DISCOVERY {pd.to_datetime(disc.t.min(), unit='s'):%Y-%m} .. {a.split}")
    print("\n--- per pair (tight 3R | engine 2.75R+)")
    for s, idx in disc.groupby("symbol").groups.items():
        print(f"  {s:8} {stat(nr.loc[idx, 'tight 3R'])}   | {stat(nr.loc[idx, 'engine 2.75R+'])}")
    print("\n--- per year (tight 3R)")
    for y, idx in disc.groupby("year").groups.items():
        print(f"  {y}     {stat(nr.loc[idx, 'tight 3R'])}")
    buckets(disc, nr, "hours_since_push", [-1, 2, 6, 12, 24, 48, 1e9])
    buckets(disc, nr, "fib_pos", [-9, 0.25, 0.5, 0.62, 0.79, 9])
    buckets(disc, nr, "tight_stop_pips", [0, 8, 12, 18, 25, 40, 1e9])
    buckets(disc, nr, "hour", [-1, 6, 10, 12, 16, 20, 24])
    if a.confirm:
        conf = df[df.t >= cut]
        m = np.ones(len(conf), bool)
        for r in a.confirm.split(","):
            m &= conf[f"ok_{r}"].to_numpy()
        nc = net(conf)
        print(f"\n=== CONFIRM ({a.split} ..), rules: {a.confirm}")
        for c in nc.columns:
            print(f"  {c:16} kept {stat(nc.loc[m, c])}   all {stat(nc[c])}")


if __name__ == "__main__":
    main()
