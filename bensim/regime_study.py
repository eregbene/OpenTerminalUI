"""Why did the edges stop? Year-by-year market properties of 25 FX pairs (FXCM H1, 2012-now) next to strategy results.

    python -m bensim.regime_study

Per pair and year (NY-close daily bars; H1 for intraday), then averaged over pairs:
  vol        annualised daily-return volatility
  shock%     share of days with |return| > 3 x the trailing 60-day sd (event shocks)
  ac1        lag-1 autocorrelation of daily returns (<0 mean-reverting, >0 trending)
  vr10       variance ratio of 10-day vs 1-day returns (>1 trends persist, <1 they revert)
  band_rev   after a close outside the Bollinger band (20,2): next-5-day move BACK toward the middle, in ATRs
             (>0 = stretches revert = what ParallaxFX bets on)
  brk_fol    after a close at a new 20-day high/low: next-10-day move in the SAME direction, in ATRs
             (>0 = breakouts follow through = what fib618 / trend following bet on)
  chop       intraday path / net daily move (sum |H1 change| / |day change|): higher = noisier days for M15 stops
  spr/ATR    median spread as % of the daily ATR (cost relative to the moves)
  corr       average pairwise correlation of daily returns across all pairs (one macro driver dominating)
Strategy columns (from files made earlier today, FXCM 25 pairs): ParallaxFX strict D1 avg R/leg, fib618 D1 and H4 avg R.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import parallax as px
from .fxcm_history import OUT, load


def pair_year(sym: str) -> tuple[pd.DataFrame, pd.Series]:
    m = load(sym, "H1")
    b = px.indicators(px.ny_bars({k: m[k] for k in ("t", "open", "high", "low", "close")}, 24))
    b = b[pd.to_datetime(b.t_close, unit="s").dt.dayofweek < 5].reset_index(drop=True)
    b["date"] = pd.to_datetime(b.t_close, unit="s").dt.normalize()
    c = b.close
    r = c.pct_change()
    tr = pd.concat([b.high - b.low, (b.high - c.shift()).abs(), (b.low - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(20).mean()
    fwd5, fwd10 = c.shift(-5) - c, c.shift(-10) - c
    out_lo, out_hi = c < b.lbb, c > b.ubb
    band_rev = pd.Series(np.where(out_lo, fwd5, np.where(out_hi, -fwd5, np.nan)) / atr)
    hi20, lo20 = b.high.rolling(20).max().shift(1), b.low.rolling(20).min().shift(1)
    brk = pd.Series(np.where(c > hi20, fwd10, np.where(c < lo20, -fwd10, np.nan)) / atr)
    shock = (r.abs() > 3 * r.rolling(60).std().shift(1)).astype(float)
    # intraday chop from H1: path length / |net move| per NY day
    h = pd.DataFrame({"t": m["t"], "c": m["close"]})
    ny = pd.to_datetime(h.t, unit="s", utc=True).dt.tz_convert("America/New_York")
    h["day"] = (ny + pd.Timedelta(hours=7)).dt.normalize().dt.tz_localize(None)
    g = h.groupby("day").c
    chop = (g.apply(lambda s: s.diff().abs().sum()) / (g.last() - g.first()).abs().replace(0, np.nan)).clip(upper=50)
    spr_atr = pd.Series(np.median(m["spr"]) / atr.to_numpy())
    df = pd.DataFrame({"year": b.date.dt.year, "r": r, "band_rev": band_rev, "brk_fol": brk, "shock": shock, "spr_atr": spr_atr})
    rows = []
    for y, d in df.groupby("year"):
        rr = d.r.dropna()
        v1, v10 = rr.var(), rr.rolling(10).sum()[9::10].var()
        rows.append({"year": y, "vol": rr.std() * np.sqrt(252), "shock%": d.shock.mean() * 100, "ac1": rr.autocorr(1),
                     "vr10": v10 / (10 * v1) if v1 else np.nan, "band_rev": d.band_rev.mean(), "brk_fol": d.brk_fol.mean(),
                     "spr/ATR%": d.spr_atr.median() * 100})
    yr = pd.DataFrame(rows).set_index("year")
    yr["chop"] = chop.groupby(chop.index.year).median()
    ret = pd.Series(r.to_numpy(), index=b.date)
    return yr.assign(symbol=sym), ret[~ret.index.duplicated(keep="last")]


def main() -> None:
    pairs = sorted(f.stem.split("_")[0] for f in OUT.glob("*_H1.npz"))
    frames, rets = [], {}
    for s in pairs:
        y, r = pair_year(s)
        frames.append(y)
        rets[s] = r
    t = pd.concat(frames)
    avg = t.drop(columns="symbol").groupby(level=0).mean()
    R = pd.DataFrame(rets)
    corr = {y: (lambda c: c.where(~np.eye(len(c), dtype=bool)).abs().stack().mean())(R[R.index.year == y].corr())
            for y in avg.index}
    avg["|corr|"] = pd.Series(corr)
    # strategy results per year (files from earlier runs)
    try:
        p = pd.read_csv("data/parallax_untouched.csv")
        p = p[(p.variant == "author") & (p.target == -100)]
        avg["ParallaxD1"] = p.groupby(pd.to_datetime(p.t_fill, unit="s").dt.year).r.mean()
    except FileNotFoundError:
        pass
    try:
        f = pd.read_csv("data/fib_htf_fxcm_h1_H4_D1.csv")
        for tf in ("D1", "H4"):
            g = f[f.tf == tf]
            avg[f"fib{tf}"] = g.groupby(pd.to_datetime(g.t_entry, unit="s").dt.year).r.mean()
    except FileNotFoundError:
        pass
    pd.set_option("display.width", 220)
    print(avg.round(3).to_string())
    pre, post = avg.loc[2013:2020], avg.loc[2021:2026]
    print("\nmean 2013-2020 vs 2021-2026:")
    print(pd.DataFrame({"2013-20": pre.mean(), "2021-26": post.mean()}).round(3).T.to_string())
    num = avg.drop(columns=[c for c in avg.columns if c.startswith(("Parallax", "fib"))])
    for col in [c for c in avg.columns if c.startswith(("Parallax", "fib"))]:
        cc = num.corrwith(avg[col]).round(2)
        print(f"\nyear-level correlation of {col} with each market property: " + ", ".join(f"{k} {v:+.2f}" for k, v in cc.items()))
    t.to_csv("data/regime_study_pair_year.csv")


if __name__ == "__main__":
    main()
