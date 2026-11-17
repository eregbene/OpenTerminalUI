"""TradingView indicators the user trades with, rebuilt causally (value at bar i uses bars <= i only), for testing
as confirmations of the fib-breakout signal.

  squeeze(b)       LazyBear "Squeeze Momentum" (SQZMOM_LB): BB(20, 1.5 -- the script uses multKC for the BB, kept as
                   published) inside KC(20, 1.5, true range) = squeeze on; momentum = linreg(close - mean(mid(HH,LL), SMA), 20)
  supertrend(b)    Supertrend(10, 3, ATR = RMA): +1 / -1 direction
  vol_osc(v)       LuxAlgo "S/R Levels with Breaks" volume %: 100 * (EMA5(vol) - EMA10(vol)) / EMA10(vol); break counts if > 20
  sr_levels(b)     the same script's pivot S/R (15 left / 15 right), known 16 bars after the pivot
Forex volume here = MT5 tick volume (number of price updates), not traded size.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

Bars = dict[str, np.ndarray]


def _tr(b: Bars) -> np.ndarray:
    prev = np.r_[b["close"][0], b["close"][:-1]]
    return np.maximum(b["high"] - b["low"], np.maximum(np.abs(b["high"] - prev), np.abs(b["low"] - prev)))


def squeeze(b: Bars, n: int = 20, mult: float = 1.5) -> tuple[np.ndarray, np.ndarray]:
    c = pd.Series(b["close"])
    basis, dev = c.rolling(n).mean(), mult * c.rolling(n).std(ddof=0)
    rng = pd.Series(_tr(b)).rolling(n).mean()
    on = ((basis - dev) > (basis - rng * mult)) & ((basis + dev) < (basis + rng * mult))
    mid = (pd.Series(b["high"]).rolling(n).max() + pd.Series(b["low"]).rolling(n).min()) / 2
    x = (c - (mid + basis) / 2).to_numpy()
    t = np.arange(n, dtype=float)
    w = (t - t.mean()) / ((t - t.mean()) ** 2).sum()
    mom = np.full(len(x), np.nan)
    for i in range(2 * n, len(x)):  # linreg(x, n, 0) = fitted value at the last point
        y = x[i - n + 1:i + 1]
        slope = (w * (y - y.mean())).sum()
        mom[i] = y.mean() + slope * (n - 1 - t.mean())
    return on.to_numpy(), mom


def supertrend(b: Bars, n: int = 10, mult: float = 3.0) -> np.ndarray:
    atr = pd.Series(_tr(b)).ewm(alpha=1 / n, adjust=False).mean().to_numpy()
    src = (b["high"] + b["low"]) / 2
    c = b["close"]
    up, dn = src - mult * atr, src + mult * atr
    d = np.ones(len(c), dtype=int)
    for i in range(1, len(c)):
        if c[i - 1] > up[i - 1]:
            up[i] = max(up[i], up[i - 1])
        if c[i - 1] < dn[i - 1]:
            dn[i] = min(dn[i], dn[i - 1])
        d[i] = 1 if d[i - 1] == -1 and c[i] > dn[i - 1] else (-1 if d[i - 1] == 1 and c[i] < up[i - 1] else d[i - 1])
    return d


def vol_osc(v: np.ndarray) -> np.ndarray:
    s, l_ = pd.Series(v).ewm(span=5, adjust=False).mean(), pd.Series(v).ewm(span=10, adjust=False).mean()
    return (100 * (s - l_) / l_).to_numpy()


def sr_levels(b: Bars, left: int = 15, right: int = 15) -> tuple[np.ndarray, np.ndarray]:
    h, l_ = b["high"], b["low"]
    n = len(h)
    res, sup = np.full(n, np.nan), np.full(n, np.nan)
    r = s = np.nan
    for i in range(n):
        j = i - right - 1  # the script uses pivothigh(15,15)[1]: confirmed one bar later
        if j >= left:
            if h[j] == h[j - left:j + right + 1].max():
                r = h[j]
            if l_[j] == l_[j - left:j + right + 1].min():
                s = l_[j]
        res[i], sup[i] = r, s
    return res, sup


def _selfcheck() -> None:
    rng = np.random.default_rng(3)
    c = 1.1 + np.cumsum(rng.normal(0, 0.0005, 400))
    b = {"open": np.r_[c[0], c[:-1]], "high": c + 0.0004, "low": c - 0.0004, "close": c}
    on, mom = squeeze(b)
    d = supertrend(b)
    assert set(np.unique(d)) <= {-1, 1} and np.isfinite(mom[-1]) and on.dtype == bool
    up = {"open": np.linspace(1, 2, 200), "high": np.linspace(1, 2, 200) + 0.01, "low": np.linspace(1, 2, 200) - 0.01, "close": np.linspace(1, 2, 200)}
    assert supertrend(up)[-1] == 1 and squeeze(up)[1][-1] > 0  # a steady rise: supertrend up, momentum positive
    assert vol_osc(np.r_[np.ones(50), np.full(5, 5.0)])[-1] > 20
    print("extra_ind selfcheck ok")



def report(csv: str = "data/bensim_ml_signals_18m.csv") -> pd.DataFrame:
    """Each indicator as a confirmation of every trade in a backtest CSV, at its M15 signal bar."""
    from . import db

    t = pd.read_csv(csv)
    rows = []
    for sym, g in t.groupby("symbol"):
        with db.connect() as c:
            r = c.execute("""select (extract(epoch from ts)::bigint / 900) * 900 k, (array_agg(open order by ts))[1], max(high), min(low),
                             (array_agg(close order by ts desc))[1], sum(tick_volume) from candles where symbol=%s and tf='M5'
                             and ts >= to_timestamp(%s) and ts < to_timestamp(%s) group by 1 order by 1""",
                          (sym, int(g.t_signal.min()) - 40 * 86400, int(g.t_signal.max()) + 900)).fetchall()
        a = np.array(r, dtype=float)
        b = {"t": a[:, 0].astype(np.int64), "open": a[:, 1], "high": a[:, 2], "low": a[:, 3], "close": a[:, 4]}
        on, mom = squeeze(b)
        st = supertrend(b)
        vo = vol_osc(a[:, 5])
        res, sup = sr_levels(b)
        key = b["t"] // 3600
        s = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
        h1 = {"t": key[s] * 3600, "high": np.maximum.reduceat(b["high"], s), "low": np.minimum.reduceat(b["low"], s), "close": b["close"][np.r_[s[1:], len(key)] - 1]}
        st_h1 = supertrend(h1)
        for row in g.itertuples():
            i = int(np.searchsorted(b["t"], row.t_signal))
            if i >= len(b["t"]) or b["t"][i] != row.t_signal:
                continue
            k1 = int(np.searchsorted(h1["t"] + 3600, row.t_signal + 900, side="right")) - 1  # last H1 bar closed by the signal close
            d = row.dir
            rows.append({"symbol": sym, "t_entry": row.t_entry, "r": row.r, "dir": d,
                         "rule": bool((row.chk_fvg or row.chk_ob) and row.chk_killzone),
                         "sqz_release": bool(on[max(0, i - 5):i].any()),
                         "mom_with": bool(np.isfinite(mom[i]) and mom[i] * d > 0 and (mom[i] - mom[i - 1]) * d > 0),
                         "supertrend_m15": bool(st[i] == d), "supertrend_h1": bool(k1 >= 0 and st_h1[k1] == d),
                         "vol_surge": bool(vo[i] > 20),
                         "sr_break": bool((b["close"][i] > res[i]) if d == 1 else (b["close"][i] < sup[i]))})
    f = pd.DataFrame(rows)
    f["quarter"] = pd.to_datetime(f.t_entry, unit="s", utc=True).dt.to_period("Q").astype(str)
    return f


def main() -> None:
    f = report()
    f.to_csv("data/bensim_extra_ind_report.csv", index=False)
    for universe, g0 in (("ALL plain-fib trades", f), ("poi+killzone trades", f[f.rule])):
        print(f"\n=== {universe}: n={len(g0)} avgR={g0.r.mean():+.3f} totR={g0.r.sum():+.1f}")
        print(f"{'filter':15} {'n yes':>6} {'avgR yes':>9} {'avgR no':>8} {'totR yes':>9} | avgR yes-no per quarter (+ = filter helped)")
        for col in ("sqz_release", "mom_with", "supertrend_m15", "supertrend_h1", "vol_surge", "sr_break"):
            y, n = g0[g0[col]], g0[~g0[col]]
            per_q = [f"{q[2:]}:{(gq[gq[col]].r.mean() - gq[~gq[col]].r.mean()):+.2f}" for q, gq in g0.groupby("quarter") if gq[col].sum() >= 5 and (~gq[col]).sum() >= 5]
            wins = sum(1 for p in per_q if not p.endswith("nan") and float(p.split(":")[1]) > 0)
            print(f"{col:15} {len(y):6d} {y.r.mean():+9.3f} {n.r.mean():+8.3f} {y.r.sum():+9.1f} | {' '.join(per_q)}  -> helped {wins}/{len(per_q)}")


if __name__ == "__main__":
    import sys

    _selfcheck() if "--selfcheck" in sys.argv else main()
