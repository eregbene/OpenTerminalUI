"""Round 26 (2026-11-10): 2-YEAR sizing test on the 100k DEMO for EVERY live stop-based strategy, as they run now:
fib618 (live PARAMS, 5 pairs), imbalance + enigma books, Bennett MENTOR (round 25 LATEST2F), channel BUYS, Faiz MATCHED,
JeaFx MATCHED H1/M5 (sflow book). Period 2024-11-07 .. 2026-11-07 (same as round 25). Flow ML/AM left out: lot-sized
at a disaster stop, not stop-based.
Trades: round 25 two_year_trades.csv + new run_symbol runs for fib618 / imbalance / enigma (observed_account costs).
Sizing schemes (lots = risk $ / (stop pips x pip value), floor 0.01; "cap 3" = at most 3 lots):
  $250 | $250 cap 3 | $500 | $500 cap 3 | current live ($1,000 Bennett+channel, $500 rest) | fixed 3 lots | fixed 1 lot
Per scheme: total $, worst drawdown from the running peak ($ and % of 100k), worst month, worst day, longest losing
streak; per strategy at each scheme. Then the "right size" for the 100k: the largest $ risk whose worst 2-year
drawdown stays <= 10% of the account (and <= 5%). Assumes every signal is taken (the live one-trade-per-pair rule
and the guard skip some -- real totals are somewhat lower).
    python reports/round26/sizing_2y.py
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
S, E = "2024-11-07", "2026-11-07"
OUT = ROOT + "/reports/round26/"
SYMS7 = ["EURUSD", "GBPUSD", "AUDUSD", "XAUUSD", "USDJPY", "USDCHF", "USDCAD"]
PIP = {"USDJPY": 0.01, "XAUUSD": 0.1}
LIVE_NOW = {"bennett": 1000, "channel": 1000}


def job(a):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # below normal
    name, sym = a
    from bensim import books, live
    from bensim.backtest import exec_profile, run_symbol
    p, kind = {"fib618": (live.PARAMS, "imbalance"), "imbalance": (books.IMB, "imbalance"), "enigma": (books.ENIGMA, "htf_fvg")}[name]
    t = run_symbol((sym, S + "T00:00:00+00:00", E + "T00:00:00+00:00", p, exec_profile("observed_account"), kind))
    return [] if t.empty else [dict(dir=r.dir, t_entry=int(r.t_entry), entry=r.entry, sl=r.sl, tp=r.tp, r=r.r, strategy=name, sym=sym)
                               for r in t.itertuples()]


def pipval(sym, price):
    return 1000 / price if sym == "USDJPY" else 10 / price if sym in ("USDCAD", "USDCHF") else 10.0


def load() -> pd.DataFrame:
    f = OUT + "other_books_trades.csv"
    if not os.path.exists(f):
        from bensim import live
        jobs = [("fib618", s) for s in live.LIVE_SYMBOLS] + [(b, s) for b in ("imbalance", "enigma") for s in SYMS7]
        with ProcessPoolExecutor(4) as ex:
            rows = [r for part in ex.map(job, jobs) for r in part]
        pd.DataFrame(rows).to_csv(f, index=False)
    o = pd.read_csv(f)
    t = pd.read_csv(ROOT + "/reports/round25/two_year_trades.csv")
    keep = {"bennett LATEST2F": "bennett", "channel CHANNEL": "channel", "faiz MATCHED": "faiz", "jeafx MATCHED H1/M5": "sflow (JeaFx)"}
    t = t[t.strategy.isin(keep)].copy()
    t["strategy"] = t.strategy.map(keep)
    t = t[(t.strategy != "channel") | (t.dir == 1)]  # channel live = buys only
    a = pd.concat([o, t], ignore_index=True).sort_values("t_entry").reset_index(drop=True)
    stop_pips = (a.entry - a.sl).abs() / a.sym.map(lambda s: PIP.get(s, 1e-4))
    a["loss_per_lot"] = stop_pips * [pipval(s, e) for s, e in zip(a.sym, a.entry)]
    return a[a.loss_per_lot > 0]


def lots(a: pd.DataFrame, risk, cap=None, fixed=None) -> np.ndarray:
    if fixed:
        return np.full(len(a), float(fixed))
    r = a.strategy.map(lambda s: LIVE_NOW.get(s, 500)).to_numpy(float) if risk == "live" else np.full(len(a), float(risk))
    lt = np.floor(r / a.loss_per_lot.to_numpy() * 100) / 100
    lt = np.maximum(lt, 0.01)
    return np.minimum(lt, cap) if cap else lt


def stats(a: pd.DataFrame, pnl: np.ndarray) -> dict:
    eq = 100_000 + np.cumsum(pnl)
    dd = (np.maximum.accumulate(np.r_[100_000, eq])[1:] - eq).max()
    ts = pd.to_datetime(a.t_entry, unit="s")
    month = pd.Series(pnl).groupby(ts.dt.to_period("M").to_numpy()).sum()
    day = pd.Series(pnl).groupby(ts.dt.date.to_numpy()).sum()
    streak = cur = 0
    for x in pnl:
        cur = cur + 1 if x < 0 else 0
        streak = max(streak, cur)
    return {"total": pnl.sum(), "dd": dd, "dd_pct": dd / 1000, "worst_month": month.min(), "worst_day": day.min(),
            "y1": pnl[ts < "2025-11-07"].sum(), "y2": pnl[ts >= "2025-11-07"].sum(), "streak": streak, "max_lots": None}


def main():
    a = load()
    r = a.r.to_numpy()
    print(f"{len(a)} trades 2024-11-07 .. 2026-11-07: " + ", ".join(f"{k} {v}" for k, v in a.strategy.value_counts().items()))
    print(f"total {r.sum():+.1f}R\n")
    schemes = {"$250 risk": dict(risk=250), "$250 risk, max 3 lots": dict(risk=250, cap=3), "$500 risk": dict(risk=500),
               "$500 risk, max 3 lots": dict(risk=500, cap=3), "current live ($1k Bennett+channel, $500 rest)": dict(risk="live"),
               "fixed 3 lots": dict(risk=0, fixed=3), "fixed 1 lot": dict(risk=0, fixed=1)}
    print(f"{'scheme':48s} {'total $':>10s} {'year 1':>9s} {'year 2':>9s} {'worst DD':>9s} {'DD %':>6s} {'worst month':>11s} {'worst day':>9s} {'lose run':>8s}")
    per = {}
    for name, kw in schemes.items():
        lt = lots(a, **kw)
        pnl = r * lt * a.loss_per_lot.to_numpy()
        s = stats(a, pnl)
        per[name] = pd.Series(pnl).groupby(a.strategy.to_numpy()).sum()
        print(f"{name:48s} {s['total']:>+10,.0f} {s['y1']:>+9,.0f} {s['y2']:>+9,.0f} {s['dd']:>9,.0f} {s['dd_pct']:>5.1f}% "
              f"{s['worst_month']:>+11,.0f} {s['worst_day']:>+9,.0f} {s['streak']:>8d}")
    print("\n$ per strategy:")
    df = pd.DataFrame(per)
    df["trades"] = a.strategy.value_counts()
    df["R"] = pd.Series(r).groupby(a.strategy.to_numpy()).sum()
    print(df.round(0).to_string())
    print("\nRight size for the 100k (same $ risk for every strategy, max 3 lots):")
    for lim in (5, 10):
        best = None
        for risk in range(50, 2001, 25):
            pnl = r * lots(a, risk=risk, cap=3) * a.loss_per_lot.to_numpy()
            s = stats(a, pnl)
            if s["dd_pct"] <= lim:
                best = (risk, s)
        if best:
            risk, s = best
            print(f"  worst drawdown <= {lim}%: ${risk} per trade -> total {s['total']:+,.0f}, DD {s['dd']:,.0f} ({s['dd_pct']:.1f}%), "
                  f"worst month {s['worst_month']:+,.0f}, worst day {s['worst_day']:+,.0f}")
        else:
            print(f"  worst drawdown <= {lim}%: not reachable even at $50")


if __name__ == "__main__":
    main()
