"""Round 27b (2026-11-11): the user's drawn pattern (GBPUSD pictures): an imbalance (FVG) zone with the liquidity (the
low the impulse started from) under it, on 1H, 4H and 1D. Price comes back to the zone: does it hold, go through it,
sweep the liquidity and come back? When does the imbalance get filled? Do 2-3 stacked zones (1H inside 4H/1D) hold better?
2 years 2024-11-07 .. 2026-11-07, 7 pairs, both directions (bullish zone = buy, bearish zone = sell, mirrored).
Zone (bullish): FVG at bar i: low[i] > high[i-2], gap >= 0.3 ATR14; top = low[i], bottom = high[i-2];
  liquidity = lowest low of bars i-6..i-2 (where the impulse started). Created at the close of bar i.
On M5 after creation:
  touch      first time price trades into the zone (low <= top)
  trade      buy at top, stop = liquidity - 5 pips ($3 gold), target 1:3; costs = spread + 0.2 pip in R
  HELD       target before stop; BROKE = stop first (same bar = stop); OPEN = neither in 20 days (marked at close)
  filled     price reached the zone bottom (imbalance fully filled) -- before the target / stop
  came back  after BROKE: price back above the zone top within 5 / 20 days (liquidity sweep then return)
  stacked    at the touch, an active (not yet broken) HIGHER-TF zone overlaps the trade range [stop, top]
    python reports/round27/zone_pattern.py
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
S, E = pd.Timestamp("2024-11-07", tz="UTC").timestamp(), pd.Timestamp("2026-11-07", tz="UTC").timestamp()
WIN, DAY = 20 * 86400, 86400
SPR = {s: float(np.mean(v)) for s, v in json.loads(Path(ROOT + "/data/costs/spread_samples_2026-11-03.json").read_text())["spread_pips"].items()}


def pip(sym):
    return 0.01 if sym == "USDJPY" else 0.1 if sym == "XAUUSD" else 1e-4


def first(mask):
    return int(np.argmax(mask)) if mask.any() else -1


def zones(b, sec):
    """Bullish zones on (high, low) arrays: (created_t, top, bottom, liq)."""
    h, lo, c = b["high"], b["low"], b["close"]
    tr = np.maximum(h[1:], c[:-1]) - np.minimum(lo[1:], c[:-1])
    atr = np.r_[np.nan, pd.Series(tr).rolling(14).mean().to_numpy()]
    out = []
    for i in range(8, len(h)):
        gap = lo[i] - h[i - 2]
        if gap > 0 and gap >= 0.3 * atr[i]:
            out.append((b["t"][i] + sec, lo[i], h[i - 2], lo[i - 6:i - 1].min()))
    return out


def run_sym(sym):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # below normal
    from bensim.backtest import load_m5, resample
    raw = load_m5(sym, "2024-08-01", "2026-11-11")
    buf, cost_px = (3.0 if sym == "XAUUSD" else 5 * pip(sym)), (SPR.get(sym, 0.5) + 0.2) * pip(sym)
    rows = []
    for d in (1, -1):  # bearish = mirror: negate prices, swap high/low
        m = raw if d == 1 else {"t": raw["t"], "open": -raw["open"], "high": -raw["low"], "low": -raw["high"], "close": -raw["close"]}
        t, hi, lo, cl = m["t"], m["high"], m["low"], m["close"]
        allz = []
        for tf, sec in TFS.items():
            for zt, top, bot, liq in zones(resample(m, sec), sec):
                sl = liq - buf
                z0 = np.searchsorted(t, zt)
                brk = first(lo[z0:] <= sl)
                allz.append(dict(tf=tf, created=zt, top=top, bot=bot, sl=sl, z0=z0,
                                 t_break=t[z0 + brk] if brk >= 0 else 2**40))
        zf = pd.DataFrame(allz)
        for r in allz:
            if not S <= r["created"] < E:
                continue
            z0, top, bot, sl = r["z0"], r["top"], r["bot"], r["sl"]
            k = first(lo[z0:] <= top)
            row = dict(sym=sym, dir=d, tf=r["tf"], created=r["created"], touched=k >= 0,
                       stop_pips=(top - sl) / pip(sym), gap_pips=(top - bot) / pip(sym))
            if k < 0:
                rows.append(row)
                continue
            k += z0
            row["days_to_touch"] = (t[k] - r["created"]) / DAY
            end = np.searchsorted(t, t[k] + WIN)
            st = top - sl
            tp = top + 3 * st
            a, b, f = first(lo[k:end] <= sl), first(hi[k:end] >= tp), first(lo[k:end] <= bot)
            A, B = (a if a >= 0 else 10**9), (b if b >= 0 else 10**9)
            if A == B == 10**9:
                res, R = "OPEN", (cl[end - 1] - top) / st
            elif A <= B:
                res, R = "BROKE", -1.0
            else:
                res, R = "HELD", 3.0
            row.update(result=res, R=R - cost_px / st, filled=f >= 0 and f <= min(A, B),
                       days_to_fill=(t[k + f] - r["created"]) / DAY if f >= 0 else np.nan)
            if res == "BROKE":
                ab = k + A
                back = first(hi[ab:np.searchsorted(t, t[ab] + WIN)] >= top)
                row["back_days"] = (t[ab + back] - t[ab]) / DAY if back >= 0 else np.nan
            # stacked: an active higher-TF zone overlapping [sl, top] at the touch time
            order = list(TFS)
            higher = order[order.index(r["tf"]) + 1:]
            tk = t[k]
            for h in ("H4", "D1"):
                row[f"in_{h}"] = bool(h in higher and ((zf.tf == h) & (zf.created < tk) & (zf.t_break > tk)
                                                       & (zf.top >= sl) & (zf.bot <= top)).any())
            rows.append(row)
    return rows


def main():
    f = OUT / "zone_pattern_events.csv"
    if not f.exists():
        with ProcessPoolExecutor(4) as ex:
            rows = [r for part in ex.map(run_sym, SYMS) for r in part]
        pd.DataFrame(rows).to_csv(f, index=False)
    ev = pd.read_csv(f)
    for c in ("in_H4", "in_D1"):  # NaN on untouched rows makes these object columns; ~ on python bools is wrong
        ev[c] = ev[c].astype(str).eq("True")
    ev["year"] = np.where(pd.to_datetime(ev.created, unit="s") < "2025-11-07", "Y1", "Y2")
    print("Zones = imbalance (FVG) with the liquidity under it; trade = enter at the zone edge, stop below the liquidity, 1:3\n")
    for tf in TFS:
        g = ev[ev.tf == tf]
        tch = g[g.touched]
        br = tch[tch.result == "BROKE"]
        held = tch[tch.result == "HELD"]
        print(f"== {tf}: {len(g)} zones, {100 * len(tch) / len(g):.0f}% came back to the zone "
              f"(median {tch.days_to_touch.median():.1f} days after it formed); median stop {tch.stop_pips.median():.0f} pips")
        print(f"   HELD (1:3 target first) {100 * len(held) / len(tch):.1f}%   BROKE (went through, took the liquidity) "
              f"{100 * len(br) / len(tch):.1f}%   open after 20 days {100 * (tch.result == 'OPEN').mean():.1f}%")
        print(f"   imbalance fully filled at/after the touch: {100 * tch.filled.mean():.0f}% of touches "
              f"(median {tch.days_to_fill.median():.1f} days after forming); held even after a full fill: "
              f"{100 * (held.filled.sum()) / max(1, tch.filled.sum()):.0f}%")
        print(f"   after BREAKING the liquidity, came back above the zone: within 5 days {100 * (br.back_days <= 5).mean():.0f}%, "
              f"within 20 days {100 * br.back_days.notna().mean():.0f}%")
        print(f"   trade result: {tch.R.mean():+.3f} R/trade, total {tch.R.sum():+.0f}R (Y1 {tch[tch.year == 'Y1'].R.sum():+.0f}, "
              f"Y2 {tch[tch.year == 'Y2'].R.sum():+.0f}) = {1000 * tch.R.sum():+,.0f} $ at $1k risk per trade")
        for name, m in (("buys", tch.dir == 1), ("sells", tch.dir == -1)):
            x = tch[m]
            print(f"      {name:5s}: {len(x):5d} trades, held {100 * (x.result == 'HELD').mean():.1f}%, {x.R.mean():+.3f} R/trade, total {x.R.sum():+.0f}R")
        if tf != "D1":
            print("   STACKED zones (the 1H/4H zone sits inside an active higher-TF zone):")
            for lab, m in (("alone", ~tch.in_H4 & ~tch.in_D1), ("+4H zone", tch.in_H4 & ~tch.in_D1),
                           ("+1D zone", ~tch.in_H4 & tch.in_D1), ("+4H +1D (3 zones)", tch.in_H4 & tch.in_D1)):
                x = tch[m]
                if len(x):
                    print(f"      {lab:18s} {len(x):5d} trades, held {100 * (x.result == 'HELD').mean():5.1f}%, broke "
                          f"{100 * (x.result == 'BROKE').mean():5.1f}%, {x.R.mean():+.3f} R/trade, total {x.R.sum():+6.0f}R "
                          f"(Y1 {x[x.year == 'Y1'].R.sum():+.0f}, Y2 {x[x.year == 'Y2'].R.sum():+.0f})")
        print("   by pair:  " + ", ".join(f"{s} {x.R.sum():+.0f}R" for s, x in tch.groupby("sym")))
        print()


if __name__ == "__main__":
    main()
