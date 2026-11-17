"""Round 32 (2026-11-11): the user's real idea (his GBPUSD daily screenshots): IMBALANCE = the big one-sided candle
(displacement), LIQUIDITY = the lows below it (his daily chart: the multi-month equal lows). The trade is to catch the
FILL BACK into the imbalance -- usually after price has taken the liquidity.
Imbalance (drop; a spike up = mirrored):  body >= 1.5 x the 20-bar average body and body >= 60% of the candle range.
  zone = the candle body (close .. open), CE = 50% of the body.
Liquidity below (not yet taken by the candle itself):
  NEAR  = the nearest confirmed 3-bar swing low below the candle low (last 120 bars)
  MAJOR = the lowest low of the last 120 bars (the range low -- his daily "liquidity" box), if below the candle low
Measured on the M5 path for 100 bars of the candle's timeframe:
  fill: touch (back to the candle close), CE (50% of the body), FULL (back to the candle open); bars until each
  sequence: FILL FIRST (CE before the liquidity) | LIQUIDITY THEN FILL (the user's pattern) | LIQUIDITY, NO FILL | NEITHER
Trades (BUY after a drop; costs = spread + 0.2 pip in R; stop first on the same bar; 100-bar limit):
  LIMIT_AT_LIQ  buy limit at the liquidity, stop 0.5 ATR below it, target = edge / CE / FULL of the imbalance
  RECLAIM       after the liquidity is taken, the first TF close back above it -> buy; stop under the sweep low - 1 pip
Periods: 2024-11-07..2026-11-03 (the 2 years asked for) and 2021-10-04..2024-11-07 (check), 7 IC pairs.
    python reports/round32/fill_pattern.py
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
OUT = Path(ROOT + "/reports/round32")
SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "XAUUSD", "USDJPY", "USDCHF", "USDCAD"]
TFS = {"M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "H12": 43200, "D1": 86400}
SPLIT = pd.Timestamp("2024-11-07", tz="UTC").timestamp()
SPR = {s: float(np.mean(v)) for s, v in json.loads(Path(ROOT + "/data/costs/spread_samples_2026-11-03.json").read_text())["spread_pips"].items()}


def pip(sym):
    return 0.01 if sym == "USDJPY" else 0.1 if sym == "XAUUSD" else 1e-4


def first(mask):
    return int(np.argmax(mask)) if mask.any() else -1


def sim(t, hi, lo, cl, z, entry, sl, tp, end):
    a, b = first(lo[z:end] <= sl), first(hi[z:end] >= tp)
    A, B = (a if a >= 0 else 10**9), (b if b >= 0 else 10**9)
    if A == B == 10**9:
        return (cl[end - 1] - entry) / (entry - sl)
    return -1.0 if A <= B else (tp - entry) / (entry - sl)


def run_sym(sym):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # below normal
    from bensim.backtest import load_m5, resample
    raw = load_m5(sym, "2021-01-01", "2026-12-31")
    s_ts, e_ts = pd.Timestamp("2021-10-04", tz="UTC").timestamp(), pd.Timestamp("2026-11-03", tz="UTC").timestamp()
    buf, cost_px = (1.0 if sym == "XAUUSD" else pip(sym)), (SPR.get(sym, 0.5) + 0.2) * pip(sym)
    rows = []
    for d in (1, -1):  # d = 1: a DROP (imbalance above, liquidity below, buy the fill); -1: a spike up, mirrored
        m = raw if d == 1 else {"t": raw["t"], "open": -raw["open"], "high": -raw["low"], "low": -raw["high"], "close": -raw["close"]}
        t, hi, lo, cl = m["t"], m["high"], m["low"], m["close"]
        for tf, sec in TFS.items():
            b = resample(m, sec)
            O, H, L, C, T = b["open"], b["high"], b["low"], b["close"], b["t"]
            body = np.abs(C - O)
            avg = pd.Series(body).rolling(20).mean().shift(1).to_numpy()
            tr = np.r_[np.nan, np.maximum(H[1:], C[:-1]) - np.minimum(L[1:], C[:-1])]
            atr = pd.Series(tr).rolling(14).mean().to_numpy()
            ii = np.arange(3, len(L) - 3)
            piv = ii[(L[ii] <= L[ii - 1]) & (L[ii] <= L[ii - 2]) & (L[ii] <= L[ii - 3]) & (L[ii] < L[ii + 1]) & (L[ii] < L[ii + 2]) & (L[ii] < L[ii + 3])]
            win = 100 * sec
            for i in range(130, len(T)):
                ct = T[i] + sec
                if not (s_ts <= ct < e_ts and O[i] > C[i] and body[i] >= 1.5 * avg[i] and body[i] >= 0.6 * (H[i] - L[i]) and np.isfinite(atr[i])):
                    continue
                z0 = np.searchsorted(t, ct)
                end = min(np.searchsorted(t, ct + win), len(t))
                if z0 >= end - 1:
                    continue
                zlo, zhi = C[i], O[i]
                ce = (zlo + zhi) / 2
                cand = piv[(piv + 3 <= i) & (piv >= i - 120)]
                below = L[cand][L[cand] < L[i]]
                liqs = {"NEAR": below.max() if len(below) else np.nan,
                        "MAJOR": L[i - 120:i].min() if L[i - 120:i].min() < L[i] else np.nan}
                f_touch, f_ce, f_full = first(hi[z0:end] >= zlo), first(hi[z0:end] >= ce), first(hi[z0:end] >= zhi)
                bars = lambda k: (t[z0 + k] - ct) / sec if k >= 0 else np.nan  # noqa: E731
                row = dict(sym=sym, dir=d, tf=tf, created=ct, body_atr=body[i] / atr[i], body_pips=body[i] / pip(sym),
                           touch=f_touch >= 0, ce=f_ce >= 0, full=f_full >= 0, bars_ce=bars(f_ce), bars_full=bars(f_full),
                           ce_hour=int((t[z0 + f_ce] % 86400) // 3600) if f_ce >= 0 else -1)
                for ln, lv in liqs.items():
                    if not np.isfinite(lv):
                        row[f"{ln}_seq"] = "NO LIQUIDITY"
                        continue
                    row[f"{ln}_dist_atr"] = (L[i] - lv) / atr[i]
                    hl = first(lo[z0:end] <= lv)
                    ce_after = first(hi[z0 + hl:end] >= ce) if hl >= 0 else -1
                    row[f"{ln}_seq"] = ("FILL FIRST" if f_ce >= 0 and (hl < 0 or f_ce < hl) else
                                        "LIQ THEN FILL" if hl >= 0 and ce_after >= 0 else "LIQ, NO FILL" if hl >= 0 else "NEITHER")
                    row[f"{ln}_bars_liq"] = bars(hl)
                    row[f"{ln}_bars_liq_to_fill"] = (t[z0 + hl + ce_after] - t[z0 + hl]) / sec if hl >= 0 and ce_after >= 0 else np.nan
                    if hl < 0 or (f_ce >= 0 and f_ce < hl):
                        continue  # no trade: liquidity never reached, or the fill came first
                    zl = z0 + hl
                    # LIMIT_AT_LIQ
                    sl = lv - 0.5 * atr[i]
                    for tn, tp in (("EDGE", zlo), ("CE", ce), ("FULL", zhi)):
                        r = sim(t, hi, lo, cl, zl, lv, sl, tp, end) - cost_px / (lv - sl)
                        row[f"{ln}_LIMIT_{tn}"] = r
                    # RECLAIM: first TF close back above the liquidity after the sweep (within 20 TF bars)
                    jb = np.searchsorted(T, t[zl], "right") - 1
                    for j in range(jb, min(jb + 20, len(T))):
                        if C[j] > lv and T[j] + sec > t[zl]:
                            ze = np.searchsorted(t, T[j] + sec)
                            sw = lo[zl:ze].min() - buf
                            if ze < end - 1 and C[j] > sw:
                                for tn, tp in (("EDGE", zlo), ("CE", ce), ("FULL", zhi)):
                                    if tp > C[j]:
                                        row[f"{ln}_RECLAIM_{tn}"] = sim(t, hi, lo, cl, ze, C[j], sw, tp, end) - cost_px / (C[j] - sw)
                                row[f"{ln}_reclaim_rr_ce"] = (ce - C[j]) / (C[j] - sw)
                            break
                rows.append(row)
    return rows


def tstat(x):
    x = x.dropna()
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def main():
    f = OUT / "fill_events.pkl"  # our own local cache
    if not f.exists():
        with ProcessPoolExecutor(4) as ex:
            rows = [r for part in ex.map(run_sym, SYMS) for r in part]
        pd.DataFrame(rows).to_pickle(f)
    ev = pd.read_pickle(f)
    ev["period"] = np.where(ev.created >= SPLIT, "2024-26", "2021-24")
    for c in ("touch", "ce", "full"):
        ev[c] = ev[c].astype(bool)
    print("Round 32: imbalance = big one-sided candle, liquidity = the lows beyond it; do imbalances FILL BACK, and when?\n")
    print("1) HOW OFTEN AND WHEN THE IMBALANCE FILLS BACK (100 bars of its own timeframe)")
    print(f"   {'TF':4s} {'period':8s} {'imbalances':>10s} {'touched':>8s} {'50% fill':>8s} {'full fill':>9s} {'bars to 50%':>11s} {'bars to full':>12s}")
    for tf in TFS:
        for per in ("2024-26", "2021-24"):
            x = ev[(ev.tf == tf) & (ev.period == per)]
            print(f"   {tf:4s} {per:8s} {len(x):10d} {100 * x.touch.mean():7.0f}% {100 * x.ce.mean():7.0f}% {100 * x.full.mean():8.0f}% "
                  f"{x.bars_ce.median():11.1f} {x.bars_full.median():12.1f}")
    print("\n2) THE LIQUIDITY: what happens first? (CE = 50% fill of the imbalance)")
    for ln in ("NEAR", "MAJOR"):
        print(f"   == {ln} liquidity ({'nearest swing low' if ln == 'NEAR' else 'lowest low of the last 120 bars'})")
        for tf in TFS:
            for per in ("2024-26", "2021-24"):
                x = ev[(ev.tf == tf) & (ev.period == per)]
                s = x[f"{ln}_seq"].value_counts(normalize=True)
                lt = x[x[f"{ln}_seq"] == "LIQ THEN FILL"]
                taken = x[x[f"{ln}_seq"].isin(["LIQ THEN FILL", "LIQ, NO FILL"])]
                print(f"   {tf:4s} {per}: fill first {100 * s.get('FILL FIRST', 0):3.0f}% | liquidity then fill {100 * s.get('LIQ THEN FILL', 0):3.0f}% | "
                      f"liquidity, no fill {100 * s.get('LIQ, NO FILL', 0):3.0f}% | neither {100 * s.get('NEITHER', 0):3.0f}% | none below {100 * s.get('NO LIQUIDITY', 0):3.0f}%"
                      f"  -> once the liquidity is taken, fills back {100 * len(lt) / max(1, len(taken)):3.0f}% "
                      f"(median {lt[f'{ln}_bars_liq_to_fill'].median():.0f} bars later)")
    print("\n3) THE TRADE: catch the fill after the liquidity is taken (R per trade = same $ risk; * = positive in both periods)")
    print(f"   {'TF':4s} {'liq':5s} {'entry':8s} {'target':6s} | {'2024-26':^36s} | {'2021-24':^36s}")
    best = []
    for tf in TFS:
        for ln in ("NEAR", "MAJOR"):
            for en in ("LIMIT", "RECLAIM"):
                for tn in ("EDGE", "CE", "FULL"):
                    c = f"{ln}_{en}_{tn}"
                    if c not in ev:
                        continue
                    a = ev[(ev.tf == tf) & (ev.period == "2024-26")][c].dropna()
                    b = ev[(ev.tf == tf) & (ev.period == "2021-24")][c].dropna()
                    if len(a) < 30 or len(b) < 30:
                        continue
                    star = "*" if a.sum() > 0 and b.sum() > 0 else " "
                    print(f"   {tf:4s} {ln:5s} {en:8s} {tn:6s} | n {len(a):5d} win {100 * (a > 0).mean():4.0f}% {a.mean():+.3f}R {a.sum():+7.1f} t {tstat(a):+4.1f} | "
                          f"n {len(b):5d} win {100 * (b > 0).mean():4.0f}% {b.mean():+.3f}R {b.sum():+7.1f} t {tstat(b):+4.1f} {star}")
                    best.append((min(tstat(a), tstat(b)), tf, c, a.sum(), b.sum()))
        print()
    print("4) PATTERN: what makes the fill more likely? (2024-26 / 2021-24, % of imbalances reaching 50% fill)")
    for tf in ("H1", "H4", "D1"):
        x = ev[ev.tf == tf].copy()
        x["size"] = pd.cut(x.body_atr, [0, 1, 1.5, 2.5, 99], labels=["<1 ATR", "1-1.5", "1.5-2.5", ">2.5 ATR"])
        parts = []
        for v, g in x.groupby("size", observed=True):
            parts.append(f"{v}: {100 * g[g.period == '2024-26'].ce.mean():.0f}/{100 * g[g.period == '2021-24'].ce.mean():.0f}%")
        sess = x[x.ce].ce_hour
        print(f"   {tf}: by candle size: " + ", ".join(parts) + " | fills happen most in UTC hours: "
              + ", ".join(f"{h}h" for h in sess.value_counts().index[:4]))


if __name__ == "__main__":
    main()
