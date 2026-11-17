"""What the two Reddit strategies add to fib618 -- three borrowed ideas tested on fib618's own 5-year trades.

    python -m bensim.fib_borrow [--trades data/fib618_base_5y.csv] [--live data/fib618_live_5y.csv]

Rules fixed BEFORE the first run (2026-10-14), never tuned:
  A  stretched location (ParallaxFX): keep a fib618 trade only if the last closed M15 bar before the entry is at/through
     the outer Bollinger band (20, 2) in the pullback direction (low <= lower band for a buy) AND Stochastic(14,3,3)
     %K <= 20 (buy) / >= 80 (sell). A_H1 = the same on the last closed H1 bar.
  B  fib on the confirmation candle (ParallaxFX): after the 0.618 touch, wait up to 16 M15 bars for a strong reversal
     bar (outside or engulfing, with-trade colour, higher high + higher close than the bar before; sell mirrored).
     The original fib618 stop trading first = no trade. Then two half-risk limits at 23.6% / 38.2% of that candle
     (only 38.2% if it closed past 23.6%, none if past 38.2%), stop beyond its 78.6%, target = fib618's own target;
     unfilled orders die after 16 bars or when price reaches the target. M5 execution (bensim.parallax.simulate).
  C  structure trail (CTA): fib618's own entry/stop/target, but each time a pullback of >= 2 M15 bars (no new extreme)
     ends with a CLOSE beyond the prior extreme, the stop moves to the pullback's extreme. M15 bars, a bar reaching
     stop and target counts as the stop; time exit after 200 M15 bars (fib618's max hold). Costs = the trade's own.
Results per variant: trades, avg R, total, years positive, per year -- next to fib618 as it is.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from . import parallax as px
from .backtest import resample


def _m15(p: px.Path) -> pd.DataFrame:
    b = resample({"t": p.t, "open": p.o, "high": p.h, "low": p.lo, "close": p.c}, 900)
    return px.indicators(pd.DataFrame({"open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"], "t": b["t"]}))


def _h1(p: px.Path) -> pd.DataFrame:
    b = resample({"t": p.t, "open": p.o, "high": p.h, "low": p.lo, "close": p.c}, 3600)
    return px.indicators(pd.DataFrame({"open": b["open"], "high": b["high"], "low": b["low"], "close": b["close"], "t": b["t"]}))


def stretched(b: pd.DataFrame, sec: int, t_entry: int, d: int) -> bool:
    i = int(np.searchsorted(b.t.to_numpy() + sec, t_entry, side="right")) - 1  # last bar closed by the entry
    if i < 0:
        return False
    x = b.iloc[i]
    return bool((x.low <= x.lbb and x.k <= 20) if d == 1 else (x.high >= x.ubb and x.k >= 80))


def confirm_candle(m: pd.DataFrame, p: px.Path, x: pd.Series) -> float | None:
    """Setup R (two half-risk legs) of idea B, or None = no trade."""
    d, sl, tp = int(x.dir), float(x.sl), float(x.tp)
    T = m.t.to_numpy()
    k0 = int(np.searchsorted(T + 900, int(x.t_entry), side="right"))  # first M15 bar closing after the touch
    Op, H, L, C = (m[c].to_numpy() for c in ("open", "high", "low", "close"))
    for j in range(max(k0, 1), min(k0 + 16, len(m))):
        if (L[j] <= sl) if d == 1 else (H[j] >= sl):
            return None
        top, bot, top1, bot1 = max(Op[j], C[j]), min(Op[j], C[j]), max(Op[j - 1], C[j - 1]), min(Op[j - 1], C[j - 1])
        shape = (H[j] > H[j - 1] and L[j] < L[j - 1]) or (top >= top1 and bot <= bot1)
        ok = shape and ((C[j] > Op[j] and H[j] > H[j - 1] and C[j] > C[j - 1]) if d == 1 else
                        (C[j] < Op[j] and L[j] < L[j - 1] and C[j] < C[j - 1]))
        if not ok:
            continue
        s = pd.Series({"dir": d, "high": H[j], "low": L[j], "close": C[j]})
        legs = px.legs_for(s, "author")
        if not legs:
            return None
        rs = []
        for entry, stop, kind in legs:
            if (tp - entry) * d <= 0:
                continue
            r = px.simulate(p, int(T[j]) + 900, d, entry, stop, tp, kind, 16 * 900, 200 * 900)
            rs.append(r[0] if r else 0.0)
        return 0.5 * sum(rs) if rs else None
    return None


def structure_trail(m: pd.DataFrame, x: pd.Series, trail: bool = True) -> float:
    """R of fib618's own trade with the CTA trailing stop (net of the trade's own costs)."""
    d, entry, sl, tp = int(x.dir), float(x.entry), float(x.sl), float(x.tp)
    risk = abs(entry - sl)
    cost = float(x.r_gross - x.r)
    T = m.t.to_numpy()
    H, L, C = m.high.to_numpy(), m.low.to_numpy(), m.close.to_numpy()
    k = int(np.searchsorted(T, int(x.t_entry), side="right")) - 1  # the bar holding the fill
    stop, ext, pb, pb_len, px_out = sl, (H[k] if d == 1 else L[k]), np.nan, 0, None
    for j in range(k + 1, min(k + 200, len(m))):
        if (L[j] <= stop) if d == 1 else (H[j] >= stop):
            px_out = stop
            break
        if (H[j] >= tp) if d == 1 else (L[j] <= tp):
            px_out = tp
            break
        if (H[j] > ext) if d == 1 else (L[j] < ext):
            if trail and pb_len >= 2 and ((C[j] > ext) if d == 1 else (C[j] < ext)):
                stop = max(stop, pb) if d == 1 else min(stop, pb)
            ext, pb, pb_len = (H[j] if d == 1 else L[j]), np.nan, 0
        else:
            pb_len += 1
            pb = np.nanmin([pb, L[j]]) if d == 1 else np.nanmax([pb, H[j]])
    if px_out is None:
        px_out = C[min(k + 199, len(m) - 1)]
    return (px_out - entry) * d / risk - cost


def run(trades: str) -> pd.DataFrame:
    t = pd.read_csv(trades)
    out = []
    for sym, g in t.groupby("symbol"):
        p = px.Path.load(sym, "2021-10-01", None)
        m15, h1 = _m15(p), _h1(p)
        for _, x in g.iterrows():
            out.append({"symbol": sym, "t_signal": int(x.t_signal), "t_entry": int(x.t_entry), "base": float(x.r),
                        "A": stretched(m15, 900, int(x.t_entry), int(x.dir)), "A_H1": stretched(h1, 3600, int(x.t_entry), int(x.dir)),
                        "B": confirm_candle(m15, p, x), "C": structure_trail(m15, x),
                        "C0": structure_trail(m15, x, trail=False)})
        print(f"  {sym}: {len(g)} trades", flush=True)
    return pd.DataFrame(out)


def report(r: pd.DataFrame, label: str) -> None:
    r = r.assign(year=pd.to_datetime(r.t_entry, unit="s").dt.year)

    def row(name: str, v: pd.Series, yrs: pd.Series) -> None:
        v = v.astype(float)
        ys = v.groupby(yrs).mean()
        t = v.mean() / v.std() * np.sqrt(len(v)) if len(v) > 1 else np.nan
        print(f"  {name:44} {len(v):5} tr avg {v.mean():+.3f}R total {v.sum():+7.1f}R t {t:+.2f} years+ {(ys > 0).sum()}/{len(ys)}  "
              + " ".join(f"{y % 100}:{x:+.2f}" for y, x in ys.items()))
    print(f"\n== {label}")
    row("fib618 as it is", r.base, r.year)
    for f in ("A", "A_H1"):
        k = r[r[f]]
        row(f"{f}: only stretched entries (kept)", k.base, k.year)
        d = r[~r[f]]
        row(f"   {f} dropped trades", d.base, d.year)
    b = r[r.B.notna()]
    row("B: fib on confirmation candle (when it triggers)", b.B, b.year)
    row("   same signals, fib618 as it is", b.base, b.year)
    row("C: CTA structure trail", r.C, r.year)
    row("   check: same M15 replay WITHOUT trail (should ~= as it is)", r.C0, r.year)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", default="data/fib618_base_5y.csv")
    ap.add_argument("--live", default="data/fib618_live_5y.csv")
    a = ap.parse_args()
    r = run(a.trades)
    r.to_csv("data/fib_borrow_5y.csv", index=False)
    live = pd.read_csv(a.live)[["symbol", "t_signal"]]
    report(r, "fib618 base (no confirmations), 7 pairs, 5y")
    report(r.merge(live, on=["symbol", "t_signal"]), "fib618 LIVE config subset (2 of killzone/POI/sweep)")


if __name__ == "__main__":
    main()
