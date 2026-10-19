"""What happens AFTER entry: excursions and alternative exits for a 5-year trade file (fib618 base by default).

    python -m bensim.trade_path [--trades data/fib618_base_5y.csv]

Per trade, from M5 bars between entry and the original exit: MFE (best move for us, in R) before the exit and MAE.
Alternative exits replayed on the same path (a bar touching stop and target = stop; costs = the trade's own
spread + commission in R): fixed targets 1 / 1.5 / 2R, break-even after +1R, 50% off at +1R (rest as before).
Only exits that end at or before the original exit are simulated, so no bars beyond the file are needed.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from .backtest import load_m5


def paths(t: pd.DataFrame) -> pd.DataFrame:
    out = []
    for s, g in t.groupby("symbol"):
        m = load_m5(s, "2021-10-01", None)
        T, H, L = m["t"], m["high"], m["low"]
        for i, x in g.iterrows():
            a, b = np.searchsorted(T, x.t_entry), np.searchsorted(T, x.t_exit, side="right")
            risk = abs(x.entry - x.sl)
            fav = ((H[a:b] - x.entry) if x.dir > 0 else (x.entry - L[a:b])) / risk  # per bar, best for us
            adv = ((x.entry - L[a:b]) if x.dir > 0 else (H[a:b] - x.entry)) / risk  # per bar, worst for us
            if not len(fav):
                fav = adv = np.zeros(1)
            cost = x.spread_pips_entry / x.risk_pips + x.commission_r
            row = {"i": i, "mfe": fav.max(), "mae": adv.max(), "cost": cost}
            for k in (1.0, 1.5, 2.0):  # fixed target k R: first bar reaching k (stop first if the same bar or earlier)
                hit_t = np.flatnonzero(fav >= k)
                hit_s = np.flatnonzero(adv >= 1.0)
                if len(hit_t) and (not len(hit_s) or hit_t[0] < hit_s[0]):
                    row[f"tgt{k:g}"] = k - cost
                else:
                    row[f"tgt{k:g}"] = x.r if (x.rr_planned <= k or not len(hit_s)) else -1 - cost
            hit1 = np.flatnonzero(fav >= 1.0)
            if len(hit1):
                after = adv[hit1[0] + 1:]
                # stopped at entry when price comes back to it after +1R -- winners too (a bar touching entry and
                # the target counts as the stop, conservative)
                row["be1"] = -cost if (after >= 0).any() else x.r
                row["half1"] = 0.5 * (1 - cost) + 0.5 * x.r
            else:
                row["be1"] = row["half1"] = x.r
            out.append(row)
    return pd.DataFrame(out).set_index("i").loc[t.index]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="data/fib618_base_5y.csv")
    a = ap.parse_args()
    t = pd.read_csv(a.trades).sort_values("t_entry").reset_index(drop=True)
    t["year"] = pd.to_datetime(t.t_entry, unit="s").dt.year
    t = t.join(paths(t))
    t["liq1"] = t.chk_liq_ahead_r.fillna(99) >= 1
    los, win = t[t.r < 0], t[t.r > 0]
    print(f"{a.trades}: {len(t)} trades, {len(win)} winners, {len(los)} losers")
    print(f"LOSERS: went >=+0.5R first {np.mean(los.mfe >= .5) * 100:.0f}%, >=+1R {np.mean(los.mfe >= 1) * 100:.0f}%, "
          f">=+1.5R {np.mean(los.mfe >= 1.5) * 100:.0f}%; median MFE {los.mfe.median():.2f}R")
    print(f"WINNERS: went <=-0.5R first {np.mean(win.mae >= .5) * 100:.0f}%, <=-0.8R {np.mean(win.mae >= .8) * 100:.0f}%; "
          f"median MAE {win.mae.median():.2f}R; median planned RR {t.rr_planned.median():.2f}")
    exits = {"as is (planned target)": "r", "target 1R": "tgt1", "target 1.5R": "tgt1.5", "target 2R": "tgt2",
             "break-even after +1R": "be1", "50% off at +1R": "half1"}
    for label, sub in (("ALL trades", t), ("only liquidity >=1R ahead", t[t.liq1])):
        print(f"\n== {label} ({len(sub)} trades)   avg R per year {sorted(sub.year.unique())}")
        for name, col in exits.items():
            ys = sub.groupby("year")[col].mean()
            eq = sub[col].cumsum()
            print(f"  {name:24} avg {sub[col].mean():+.3f}R  total {sub[col].sum():+7.1f}R  DD {(eq.cummax() - eq).max():5.1f}R  "
                  f"win {np.mean(sub[col] > 0) * 100:4.1f}%  years+ {(ys > 0).sum()}/{len(ys)}  " + " ".join(f"{v:+.2f}" for v in ys))


if __name__ == "__main__":
    main()
