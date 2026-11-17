"""The user's first table (zone at the fill, stop under the liquidity, 1:3) rebuilt on BOTH periods with everything learned
in rounds 27-30, and WHY 4H looked OK while 1H/D1 did not: gross edge vs costs vs noise.
Uses the round 28 zone events (same zone definition) + adds the Fibonacci premium/discount position of the zone
(zone top inside the last 50 bars' range of its own TF: < 0.5 = discount for buys, mirrored for sells).
    python reports/round28/first_table_why.py
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
OUT = Path(ROOT + "/reports/round28")
TFS = {"H1": 3600, "H4": 14400, "D1": 86400}
SPR = {s: float(np.mean(v)) for s, v in json.loads(Path(ROOT + "/data/costs/spread_samples_2026-11-03.json").read_text())["spread_pips"].items()}


def add_discount(ev):
    """Position of the zone top in the last 50 TF bars' high-low range at creation (mirrored for sells)."""
    from bensim.backtest import load_m5, resample
    ev["fib_pos"] = np.nan
    for sym, g in ev.groupby("sym"):
        raw = load_m5(sym, "2021-01-01", "2026-12-31")
        for d in (1, -1):
            m = raw if d == 1 else {"t": raw["t"], "open": -raw["open"], "high": -raw["low"], "low": -raw["high"], "close": -raw["close"]}
            for tf, sec in TFS.items():
                b = resample(m, sec)
                sel = g[(g.dir == d) & (g.tf == tf)]
                i = np.searchsorted(b["t"] + sec, sel.created.to_numpy())  # bar whose close = created
                lo_ = np.array([b["low"][max(0, k - 50):k + 1].min() for k in i])
                hi_ = np.array([b["high"][max(0, k - 50):k + 1].max() for k in i])
                top = b["low"][i]
                ev.loc[sel.index, "fib_pos"] = (top - lo_) / np.where(hi_ > lo_, hi_ - lo_, np.nan)
    return ev


def main():
    f = OUT / "first_table_events.pkl"  # our own local cache
    if not f.exists():
        parts = []
        for tag in ("untouched", "discovery"):
            e = pd.read_csv(OUT / f"zone_map_events_{tag}.csv", low_memory=False)
            e = e[e.tf.isin(TFS) & e.touched.astype(str).eq("True")].copy()
            e["period"] = "2021-24" if tag == "untouched" else "2024-26"
            parts.append(e)
        ev = add_discount(pd.concat(parts, ignore_index=True))
        ev.to_pickle(f)
    ev = pd.read_pickle(f)
    ev["bos"] = ev.bos.astype(str).eq("True")
    ev["news_touch"] = ev.news_touch.astype(str).eq("True")
    ev["cost_R"] = (ev.sym.map(lambda s: SPR.get(s, 0.5)) + 0.2) / ev.stop_pips
    ev["gross"] = ev.R_zone_3R + ev.cost_R
    ev["age_bars"] = ev.hours_to_touch * 3600 / ev.tf.map(TFS)
    R = "R_zone_3R"

    print("A) THE FIRST TABLE, BOTH PERIODS: enter at the zone edge, stop under the liquidity, 1:3\n")
    print(f"{'TF':3s} {'period':8s} {'trades':>6s} {'win%':>6s} {'need%':>6s} {'gross R':>8s} {'costs R':>8s} {'net R/tr':>8s} {'total R':>8s} {'luck band +-':>12s} {'stop pips':>9s}")
    for tf in TFS:
        for per in ("2021-24", "2024-26"):
            x = ev[(ev.tf == tf) & (ev.period == per)]
            r = x[R]
            need = 100 * (1 + x.cost_R.mean()) / (3 + 1)  # break-even win rate for 1:3 after costs
            band = 2 * r.std() * np.sqrt(len(r))
            print(f"{tf:3s} {per:8s} {len(r):6d} {100 * (r > 0).mean():5.1f}% {need:5.1f}% {x.gross.mean():+8.3f} {-x.cost_R.mean():+8.3f} "
                  f"{r.mean():+8.3f} {r.sum():+8.1f} {band:12.0f} {x.stop_pips.median():9.0f}")
    print("   need% = win rate needed to break even at 1:3 after costs; luck band = +-2 standard deviations of the total:")
    print("   a total INSIDE the band is what pure chance produces with no edge at all.\n")

    print("B) WHAT WE LEARNED SINCE, ADDED TO THE SAME TRADE (avg R per trade; both periods must agree)")
    f_ = {
        "base (the first table)": pd.Series(True, index=ev.index),
        "impulse made a BOS": ev.bos,
        "liquidity far (> 2 ATR under the zone)": ev.liq_atr > 2,
        "Fibonacci DISCOUNT (zone in the lower half of the range)": ev.fib_pos < 0.5,
        "Fibonacci PREMIUM (zone in the upper half)": ev.fib_pos >= 0.5,
        "zone older than 10 bars at the touch": ev.age_bars >= 10,
        "touch in London/NY (7-17 UTC)": ev.touch_session.isin(["London 7-12", "NY 12-17"]),
        "no high-impact news +-1h at the touch": ~ev.news_touch,
        "4H + 1D trend same way": (ev.trend_H4 == 1) & (ev.trend_D1 == 1),
        "BOS + far liquidity + discount": ev.bos & (ev.liq_atr > 2) & (ev.fib_pos < 0.5),
        "BOS + far liquidity + discount + London/NY": ev.bos & (ev.liq_atr > 2) & (ev.fib_pos < 0.5) & ev.touch_session.isin(["London 7-12", "NY 12-17"]),
    }
    print(f"{'filter':58s} " + " ".join(f"{tf + ' ' + p:>16s}" for tf in TFS for p in ("21-24", "24-26")))
    for lab, m in f_.items():
        cells = []
        for tf in TFS:
            for per in ("2021-24", "2024-26"):
                r = ev[m & (ev.tf == tf) & (ev.period == per)][R]
                cells.append(f"{r.mean():+.3f} n{len(r):<5d}" if len(r) >= 30 else f"{'-':>16s}")
        print(f"{lab:58s} " + " ".join(f"{c:>16s}" for c in cells))
    print("   (R per trade; +0.10 R/trade over 500 trades = +50R)\n")

    print("C) WHY 4H LOOKED OK: the same trade, each pair, both periods (total R)")
    for tf in TFS:
        x = ev[ev.tf == tf]
        print(f"   {tf}: " + ", ".join(f"{s} {g[g.period == '2021-24'][R].sum():+.0f}/{g[g.period == '2024-26'][R].sum():+.0f}"
                                     for s, g in x.groupby("sym")))


if __name__ == "__main__":
    main()
