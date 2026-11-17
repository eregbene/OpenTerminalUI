"""Currency clock: each currency's own hour-of-day habit, measured on the past, traded through the cheapest pair.

    python -m bensim.currency_clock

Rules fixed BEFORE the first run (2026-10-14), data = bensim.edge_miner cache (25 FXCM pairs, mid prices, real spreads):
  1. currency move per hour: for every currency c and hour t, the mean over all pairs containing c of the NEXT hour's
     mid return, signed so that + = c strengthens (c vs the basket).
  2. DISCOVERY 2012-2017: per (currency, UTC hour) the mean bp and a t-stat over days; keep effects with |t| > 3.5 and
     the same sign in >= 5 of the 6 years. VALIDATION 2018-2019: keep only those with the same sign again.
  3. LOCKED TEST 2020-now, every weekday hour: predicted pair move = effect(base, hour) - effect(quote, hour) (0 for
     currencies without a kept effect); trade the ONE pair with the largest |prediction| minus its real round-trip cost
     at that hour, if > 0; hold one hour; P&L = next-hour mid return in the predicted direction - real cost.
  Reported: kept effects, test bp per trade, t, years and pairs, and the same with the effects' signs randomly flipped
  (placebo).
"""
from __future__ import annotations

import pickle

import numpy as np
import pandas as pd

from .edge_miner import CCY, DISC, PATH, VAL


def main() -> None:
    with open(PATH, "rb") as fh:  # our own cache from edge_miner build
        f = pickle.load(fh)
    f = f[["t", "symbol", "hour", "year", "long_1", "short_1", "spr"]].dropna()
    f["fwd"] = (f.long_1 - f.short_1) / 2  # next-hour mid move in bp (cost removed)
    f["cost"] = -(f.long_1 + f.short_1) / 2  # real round-trip cost at that hour, bp
    rows = []
    for c in CCY:
        sub = f[f.symbol.str.contains(c)]
        sgn = np.where(sub.symbol.str[:3] == c, 1.0, -1.0)
        cm = (sub.fwd * sgn).groupby(sub.t).mean()
        rows.append(pd.DataFrame({"t": cm.index, "ccy": c, "move": cm.to_numpy()}))
    cur = pd.concat(rows)
    cur["hour"], cur["year"], cur["day"] = cur.t.dt.hour, cur.t.dt.year, cur.t.dt.floor("D")
    disc, val = cur[cur.year <= DISC], cur[(cur.year > DISC) & (cur.year <= VAL)]
    eff = []
    for (c, h), g in disc.groupby(["ccy", "hour"]):
        m, t = g.move.mean(), g.move.mean() / g.move.std() * np.sqrt(len(g))
        yrs = g.groupby("year").move.mean()
        v = val[(val.ccy == c) & (val.hour == h)].move.mean()
        eff.append({"ccy": c, "hour": h, "disc_bp": m, "disc_t": t, "same_sign_years": int((np.sign(yrs) == np.sign(m)).sum()), "val_bp": v})
    e = pd.DataFrame(eff)
    kept = e[(e.disc_t.abs() > 3.5) & (e.same_sign_years >= 5) & (np.sign(e.val_bp) == np.sign(e.disc_bp))]
    pd.set_option("display.width", 200)
    print(f"{len(e)} currency-hour effects measured; {len(kept)} kept (discovery |t|>3.5, >=5/6 years, validation same sign):")
    print(kept.sort_values(["ccy", "hour"]).round(2).to_string(index=False))
    table = {(r.ccy, r.hour): r.disc_bp for r in kept.itertuples()}

    def run(tab: dict) -> pd.DataFrame:
        test = f[f.year > VAL]
        base, quote = test.symbol.str[:3].to_numpy(), test.symbol.str[3:].to_numpy()
        hrs = test.hour.to_numpy()
        pred = np.array([tab.get((b, h), 0.0) - tab.get((q, h), 0.0) for b, q, h in zip(base, quote, hrs)])
        x = test.assign(pred=pred, edge=np.abs(pred) - test.cost.to_numpy())
        x = x[(x.pred != 0) & (x.edge > 0)]
        x = x.loc[x.groupby("t").edge.idxmax()]  # one trade per hour: the best pair
        return x.assign(pnl=np.sign(x.pred) * x.fwd - x.cost)

    tr = run(table)

    def show(t: pd.DataFrame, label: str) -> None:
        if t.empty:
            print(f"  {label}: no trades")
            return
        ys = t.groupby("year").pnl.sum()
        pp = t.groupby("symbol").pnl.mean()
        print(f"  {label:34} {len(t):6} trades avg {t.pnl.mean():+.2f} bp t {t.pnl.mean() / t.pnl.std() * np.sqrt(len(t)):+.2f} "
              f"total {t.pnl.sum():+.0f} bp years+ {(ys > 0).sum()}/{len(ys)} pairs+ {(pp > 0).sum()}/{len(pp)}")
        print("     per year (bp): " + " ".join(f"{y}:{v:+.0f}" for y, v in ys.items()))

    print("\nLOCKED TEST 2020-now:")
    show(tr, "currency clock")
    print("     trades by pair: " + ", ".join(f"{k} {v}" for k, v in tr.symbol.value_counts().head(8).items()))
    rng = np.random.default_rng(0)
    pl = []
    for _ in range(50):
        flip = {k: v * rng.choice([-1, 1]) for k, v in table.items()}
        p = run(flip)
        pl.append(p.pnl.mean() if len(p) else np.nan)
    print(f"  PLACEBO (random signs, 50 runs): avg bp/trade median {np.nanmedian(pl):+.2f}, best {np.nanmax(pl):+.2f}")
    tr.to_csv("data/currency_clock_test_trades.csv", index=False)


if __name__ == "__main__":
    main()
