"""Judge Phantom's rules on the setup database (bensim.phantom): each rule's KEPT vs DROPPED zones, on the discovery
period only; the confirm period is looked at once, for the final rule set.

    python -m bensim.phantom_report data/phantom_setups.csv [--split 2025-07-01] [--confirm]
All R values are net of commission + slippage (spread is in the fills).
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

OUT = ["conf_2R_r", "conf_3R_r", "conf_5R_r", "conf_prevhigh_r", "conf_trail_r", "risk_2R_r", "risk_3R_r", "risk_prevhigh_r", "risk_trail_r"]


def stat(r: pd.Series) -> str:
    r = r.dropna()
    if len(r) < 5:
        return f"{len(r):4d}         -"
    t = r.mean() / r.std(ddof=1) * np.sqrt(len(r)) if r.std(ddof=1) > 0 else 0.0
    return f"{len(r):4d} {r.mean():+.3f} t{t:+5.1f}"


def rules(df: pd.DataFrame) -> dict[str, pd.Series]:
    """Each Phantom rule as a boolean mask (True = the rule keeps the zone). Section numbers = docs/phantom_trading.md."""
    with_d1 = df.d1_trend == df.dir
    return {
        "reversal zone [7]": df.model == "reversal",
        "first continuation only [7]": (df.model == "reversal") | (df.continuation_n <= 1),
        "slow return (ratio >= 1) [8]": df.return_ratio >= 1.0,
        "no V return (ratio >= 0.5) [8]": df.return_ratio >= 0.5,
        "corrective return (>= 2 swings) [8b]": df.return_swings >= 2,
        "impulsive last leg [8b]": df.last_leg_atr >= 1.5,
        "slow return + impulsive last leg [8b]": (df.return_ratio >= 1.0) & (df.last_leg_atr >= 1.5),
        "daily trend agrees [1,11]": with_d1,
        "4H trend agrees": df.h4_trend == df.dir,
        "4H zones only [4,11]": df.zone_tf == "4H",
        "departure left an imbalance [2,3]": df.imbalances >= 1,
        "strong departure (>= 2 ATR) [6]": df.depart_atr >= 2.0,
        "zone not too large (<= 1 ATR) [2]": df.zone_atr <= 1.0,
        "zone not formed in Asia [8]": ~df.zone_hour.between(0, 6),
        "returned within 5 days": df.wait_h <= 120,
    }


def table(df: pd.DataFrame, title: str) -> None:
    print(f"\n=== {title}   (cells: n  avg R  t)")
    print(f"{'':42}" + "".join(f"{c.replace('_r', ''):>20}" for c in OUT))
    print(f"{'ALL zones':42}" + "".join(f"{stat(df[c]):>20}" for c in OUT))
    for name, m in rules(df).items():
        for keep in (True, False):
            mm = m == keep
            print(f"{name + (' keeps' if keep else ' drops'):42}" + "".join(f"{stat(df.loc[mm, c]):>20}" for c in OUT))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--split", default="2025-07-01")
    a = ap.parse_args()
    df = pd.read_csv(a.csv)
    cut = pd.Timestamp(a.split, tz="UTC").timestamp()
    disc = df[df.created < cut]
    print(f"{len(df)} zones; discovery {len(disc)} (< {a.split}), confirm {len(df) - len(disc)}")
    table(disc, "DISCOVERY")
    print("\n--- per pair (discovery): conf_3R | conf_trail | risk_3R")
    for s, g in disc.groupby("symbol"):
        print(f"  {s:8} {stat(g.conf_3R_r):>20} {stat(g.conf_trail_r):>20} {stat(g.risk_3R_r):>20}")


if __name__ == "__main__":
    main()
