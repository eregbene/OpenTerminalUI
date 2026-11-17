"""Combined book: rate-aware ML model (bensim.macro_yields ensemble) + EURUSD European-morning short, risk-sized.

    python -m bensim.combo

Fixed before running (2026-10-14):
  stream 1  data/macro/ensemble_trades.csv (walk-forward 2016-now, 5 pairs, 6h holds, bp net of real spread)
  stream 2  EURUSD short 09:00 -> 12:00 UTC every weekday, FXCM M5 MID prices (bid close + half the real spread), paying
            half the real spread at entry (08:55 bar close) and at exit (11:55 bar close); 2016-now. Note: this window was
            FOUND on 2012-2019 data, so its 2016-2019 part is in-sample; 2020-now is the clean part.
  sizing    every trade gets the same dollar risk: notional = R$ / (typical move of its stream, sd in bp / 1e4).
            R$ is then chosen so the COMBINED book has ~10% annual volatility on 100k (reported, plus 5% and 15%).
  results   daily $ P&L of each stream and the sum: per year, Sharpe, worst drawdown, stream correlation, lots per trade.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .fxcm_history import load


def morning() -> pd.DataFrame:
    m = load("EURUSD", "M5")
    t = pd.to_datetime(m["t"], unit="s")
    df = pd.DataFrame({"t": t, "mid": m["close"] + m["spr"] / 2, "spr_bp": m["spr"] / m["close"] * 1e4})
    df = df[(df.t.dt.dayofweek < 5) & (df.t.dt.year >= 2016)]
    df["d"] = df.t.dt.normalize()
    ent = df[(df.t.dt.hour == 8) & (df.t.dt.minute == 55)].set_index("d")
    ext = df[(df.t.dt.hour == 11) & (df.t.dt.minute == 55)].set_index("d")
    j = ent.join(ext, lsuffix="_in", rsuffix="_out", how="inner")
    bp = -np.log(j.mid_out / j.mid_in) * 1e4 - (j.spr_bp_in + j.spr_bp_out) / 2  # SHORT, net of real costs
    return pd.DataFrame({"day": j.index, "bp": bp.to_numpy(), "stream": "EURUSD morning"})


def main() -> None:
    ml = pd.read_csv("data/macro/ensemble_trades.csv", parse_dates=["t"])
    ml = pd.DataFrame({"day": ml.t.dt.normalize(), "bp": ml.bp, "stream": "rate-aware ML", "symbol": ml.symbol})
    mo = morning()
    sd = {"rate-aware ML": ml.bp.std(), "EURUSD morning": mo.bp.std()}
    print(f"streams: ML {len(ml)} trades (sd {sd['rate-aware ML']:.1f} bp, avg {ml.bp.mean():+.2f}), "
          f"EURUSD morning {len(mo)} days (sd {sd['EURUSD morning']:.1f} bp, avg {mo.bp.mean():+.2f})")
    for name, d in (("ML", ml), ("morning", mo)):
        ys = d.groupby(d.day.dt.year).bp.mean()
        print(f"  {name} per year avg bp: " + " ".join(f"{y}:{v:+.2f}" for y, v in ys.items()))
    # $ per trade with 1 unit of risk (R$ = 1): bp / sd  -> each trade contributes in "risk units"
    tr = pd.concat([ml.assign(u=ml.bp / sd["rate-aware ML"]), mo.assign(u=mo.bp / sd["EURUSD morning"])], ignore_index=True)
    daily = tr.pivot_table(index="day", columns="stream", values="u", aggfunc="sum").fillna(0)
    daily["book"] = daily.sum(axis=1)
    corr = daily["rate-aware ML"].corr(daily["EURUSD morning"])
    print(f"\ndaily correlation between the two streams: {corr:+.3f}")
    ann = {c: daily[c].std() * np.sqrt(252) for c in daily}
    for c in daily:
        print(f"  {c:16} Sharpe {daily[c].mean() / daily[c].std() * np.sqrt(252):.2f}")
    for target in (0.05, 0.10, 0.15):
        r_usd = target * 100_000 / ann["book"]  # dollars of risk per trade (1 sd of its move)
        usd = daily * r_usd
        eq = 100_000 + usd["book"].cumsum()
        dd = (eq.cummax() - eq).max()
        yr = usd.groupby(usd.index.year).sum()
        post = usd.loc[usd.index >= "2021-01-01", "book"]
        lots_ml = r_usd / (sd["rate-aware ML"] / 1e4) / 100_000
        lots_mo = r_usd / (sd["EURUSD morning"] / 1e4) / 100_000
        print(f"\nTARGET {target:.0%} annual volatility on 100k -> ${r_usd:,.0f} risk (1 sd) per trade = "
              f"{lots_ml:.2f} lots per ML trade, {lots_mo:.2f} lots per morning trade")
        print(f"  avg year {yr.book.mean():+,.0f}$ | since 2021 avg year {post.groupby(post.index.year).sum().mean():+,.0f}$ | "
              f"worst drawdown {dd:,.0f}$ ({dd / 1000:.1f}%) | worst day {usd.book.min():,.0f}$ | years+ {(yr.book > 0).sum()}/{len(yr)}")
        print("  per year: " + " ".join(f"{y}:{v / 1000:+.1f}k" for y, v in yr.book.items()))
        print("  of which ML / morning: " + " ".join(f"{y}:{a / 1000:+.1f}/{b / 1000:+.1f}" for y, a, b in
                                                     zip(yr.index, yr["rate-aware ML"], yr["EURUSD morning"])))


if __name__ == "__main__":
    main()
