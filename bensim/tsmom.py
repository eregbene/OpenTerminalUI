"""FX time-series momentum (trend following) -- the textbook version of the literature, on 25 FXCM pairs 2012-now.

    python -m bensim.tsmom

Rules fixed before running (Moskowitz-Ooi-Pedersen 2012 style, nothing tuned):
  * month-end (NY 17:00 daily closes), for every pair: sign of the 12-month return (252 trading days) -> long / short
  * size: each pair targets the same risk, weight = 1 / (annualised 60-day volatility), whole book scaled so the
    portfolio would have ~10% annual volatility if pairs were uncorrelated (ex-ante, no look-ahead)
  * held one month; costs: the pair's median real FXCM ask-bid spread on every change of position (half spread each
    side of the traded notional); swap/carry NOT included (no rate data) -- a known gap, can cut either way.
Reports yearly returns, Sharpe, worst drawdown -- and the same with a 3-month lookback as the one variant the papers
also report.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import parallax as px
from .fxcm_history import OUT, load


def closes() -> tuple[pd.DataFrame, pd.Series]:
    cols, spreads = {}, {}
    for f in sorted(OUT.glob("*_H1.npz")):
        s = f.stem.split("_")[0]
        m = load(s, "H1")
        b = px.ny_bars({k: m[k] for k in ("t", "open", "high", "low", "close")}, 24)
        b = b[pd.to_datetime(b.t_close, unit="s").dt.dayofweek < 5]
        ser = pd.Series(b.close.to_numpy(), index=pd.to_datetime(b.t_close, unit="s").dt.normalize())
        cols[s] = ser[~ser.index.duplicated(keep="last")]  # a weekend stub can share a date with Friday's close
        spreads[s] = float(np.median(m["spr"] / m["close"]))  # spread as a fraction of price
    return pd.DataFrame(cols).sort_index().ffill(limit=3), pd.Series(spreads)


def backtest(px_: pd.DataFrame, spr: pd.Series, lookback: int) -> pd.Series:
    ret = px_.pct_change(fill_method=None)
    vol = ret.rolling(60).std() * np.sqrt(252)
    signal = np.sign(px_ / px_.shift(lookback) - 1)
    month_end = ret.index.to_series().groupby(ret.index.to_period("M")).max().values
    w = signal / vol
    w.loc[~ret.index.isin(month_end)] = np.nan
    w = w.ffill()  # set at month end, held through the next month
    w = w * (0.10 / np.sqrt(w.notna().sum(axis=1)).clip(lower=1)).to_numpy()[:, None]  # ~10% book vol if uncorrelated
    w = w.shift(1)  # decided at the close, earns from the next day
    gross = (w * ret).sum(axis=1)
    cost = (w.diff().abs() * spr / 2).sum(axis=1)
    return (gross - cost).dropna()


def report(r: pd.Series, label: str) -> None:
    r = r[r.index >= "2013-01-01"]
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    yr = (1 + r).groupby(r.index.year).prod() - 1
    print(f"{label}: CAGR {eq.iloc[-1] ** (252 / len(r)) - 1:+.1%}  Sharpe {r.mean() / r.std() * np.sqrt(252):.2f}  "
          f"max DD {dd:.1%}  years+ {(yr > 0).sum()}/{len(yr)}")
    print("   " + " ".join(f"{y}:{v:+.1%}" for y, v in yr.items()))


def main() -> None:
    c, spr = closes()
    print(f"{c.shape[1]} pairs, daily NY closes {c.index[0]:%Y-%m-%d} .. {c.index[-1]:%Y-%m-%d}; returns from 2013 (12-month warm-up)")
    for lb, name in ((252, "TSMOM 12-month (the main spec)"), (63, "TSMOM 3-month (variant the papers report)")):
        report(backtest(c, spr, lb), name)


if __name__ == "__main__":
    main()
