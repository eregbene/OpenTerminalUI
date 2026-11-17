"""Regime switch: per pair and month, trade mean-reversion OR trend OR nothing -- decided only from the past.

    python -m bensim.regime_switch

Rules fixed BEFORE the first run (2026-10-14), from the regime study (bensim/regime_study.py):
  data       FXCM 25 pairs, NY-17:00 daily bars from H1, 2012 .. now; weekend stubs dropped.
  MR         close outside Bollinger (20, 2) -> enter AGAINST it at the next open; exit at the first close back at/inside
             the middle band (20 SMA) or after 5 days (close); stop 2 x ATR(20) from the entry.
  TF         close beyond the prior 20-day high/low -> enter WITH it at the next open; exit after 10 days (close);
             stop 2 x ATR(20). One open trade per pair per strategy.
  stop       checked on the daily high/low; a gap through fills at the open. Cost = the pair's median real FXCM
             spread + 0.1 pip slippage per round trip, in R.
  switch A   ("what is working"): at each month start, per pair, using only trades of that pair CLOSED before it in
             the last 12 months: MR if its mean R > 0 and > TF's; else TF if TF's mean R > 0; else stand aside.
  switch B   ("market behaviour"): trailing 252-day variance ratio of 10-day vs 1-day returns: < 0.95 -> MR,
             > 1.05 -> TF, else stand aside.
  checks     always-MR, always-TF, 200 random monthly switches (same mix of choices as switch A); periods 2014-2020
             and 2021-now shown apart; $ on 100k at 0.25% risk per trade with the worst drawdown (FTMO max 10%).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import parallax as px
from .fxcm_history import OUT, load
from .strategy import pip


def daily(sym: str) -> tuple[pd.DataFrame, float]:
    m = load(sym, "H1")
    b = px.indicators(px.ny_bars({k: m[k] for k in ("t", "open", "high", "low", "close")}, 24))
    b = b[pd.to_datetime(b.t_close, unit="s").dt.dayofweek < 5].reset_index(drop=True)
    tr = pd.concat([b.high - b.low, (b.high - b.close.shift()).abs(), (b.low - b.close.shift()).abs()], axis=1).max(axis=1)
    b["atr"] = tr.rolling(20).mean()
    b["date"] = pd.to_datetime(b.t_close, unit="s")
    r = np.log(b.close).diff()
    b["vr"] = (r.rolling(10).sum().rolling(252).var() / (10 * r.rolling(252).var()))
    return b, float(np.median(m["spr"])) + 0.1 * pip(sym)


def trades(b: pd.DataFrame, cost: float, kind: str) -> pd.DataFrame:
    Op, H, L, C = (b[c].to_numpy() for c in ("open", "high", "low", "close"))
    mid, ub, lb, atr = ((b.ubb + b.lbb) / 2).to_numpy(), b.ubb.to_numpy(), b.lbb.to_numpy(), b.atr.to_numpy()
    hi20 = b.high.rolling(20).max().shift(1).to_numpy()
    lo20 = b.low.rolling(20).min().shift(1).to_numpy()
    out, busy, n = [], -1, len(b)
    for i in range(25, n - 1):
        if i <= busy or not np.isfinite(atr[i]):
            continue
        if kind == "MR":
            d = -1 if C[i] > ub[i] else (1 if C[i] < lb[i] else 0)
            hold = 5
        else:
            d = 1 if C[i] > hi20[i] else (-1 if C[i] < lo20[i] else 0)
            hold = 10
        if not d:
            continue
        k = i + 1
        entry = Op[k]
        stop = entry - d * 2 * atr[i]
        risk = 2 * atr[i]
        exit_px, j = None, k
        for j in range(k, min(k + hold, n)):
            if (L[j] <= stop) if d == 1 else (H[j] >= stop):
                exit_px = (min(stop, Op[j]) if d == 1 else max(stop, Op[j])) if j > k else stop
                break
            if kind == "MR" and (C[j] - mid[j]) * d >= 0:
                exit_px = C[j]
                break
        if exit_px is None:
            exit_px = C[j]
        out.append((b.date.iloc[i], b.date.iloc[j], d, (exit_px - entry) * d / risk - cost / risk))
        busy = j
    return pd.DataFrame(out, columns=["signal", "exit", "dir", "r"]).assign(kind=kind)


def decide_A(mr: pd.DataFrame, tf: pd.DataFrame, months: pd.DatetimeIndex) -> pd.Series:
    ch = {}
    for m in months:
        lo = m - pd.DateOffset(months=12)
        a = mr[(mr.exit < m) & (mr.exit >= lo)].r
        t = tf[(tf.exit < m) & (tf.exit >= lo)].r
        am, tm = (a.mean() if len(a) >= 5 else np.nan), (t.mean() if len(t) >= 5 else np.nan)
        ch[m] = "MR" if am > 0 and (not tm > am) else ("TF" if tm > 0 else "-")
    return pd.Series(ch)


def decide_B(b: pd.DataFrame, months: pd.DatetimeIndex) -> pd.Series:
    vr = pd.Series(b.vr.to_numpy(), index=b.date)
    ch = {}
    for m in months:
        v = vr[vr.index < m]
        x = v.iloc[-1] if len(v) and np.isfinite(v.iloc[-1]) else np.nan
        ch[m] = "MR" if x < 0.95 else ("TF" if x > 1.05 else "-")
    return pd.Series(ch)


def pick(mr: pd.DataFrame, tf: pd.DataFrame, choice: pd.Series) -> pd.DataFrame:
    """Trades whose signal falls in a month whose choice is their kind."""
    out = []
    for df in (mr, tf):
        mo = df.signal.dt.to_period("M").dt.to_timestamp()
        out.append(df[choice.reindex(mo).to_numpy() == df.kind.iloc[0]] if len(df) else df)
    return pd.concat(out, ignore_index=True)


def stats(t: pd.DataFrame, label: str, risk_pct: float = 0.25) -> str:
    if t.empty:
        return f"  {label:34} no trades"
    t = t.sort_values("exit")
    r = t.r.to_numpy()
    usd = 100_000 * risk_pct / 100 * r
    eq = 100_000 + np.cumsum(usd)
    dd = (np.maximum.accumulate(eq) - eq).max()
    ys = t.groupby(t.signal.dt.year).r.sum()
    return (f"  {label:34} {len(r):5} tr avg {r.mean():+.3f}R total {r.sum():+7.1f}R t {r.mean() / r.std() * np.sqrt(len(r)):+.2f} "
            f"years+ {(ys > 0).sum():2}/{len(ys)} | 100k@{risk_pct}%: {usd.sum() / 1000:+.1f}k, worst DD {dd / 1000:.1f}k")


def main() -> None:
    pairs = sorted(f.stem.split("_")[0] for f in OUT.glob("*_H1.npz"))
    months = pd.date_range("2014-01-01", pd.Timestamp.now().strftime("%Y-%m-01"), freq="MS")
    res = {k: [] for k in ("MR always", "TF always", "switch A (what works)", "switch B (variance ratio)")}
    perpair = []
    rng = np.random.default_rng(0)
    plac = [[] for _ in range(200)]
    for s in pairs:
        b, cost = daily(s)
        mr, tf = trades(b, cost, "MR"), trades(b, cost, "TF")
        mr, tf = mr[mr.signal >= months[0]], tf[tf.signal >= months[0]]
        a, bb = decide_A(mr, tf, months), decide_B(b, months)
        res["MR always"].append(mr)
        res["TF always"].append(tf)
        res["switch A (what works)"].append(pick(mr, tf, a))
        res["switch B (variance ratio)"].append(pick(mr, tf, bb))
        mix = a.value_counts(normalize=True).reindex(["MR", "TF", "-"]).fillna(0).to_numpy()
        for p in plac:
            p.append(pick(mr, tf, pd.Series(rng.choice(["MR", "TF", "-"], len(months), p=mix), index=months)))
        perpair.append((s, (a == "MR").mean(), (a == "TF").mean()))
        print(f"  {s}: MR {len(mr)} tr {mr.r.mean():+.3f}R | TF {len(tf)} tr {tf.r.mean():+.3f}R | A chose MR {(a == 'MR').mean():.0%} TF {(a == 'TF').mean():.0%}", flush=True)
    cut = pd.Timestamp("2021-01-01")
    for name, frames in res.items():
        t = pd.concat(frames, ignore_index=True)
        print(f"\n{name}")
        print(stats(t, "2014 .. now"))
        print(stats(t[t.signal < cut], "2014-2020"))
        print(stats(t[t.signal >= cut], "2021 .. now"))
    a_total = pd.concat(res["switch A (what works)"]).r.sum()
    pl = np.array([pd.concat(p).r.sum() for p in plac])
    print(f"\nPLACEBO: 200 random switches with switch A's mix: total R median {np.median(pl):+.1f}, 5-95% "
          f"[{np.percentile(pl, 5):+.1f}, {np.percentile(pl, 95):+.1f}] -> switch A ({a_total:+.1f}R) beats {np.mean(pl < a_total):.0%}")
    pd.concat([pd.concat(v).assign(set=k) for k, v in res.items()]).to_csv("data/regime_switch_trades.csv", index=False)


if __name__ == "__main__":
    main()
