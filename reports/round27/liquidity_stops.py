"""Round 27 (2026-11-11): the user's stop idea on 2 years of every live strategy's trades (round 26 set, 3,569 trades).
User: keep the entry (imbalance / FVG / zone), but put the stop BELOW THE LIQUIDITY (the nearest swing low for a buy,
swing high for a sell) "with some pips" -- on the 1H, 4H or 1D chart -- instead of the engine's small stop.
Per trade, re-simulated on M5 bid bars (spread + 0.1 pip slip each side, observed_account spreads):
  ORIG      engine stop + engine target
  <TF> keepTP   stop = nearest confirmed TF swing beyond the engine stop, minus buffer; engine target
  <TF> 3R       same stop; target 3 x the new stop (the user's 1:3)
TF = H1 / H4 / D1. Swing = 2-bar fractal, confirmed (both right bars closed) before entry, last 300 TF bars.
Buffer: 5 pips FX (0.05 JPY), $3 gold. Max hold 10 days, then out at the bar close.
Results in R of each variant's own stop = the same $ risk per trade (lots shrink when the stop widens).
Also: how many ORIG losers would the wider stop have saved (orig stop hit, then target hit before the new stop).
    python reports/round27/liquidity_stops.py
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + "/reports/round26")
OUT = Path(ROOT + "/reports/round27")
TFS = {"H1": 3600, "H4": 14400, "D1": 86400}
HOLD = 10 * 86400
SPR = {s: float(np.mean(v)) for s, v in json.loads(Path(ROOT + "/data/costs/spread_samples_2026-11-03.json").read_text())["spread_pips"].items()}


def pip(sym):
    return 0.01 if sym == "USDJPY" else 0.1 if sym == "XAUUSD" else 1e-4


def buffer(sym):
    return 3.0 if sym == "XAUUSD" else 5 * pip(sym)


def swings(b, dirn, k=2):
    """(confirm_time, level) of fractal lows (dirn=1) / highs (dirn=-1)."""
    x = b["low"] if dirn == 1 else -b["high"]
    n = len(x)
    i = np.arange(k, n - k)
    ok = np.ones(len(i), bool)
    for j in range(1, k + 1):
        ok &= (x[i] <= x[i - j]) & (x[i] < x[i + j])
    i = i[ok]
    return b["t"][i + k] + (b["t"][1] - b["t"][0] if n > 1 else 0), (x[i] if dirn == 1 else -x[i])


def sim(m5, z, dirn, entry, sl, tp, spr):
    """R of one trade from M5 index z (first bar at/after entry). Buys exit at bid, sells at ask (bid + spr)."""
    end = np.searchsorted(m5["t"], m5["t"][z] + HOLD)
    hi, lo = m5["high"][z:end], m5["low"][z:end]
    if dirn == -1:
        hi, lo = hi + spr, lo + spr
    stop = abs(entry - sl)
    hit_sl = (lo <= sl) if dirn == 1 else (hi >= sl)
    hit_tp = (hi >= tp) if dirn == 1 else (lo <= tp)
    a = np.argmax(hit_sl) if hit_sl.any() else 10**9
    b = np.argmax(hit_tp) if hit_tp.any() else 10**9
    if a == b == 10**9:
        px = m5["close"][end - 1] + (spr if dirn == -1 else 0)
        return (px - entry) * dirn / stop, end - z
    if a <= b:  # same bar = stop first
        return -1.0, a
    return abs(tp - entry) / stop, b


def run_sym(args):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # below normal
    sym, trades = args
    from bensim.backtest import load_m5, resample
    m5 = load_m5(sym, "2024-06-01", "2026-11-11")
    spr = (SPR.get(sym, 0.5) + 0.2) * pip(sym)  # spread + 0.1 slip in and out
    sw = {tf: {d: swings(resample(m5, sec), d) for d in (1, -1)} for tf, sec in TFS.items()}
    rows = []
    for t in trades:
        d, e, sl0, tp0 = t["dir"], t["entry"], t["sl"], t["tp"]
        z = np.searchsorted(m5["t"], t["t_entry"])
        if z >= len(m5["t"]) - 2 or abs(e - sl0) <= 0:
            continue
        r0, _ = sim(m5, z, d, e, sl0, tp0, spr)
        row = dict(strategy=t["strategy"], sym=sym, t_entry=t["t_entry"], dir=d, orig=r0,
                   orig_stop_pips=abs(e - sl0) / pip(sym), lpl=t["loss_per_lot"] / abs(e - sl0))  # $ per lot per 1.0 move
        for tf in TFS:
            ct, lv = sw[tf][d]
            m = ct <= t["t_entry"]
            ct, lv = ct[m][-300:], lv[m][-300:]
            beyond = lv[lv < sl0] if d == 1 else lv[lv > sl0]
            if len(beyond) == 0:
                row.update({f"{tf}_stop_pips": np.nan, f"{tf}_keepTP": np.nan, f"{tf}_3R": np.nan, f"{tf}_saved": False})
                continue
            new_sl = (beyond.max() - buffer(sym)) if d == 1 else (beyond.min() + buffer(sym))
            st = abs(e - new_sl)
            rk, _ = sim(m5, z, d, e, new_sl, tp0, spr)
            r3, _ = sim(m5, z, d, e, new_sl, e + 3 * st * d, spr)
            row.update({f"{tf}_stop_pips": st / pip(sym), f"{tf}_keepTP": rk, f"{tf}_3R": r3,
                        f"{tf}_saved": r0 < 0 and rk > 0})
        rows.append(row)
    return rows


def main():
    from sizing_2y import load
    a = load()
    by = {s: g.to_dict("records") for s, g in a.groupby("sym")}
    f = OUT / "liquidity_stops_trades.csv"
    if not f.exists():
        with ProcessPoolExecutor(4) as ex:
            rows = [r for part in ex.map(run_sym, by.items()) for r in part]
        pd.DataFrame(rows).to_csv(f, index=False)
    t = pd.read_csv(f)
    t["year"] = np.where(pd.to_datetime(t.t_entry, unit="s") < "2025-11-07", "Y1", "Y2")
    cols = ["orig"] + [f"{tf}_{v}" for tf in TFS for v in ("keepTP", "3R")]
    print(f"{len(t)} trades re-simulated (2024-11-07 .. 2026-11-07), R per trade at the SAME $ risk\n")

    def table(g, title):
        print(f"== {title} ({len(g)} trades)")
        print(f"{'variant':12s} {'n':>5s} {'win%':>6s} {'avg R':>7s} {'total R':>8s} {'Y1 R':>7s} {'Y2 R':>7s} {'stop pips':>9s} {'$ at $1k risk':>13s}")
        for c in cols:
            x = g[c].dropna()
            sp = g["orig_stop_pips"] if c == "orig" else g[c.split("_")[0] + "_stop_pips"]
            y = g.loc[x.index]
            print(f"{c:12s} {len(x):5d} {100 * (x > 0).mean():5.1f}% {x.mean():+7.3f} {x.sum():+8.1f} "
                  f"{y[y.year == 'Y1'][c].sum():+7.1f} {y[y.year == 'Y2'][c].sum():+7.1f} {sp.loc[x.index].median():9.1f} {1000 * x.sum():+13,.0f}")
        for tf in TFS:
            m = g[f"{tf}_keepTP"].notna()
            lost = (g.orig[m] < 0).sum()
            print(f"   {tf}: engine-stop losers saved by the wider stop (then hit target): {g[f'{tf}_saved'][m].sum()} of {lost}")
        print()

    table(t, "ALL strategies")
    for s, g in t.groupby("strategy"):
        table(g, s)
    # what the user did on the DEMO: same lots, stop widened -> $ change
    print("== SAME LOTS as the engine, stop moved below liquidity (what was done on the GBPUSD trade): $ total")
    pipsz = t.sym.map(lambda s: 0.01 if s == "USDJPY" else 0.1 if s == "XAUUSD" else 1e-4)
    lots = 1000 / (t.orig_stop_pips * pipsz * t.lpl)  # lots the engine used at $1k risk on its own stop
    for tf in TFS:
        m = t[f"{tf}_keepTP"].notna()
        per_r = t[f"{tf}_stop_pips"] * pipsz * t.lpl * lots  # $ of 1R on the wide stop with the same lots
        usd = (t[f"{tf}_keepTP"] * per_r)[m]
        worst = -per_r[m]
        print(f"   {tf}: total {usd.sum():+,.0f} $ vs engine {(1000 * t.orig[m]).sum():+,.0f} $; median loss when stopped "
              f"{-worst.median():,.0f} $, worst single loss {-worst.min():,.0f} $")


if __name__ == "__main__":
    main()
