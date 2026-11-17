"""Crypto time-series momentum (rules frozen 2026-10-19 before any run).

    python -m bensim.crypto_momentum

Universe: every coin in data/bars/binance with SPOT history starting <= 2020-12-31 (survivors of today's top 30 --
a survivorship bias that flatters every crypto test; the BTC benchmark has none).
Daily bars (UTC 00:00) from M5. At each daily close: signal = close > close 28 days ago -> hold the coin next day,
else flat. Weight = min(1, 2% / 20-day daily vol) / number of coins trading that day (no leverage, sum <= 1).
Cost: |weight change| x 0.1% (spot fee per side). Futures variants (0.05%/side): long/flat and long/short, each with and without
funding (binance_history --funding: longs pay positive rates, shorts receive).
Judged ONCE on 2022-01-01..today (untouched); 2017-2021 = development only. Pass: higher Sharpe than BTC buy & hold
AND a smaller max drawdown in 2022+.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .binance_history import OUT, load, load_funding

LOOKBACK, VOL_N, TARGET = 28, 20, 0.02
SPLIT = "2022-01-01"


def daily(name: str) -> pd.Series:
    m = load(name)
    d = pd.Series(m["close"], index=pd.to_datetime(m["t"], unit="s")).resample("1D").last()
    return d.dropna()


def weights(px: pd.DataFrame, short: bool = False) -> pd.DataFrame:
    """Target weight per coin decided at each daily close (rows = days, cols = coins). Shared by the backtest and the
    paper/live book (bensim.crypto_paper) so both trade exactly the same rule."""
    ret = px.pct_change(fill_method=None)
    sig = (px > px.shift(LOOKBACK)).astype(float)
    if short:
        sig = sig.where(px.notna()) * 2 - 1  # +1 above, -1 below
    vol = ret.rolling(VOL_N).std()
    live = px.notna() & vol.notna() & px.shift(LOOKBACK).notna()
    w = (TARGET / vol).clip(upper=1.0).where(live) * sig
    return w.div(live.sum(axis=1), axis=0).fillna(0.0)


def run(names: list[str], fee: float, short: bool, funding: bool = False) -> pd.Series:
    px = pd.DataFrame({n: daily(n) for n in names})
    ret = px.pct_change(fill_method=None)
    w = weights(px, short).shift(1).fillna(0.0)  # decided at the close, held the next day
    gross = (w * ret.fillna(0.0)).sum(axis=1)
    cost = w.diff().abs().sum(axis=1).fillna(0.0) * fee
    if funding:  # futures: a long PAYS each positive funding rate, a short receives it (day d's payments, day d's weight)
        f = pd.DataFrame({n: pd.Series(load_funding(n)[1], index=pd.to_datetime(load_funding(n)[0], unit="s")).resample("1D").sum()
                          for n in names}).reindex(w.index).fillna(0.0)
        cost = cost + (w * f).sum(axis=1)
    return gross - cost


def stats(r: pd.Series) -> str:
    r = r.dropna()
    eq = (1 + r).cumprod()
    yrs = len(r) / 365
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else 0
    sharpe = r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else 0
    dd = (eq / eq.cummax() - 1).min()
    return f"CAGR {cagr * 100:+6.1f}%  Sharpe {sharpe:+.2f}  maxDD {dd * 100:6.1f}%"


def main() -> None:
    meta = json.loads((OUT / "symbols.json").read_text())
    spot = [n for n in meta if n.endswith(".SPOT") and daily(n).index[0] <= pd.Timestamp("2020-12-31")]
    fut = [n.replace(".SPOT", "") for n in spot if n.replace(".SPOT", "") in meta]
    print(f"universe ({len(spot)}): {', '.join(n.replace('USDT.SPOT', '') for n in spot)}")
    btc = daily("BTCUSDT.SPOT").pct_change(fill_method=None)
    ew = pd.DataFrame({n: daily(n) for n in spot}).pct_change(fill_method=None).mean(axis=1)
    series = {"BTC buy & hold": btc, "equal-weight buy & hold": ew,
              "MOMENTUM long/flat (spot)": run(spot, 0.001, False),
              "futures long/flat, no funding": run(fut, 0.0005, False),
              "futures long/flat + FUNDING": run(fut, 0.0005, False, True),
              "futures long/short, no funding": run(fut, 0.0005, True),
              "futures long/short + FUNDING": run(fut, 0.0005, True, True)}
    for period, a, b in (("DEVELOPMENT 2017-08..2021-12", None, SPLIT), ("UNTOUCHED 2022-01..today", SPLIT, None)):
        print(f"\n{period}")
        for k, s in series.items():
            s = s[a:b] if a or b else s
            if b:
                s = s[s.index < b]
            print(f"  {k:32} {stats(s)}")
    mom = series["MOMENTUM long/flat (spot)"]
    print("\nby year (momentum long/flat vs BTC):")
    for y in sorted(set(mom.index.year)):
        m_, b_ = mom[mom.index.year == y], btc[btc.index.year == y]
        print(f"  {y}: momentum {((1 + m_).prod() - 1) * 100:+7.1f}%   BTC {((1 + b_.dropna()).prod() - 1) * 100:+7.1f}%")


if __name__ == "__main__":
    main()
