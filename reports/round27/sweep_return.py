"""Round 27c (2026-11-11): SWEEP THEN RETURN with MSS / BOS (user: "we need a pattern to form a new thing").
Setup (bullish; bearish = mirror): a zone (FVG + the liquidity it started from, as zone_pattern.py) on H1 / H4 / D1.
Price SWEEPS the liquidity (trades below it) within AGE days of the zone forming. Then on the lower TF
(M15 for H1 zones, H1 for H4, H4 for D1), within DEAD days of the sweep:
  RECLAIM  a lower-TF close back above the zone top                         -> buy at that close
  MSS      a lower-TF close above the last swing high made before the sweep low -> buy at that close
  MSS_FVG  after the MSS, limit at the top of the newest lower-TF FVG inside the MSS leg (valid 24 lower-TF bars)
  BOS      after the MSS, a close above the next swing high that forms (second break) -> buy at that close
Stop = the sweep low (lowest low up to the entry) - buffer (5 pips FX, 0.05 JPY, $3 gold).
Targets: 2R | 3R | LIQ = the high price fell from into the zone (highest high between zone and sweep), only if >= 1R.
M5 bid bars, costs = spread + 0.2 pip in R, stop first on the same bar, max hold 20 days (marked at close).
Swing = 2-bar fractal, used only after both right bars closed. 2024-11-07 .. 2026-11-07, 7 pairs.
NOTE: 3 TF x 4 entries x 3 targets = 36 combinations on the same data -- a winner here still needs the untouched
2021-2024 check before anything goes live.
    python reports/round27/sweep_return.py
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
OUT = Path(ROOT + "/reports/round27")
SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "XAUUSD", "USDJPY", "USDCHF", "USDCAD"]
TFS = {"H1": 3600, "H4": 14400, "D1": 86400}
LTF = {"H1": 900, "H4": 3600, "D1": 14400}
AGE = {"H1": 10, "H4": 30, "D1": 90}
DEAD = {"H1": 2, "H4": 5, "D1": 10}
S, E = pd.Timestamp("2024-11-07", tz="UTC").timestamp(), pd.Timestamp("2026-11-07", tz="UTC").timestamp()
DAY, WIN = 86400, 20 * 86400
ENTRIES, TPS = ("RECLAIM", "MSS", "MSS_FVG", "BOS"), ("2R", "3R", "LIQ")
SPR = {s: float(np.mean(v)) for s, v in json.loads(Path(ROOT + "/data/costs/spread_samples_2026-11-03.json").read_text())["spread_pips"].items()}


def pip(sym):
    return 0.01 if sym == "USDJPY" else 0.1 if sym == "XAUUSD" else 1e-4


def first(mask):
    return int(np.argmax(mask)) if mask.any() else -1


def atr14(b):
    h, lo, c = b["high"], b["low"], b["close"]
    tr = np.maximum(h[1:], c[:-1]) - np.minimum(lo[1:], c[:-1])
    return np.r_[np.nan, pd.Series(tr).rolling(14).mean().to_numpy()]


def pivots_high(h, k=2):
    i = np.arange(k, len(h) - k)
    ok = np.ones(len(i), bool)
    for j in range(1, k + 1):
        ok &= (h[i] >= h[i - j]) & (h[i] > h[i + j])
    return i[ok]


def sim(m, z, entry, sl, tp):
    """R (before costs) from M5 index z; long only (prices are mirrored for sells)."""
    t, hi, lo, cl = m["t"], m["high"], m["low"], m["close"]
    end = np.searchsorted(t, t[z] + WIN)
    a, b = first(lo[z:end] <= sl), first(hi[z:end] >= tp)
    A, B = (a if a >= 0 else 10**9), (b if b >= 0 else 10**9)
    if A == B == 10**9:
        return (cl[end - 1] - entry) / (entry - sl)
    return -1.0 if A <= B else (tp - entry) / (entry - sl)


def run_sym(sym):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # below normal
    from bensim.backtest import load_m5, resample
    raw = load_m5(sym, "2024-06-01", "2026-11-11")
    buf, cost_px = (3.0 if sym == "XAUUSD" else 5 * pip(sym)), (SPR.get(sym, 0.5) + 0.2) * pip(sym)
    rows, funnel = [], []
    for d in (1, -1):
        m = raw if d == 1 else {"t": raw["t"], "open": -raw["open"], "high": -raw["low"], "low": -raw["high"], "close": -raw["close"]}
        t, hi, lo = m["t"], m["high"], m["low"]
        for tf, sec in TFS.items():
            zb = resample(m, sec)
            at = atr14(zb)
            L = resample(m, LTF[tf])
            lsec = LTF[tf]
            P = pivots_high(L["high"])
            for i in range(8, len(zb["t"])):
                gap = zb["low"][i] - zb["high"][i - 2]
                zt = zb["t"][i] + sec
                if not (gap > 0 and gap >= 0.3 * at[i] and S <= zt < E):
                    continue
                top, liq = zb["low"][i], zb["low"][i - 6:i - 1].min()
                z0 = np.searchsorted(t, zt)
                s = first(lo[z0:np.searchsorted(t, zt + AGE[tf] * DAY)] < liq)
                base = dict(sym=sym, dir=d, tf=tf, created=zt, gap_pips=gap / pip(sym), gap_atr=gap / at[i], swept=s >= 0)
                if s < 0:
                    funnel.append(base)
                    continue
                s += z0
                liq_tp = hi[z0:s].max() if s > z0 else np.nan  # the high price fell from into the zone
                js = np.searchsorted(L["t"], t[s], "right") - 1
                jdead = min(np.searchsorted(L["t"], t[s] + DEAD[tf] * DAY), len(L["t"]))
                lowv, low_i = L["low"][js], js
                got = {}
                for j in range(js, jdead):
                    if L["low"][j] < lowv:
                        lowv, low_i = L["low"][j], j
                    c = L["close"][j]
                    if "RECLAIM" not in got and c > top:
                        got["RECLAIM"] = (j, c, lowv)
                    if "MSS" not in got:
                        cand = P[(P < low_i) & (P + 2 <= j)]
                        if len(cand) and c > L["high"][cand[-1]]:
                            got["MSS"] = (j, c, lowv)
                            mss_low_i, mss_j = low_i, j
                    elif "BOS" not in got:
                        if L["low"][j] < got["MSS"][2]:
                            break  # the sweep low was taken again: setup dead
                        cand = P[(P >= mss_j) & (P + 2 <= j)]
                        if len(cand) and c > L["high"][cand[0]]:
                            got["BOS"] = (j, c, got["MSS"][2])
                    if "RECLAIM" in got and "BOS" in got:
                        break
                if "MSS" in got:  # MSS_FVG: newest lower-TF FVG inside the MSS leg, limit at its top
                    fv = [L["low"][k] for k in range(mss_low_i + 2, mss_j + 1) if L["low"][k] > L["high"][k - 2]]
                    if fv:
                        z1 = np.searchsorted(t, L["t"][mss_j] + lsec)
                        z2 = np.searchsorted(t, L["t"][mss_j] + lsec * 25)
                        f = first(lo[z1:z2] <= fv[-1])
                        if f >= 0:
                            got["MSS_FVG"] = (None, fv[-1], got["MSS"][2], z1 + f)
                funnel.append({**base, **{f"has_{k}": k in got for k in ENTRIES},
                               "hours_to_mss": (L["t"][got["MSS"][0]] + lsec - t[s]) / 3600 if "MSS" in got else np.nan})
                for ent, v in got.items():
                    entry, sl = v[1], v[2] - buf
                    z = v[3] if ent == "MSS_FVG" else np.searchsorted(t, L["t"][v[0]] + lsec)
                    if z >= len(t) - 1 or entry <= sl:
                        continue
                    st = entry - sl
                    row = dict(base, entry_type=ent, t_entry=t[z], stop_pips=st / pip(sym), sweep_pips=(liq - v[2]) / pip(sym))
                    for tp_name, tp in (("2R", entry + 2 * st), ("3R", entry + 3 * st), ("LIQ", liq_tp)):
                        ok = tp_name != "LIQ" or (np.isfinite(tp) and tp - entry >= st)
                        row[tp_name] = sim(m, z, entry, sl, tp) - cost_px / st if ok else np.nan
                    rows.append(row)
    return rows, funnel


def main():
    f, g = OUT / "sweep_return_trades.csv", OUT / "sweep_return_funnel.csv"
    if not f.exists():
        with ProcessPoolExecutor(4) as ex:
            parts = list(ex.map(run_sym, SYMS))
        pd.DataFrame([r for p in parts for r in p[0]]).to_csv(f, index=False)
        pd.DataFrame([r for p in parts for r in p[1]]).to_csv(g, index=False)
    tr, fu = pd.read_csv(f), pd.read_csv(g)
    tr["year"] = np.where(pd.to_datetime(tr.t_entry, unit="s") < "2025-11-07", "Y1", "Y2")
    print("SWEEP THEN RETURN: zone -> liquidity swept -> lower-TF confirmation -> entry; stop under the sweep low\n")
    print("== How often it happens")
    for tf in TFS:
        x = fu[fu.tf == tf]
        sw = x[x.swept.astype(str) == "True"]
        has = lambda k: sw[f"has_{k}"].astype(str).eq("True")
        print(f"   {tf}: {len(x)} zones, liquidity swept {100 * len(sw) / len(x):.0f}%; after the sweep: reclaim {100 * has('RECLAIM').mean():.0f}%, "
              f"MSS {100 * has('MSS').mean():.0f}% (median {sw.hours_to_mss.median():.0f} h after the sweep), "
              f"MSS+FVG fill {100 * has('MSS_FVG').mean():.0f}%, BOS {100 * has('BOS').mean():.0f}%")
    print("\n== Results (R per trade = same $ risk; $ at $1k risk per trade). * = both years positive")
    print(f"{'TF':3s} {'entry':8s} {'target':6s} {'n':>5s} {'win%':>6s} {'avg R':>7s} {'total R':>8s} {'Y1':>7s} {'Y2':>7s} {'stop pips':>9s} {'$ at $1k':>10s}")
    best = []
    for tf in TFS:
        for ent in ENTRIES:
            x = tr[(tr.tf == tf) & (tr.entry_type == ent)]
            for tp in TPS:
                y = x[x[tp].notna()]
                if len(y) < 20:
                    continue
                y1, y2 = y[y.year == "Y1"][tp].sum(), y[y.year == "Y2"][tp].sum()
                star = "*" if y1 > 0 and y2 > 0 else " "
                print(f"{tf:3s} {ent:8s} {tp:6s} {len(y):5d} {100 * (y[tp] > 0).mean():5.1f}% {y[tp].mean():+7.3f} {y[tp].sum():+8.1f} "
                      f"{y1:+7.1f} {y2:+7.1f} {y.stop_pips.median():9.1f} {1000 * y[tp].sum():+10,.0f} {star}")
                best.append((y[tp].sum(), tf, ent, tp, star))
        print()
    print("== Top 3 by total R: split by imbalance size (gap in ATR of its own TF), buys/sells, pair")
    for tot, tf, ent, tp, star in sorted(best, reverse=True)[:3]:
        y = tr[(tr.tf == tf) & (tr.entry_type == ent) & tr[tp].notna()].copy()
        y["size"] = pd.cut(y.gap_atr, [0, 0.6, 1, 2, 99], labels=["small <0.6", "0.6-1", "1-2", "big >2"])
        print(f" {tf} {ent} {tp} (total {tot:+.0f}R{', both years +' if star == '*' else ''}); median imbalance {y.gap_pips.median():.0f} pips, "
              f"median sweep {y.sweep_pips.median():.0f} pips below the liquidity")
        for lab, z in y.groupby("size", observed=True):
            print(f"    {lab:11s} {len(z):5d} trades, median {z.gap_pips.median():5.0f} pips, win {100 * (z[tp] > 0).mean():5.1f}%, "
                  f"{z[tp].mean():+.3f} R/trade, total {z[tp].sum():+.0f}R")
        print("    buys {:+.0f}R / sells {:+.0f}R | ".format(y[y.dir == 1][tp].sum(), y[y.dir == -1][tp].sum())
              + ", ".join(f"{s} {z[tp].sum():+.0f}" for s, z in y.groupby("sym")))


if __name__ == "__main__":
    main()
