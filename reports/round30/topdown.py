"""Round 30 (2026-11-11): Fibonacci premium/discount + OTE, top-down (HTF zone, LTF 5m/15m entry). Pre-registration: PREREG.md.
Bullish (bearish = mirrored prices):
  dealing range  starts at an HTF BOS (close above the last confirmed swing high); low = last confirmed HTF swing low,
                 high = running high (M5 precise) until the pullback reaches the zone, then frozen
  zones          DISC = 0.5 (equilibrium) | OTE = 0.62 | DEEP = 0.79 retracement of the range
  htf_fvg        the zone touch is inside a bullish HTF FVG formed in the leg (low -> high)
  resolution     NEW HIGH (above the frozen range high) or BROKE LOW (below the range low) first, max 30 HTF bars
Trades:
  FIB_LIMIT      buy limit at 0.705 of the range, stop below the range low, target = range high (pure Fibonacci)
  LTF model      after the zone touch, LTF (M5 / M15) MSS = close above the last LTF swing high made before the low in
                 the zone; entry at the MSS close (MSS) or a limit at the newest LTF FVG top/CE in the MSS leg (24 LTF bars);
                 stop = LTF low in the zone - 1 pip ($1 gold); targets RH (range high) | EXT (-0.27 extension) | 2R | 3R
Periods: untouched 2021-10-04..2024-11-07 and discovery 2024-11-07..2026-11-03, reported separately.
    python reports/round30/topdown.py
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
OUT = Path(ROOT + "/reports/round30")
SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "XAUUSD", "USDJPY", "USDCHF", "USDCAD"]
PAIRS = [("D1", "M15"), ("H4", "M15"), ("H4", "M5"), ("H1", "M5"), ("H1", "M15")]
SEC = {"M5": 300, "M15": 900, "H1": 3600, "H4": 14400, "D1": 86400}
ZONES = {"DISC": 0.5, "OTE": 0.62, "DEEP": 0.79}
SPLIT = pd.Timestamp("2024-11-07", tz="UTC").timestamp()
START, END = "2021-10-04", "2026-11-03"
SPR = {s: float(np.mean(v)) for s, v in json.loads(Path(ROOT + "/data/costs/spread_samples_2026-11-03.json").read_text())["spread_pips"].items()}


def pip(sym):
    return 0.01 if sym == "USDJPY" else 0.1 if sym == "XAUUSD" else 1e-4


def first(mask):
    return int(np.argmax(mask)) if mask.any() else -1


def pivots(x, high=True, k=2):
    y = x if high else -x
    i = np.arange(k, len(y) - k)
    ok = np.ones(len(i), bool)
    for j in range(1, k + 1):
        ok &= (y[i] >= y[i - j]) & (y[i] > y[i + j])
    return i[ok]


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
    raw = load_m5(sym, "2021-04-01", "2026-12-31")
    s_ts, e_ts = pd.Timestamp(START, tz="UTC").timestamp(), pd.Timestamp(END, tz="UTC").timestamp()
    buf, cost_px = (1.0 if sym == "XAUUSD" else pip(sym)), (SPR.get(sym, 0.5) + 0.2) * pip(sym)
    ranges, trades = [], []
    for d in (1, -1):
        m = raw if d == 1 else {"t": raw["t"], "open": -raw["open"], "high": -raw["low"], "low": -raw["high"], "close": -raw["close"]}
        t, hi, lo, cl = m["t"], m["high"], m["low"], m["close"]
        bars = {tf: (m if tf == "M5" else resample(m, s)) for tf, s in SEC.items()}
        piv = {tf: (pivots(bars[tf]["high"]), pivots(bars[tf]["low"], high=False)) for tf in SEC}
        for htf in ("D1", "H4", "H1"):
            B, hs = bars[htf], SEC[htf]
            pH, pL = piv[htf]
            n = len(B["t"])
            j, busy_until = 10, 0
            while j < n - 1:
                ct = B["t"][j] + hs
                if ct < busy_until or not s_ts <= ct < e_ts:
                    j += 1
                    continue
                qh = pH[pH + 2 <= j - 1]
                ql = pL[pL + 2 <= j]
                if not len(qh) or not len(ql) or B["close"][j] <= B["high"][qh[-1]]:
                    j += 1
                    continue
                rl, li = B["low"][ql[-1]], ql[-1]  # BOS up: dealing range from the last swing low
                z0 = np.searchsorted(t, ct)
                zend = min(np.searchsorted(t, ct + 30 * hs), len(t) - 1)
                if z0 >= zend - 2 or B["high"][j] <= rl:
                    j += 1
                    continue
                rh_run = np.maximum.accumulate(np.r_[B["high"][li:j + 1].max(), hi[z0:zend]])[:-1]  # high before each M5 bar
                row = dict(sym=sym, dir=d, htf=htf, created=ct, range_pips=(rh_run[0] - rl) / pip(sym))
                res_t = t[zend]
                for zn, f in ZONES.items():
                    lvl = rh_run - f * (rh_run - rl)
                    q = first(lo[z0:zend] <= lvl)
                    row[f"hit_{zn}"] = q >= 0
                    if q < 0:
                        continue
                    q += z0
                    rh = rh_run[q - z0]
                    nb, nh = first(lo[q:zend] < rl), first(hi[q + 1:zend] > rh)
                    nh = nh + 1 if nh >= 0 else -1
                    row[f"{zn}_result"] = "BROKE LOW" if nb >= 0 and (nh < 0 or nb < nh) else "NEW HIGH" if nh >= 0 else "OPEN"
                    row[f"{zn}_hours"] = (t[q + (nh if row[f"{zn}_result"] == "NEW HIGH" else nb)] - t[q]) / 3600 if row[f"{zn}_result"] != "OPEN" else np.nan
                    # HTF FVG in the leg covering the touch level
                    lvl_q = lvl[q - z0]
                    fv = [(B["high"][k - 2], B["low"][k]) for k in range(li + 2, j + 1) if B["low"][k] > B["high"][k - 2]]
                    row[f"{zn}_htf_fvg"] = any(a_ <= lvl_q <= b_ + 1e-12 for a_, b_ in fv)
                    zres = q + (nb if row[f"{zn}_result"] == "BROKE LOW" else nh) if row[f"{zn}_result"] != "OPEN" else zend
                    if zn == "OTE":
                        res_t = t[zres]
                        # pure Fibonacci limit at 0.705, stop below the range low, target the range high
                        e705 = rh - 0.705 * (rh - rl)
                        f7 = first(lo[q:zend] <= e705)
                        if f7 >= 0 and q + f7 <= zres:
                            sl = rl - buf
                            tend = min(np.searchsorted(t, t[q + f7] + 30 * hs), len(t))
                            trades.append(dict(row_key=(sym, d, htf, ct), sym=sym, dir=d, htf=htf, ltf="-", zone="OTE", entry="FIB_LIMIT_0705",
                                               target="RH", htf_fvg=row["OTE_htf_fvg"], t_entry=t[q + f7],
                                               R=sim(t, hi, lo, cl, q + f7, e705, sl, rh, tend) - cost_px / (e705 - sl)))
                    # LTF entry model
                    for h2, ltf in PAIRS:
                        if h2 != htf:
                            continue
                        Lb, ls = bars[ltf], SEC[ltf]
                        P = piv[ltf][0]
                        js = np.searchsorted(Lb["t"], t[q], "right") - 1
                        jd = min(np.searchsorted(Lb["t"], t[zres] if zres < len(t) else t[-1], "right"), len(Lb["t"]) - 1)
                        lowv, low_i = Lb["low"][js], js
                        for jj in range(js, jd):
                            if Lb["low"][jj] < lowv:
                                lowv, low_i = Lb["low"][jj], jj
                            k_ = np.searchsorted(P, low_i) - 1
                            while k_ >= 0 and P[k_] + 2 > jj:
                                k_ -= 1
                            if k_ < 0 or Lb["close"][jj] <= Lb["high"][P[k_]]:
                                continue
                            sl = lowv - buf
                            zm = np.searchsorted(t, Lb["t"][jj] + ls)
                            tend = min(np.searchsorted(t, t[min(zm, len(t) - 1)] + 30 * hs), len(t))
                            ext = rh + 0.27 * (rh - rl)
                            fvs = [(Lb["high"][x - 2], Lb["low"][x]) for x in range(low_i + 2, jj + 1) if Lb["low"][x] > Lb["high"][x - 2]]
                            ents = {"MSS": (Lb["close"][jj], zm)}
                            if fvs:
                                for en, px in (("LTF_FVG_TOP", fvs[-1][1]), ("LTF_FVG_CE", (fvs[-1][0] + fvs[-1][1]) / 2)):
                                    zl = np.searchsorted(t, Lb["t"][jj] + ls * 25)
                                    ff = first(lo[zm:zl] <= px)
                                    if ff >= 0:
                                        ents[en] = (px, zm + ff)
                            for en, (px, ze) in ents.items():
                                if px <= sl or ze >= len(t) - 1:
                                    continue
                                risk = px - sl
                                for tn, tp in (("RH", rh), ("EXT", ext), ("2R", px + 2 * risk), ("3R", px + 3 * risk)):
                                    if tp <= px:
                                        continue
                                    trades.append(dict(sym=sym, dir=d, htf=htf, ltf=ltf, zone=zn, entry=en, target=tn,
                                                       htf_fvg=row[f"{zn}_htf_fvg"], t_entry=t[ze], stop_pips=risk / pip(sym),
                                                       R=sim(t, hi, lo, cl, ze, px, sl, tp, tend) - cost_px / risk,
                                                       # details for hand checks (prices in the MIRRORED space for sells: x -1)
                                                       t_bos=ct, range_low=rl, range_high=rh, t_zone=t[q], t_mss=Lb["t"][jj] + ls,
                                                       ltf_low=lowv, px=px, sl=sl, tp=tp, t_end=t[tend - 1], cost_R=cost_px / risk))
                            break
                ranges.append(row)
                busy_until = res_t  # one dealing range at a time per HTF
                j += 1
    return ranges, trades


def tstat(x):
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def main():
    fr, ft = OUT / "ranges.pkl", OUT / "trades.pkl"  # our own local cache written below (not external data)
    if not ft.exists():
        with ProcessPoolExecutor(4) as ex:
            parts = list(ex.map(run_sym, SYMS))
        pd.DataFrame([r for p in parts for r in p[0]]).to_pickle(fr)
        pd.DataFrame([r for p in parts for r in p[1]]).drop(columns=["row_key"], errors="ignore").to_pickle(ft)
    rg, tr = pd.read_pickle(fr), pd.read_pickle(ft)
    rg["period"] = np.where(rg.created < SPLIT, "untouched 2021-24", "discovery 2024-26")
    tr["period"] = np.where(tr.t_entry < SPLIT, "untouched 2021-24", "discovery 2024-26")
    print(f"Round 30: Fibonacci premium/discount + OTE, top-down. {len(rg)} dealing ranges, {len(tr)} trade rows, 7 pairs, buys + sells\n")
    print("1) THE PATTERN: after an HTF break of structure, the pullback into discount / OTE")
    for htf in ("D1", "H4", "H1"):
        for per, g in rg[rg.htf == htf].groupby("period"):
            parts = [f"{len(g)} ranges"]
            for zn in ZONES:
                h = g[g[f"hit_{zn}"].astype(bool)]
                nh = (h[f"{zn}_result"] == "NEW HIGH").mean()
                parts.append(f"{zn} {100 * len(h) / len(g):.0f}% -> new high {100 * nh:.0f}% / broke low {100 * (h[f'{zn}_result'] == 'BROKE LOW').mean():.0f}%")
            hf = g[g.hit_OTE.astype(bool)]
            w = hf[hf.OTE_htf_fvg.astype(bool)]
            wo = hf[~hf.OTE_htf_fvg.astype(bool)]
            parts.append(f"OTE inside an HTF FVG: new high {100 * (w.OTE_result == 'NEW HIGH').mean():.0f}% (n {len(w)}) vs not {100 * (wo.OTE_result == 'NEW HIGH').mean():.0f}% (n {len(wo)})")
            print(f"   {htf} {per}: " + " | ".join(parts))
    print("   (new high = price went on to the range high = the liquidity; broke low = the setup failed)\n")

    print("2) PRE-REGISTERED textbook model: H4 OTE -> M15 MSS -> limit at the M15 FVG 50% -> stop under the M15 low -> target H4 range high")
    pr = tr[(tr.htf == "H4") & (tr.ltf == "M15") & (tr.zone == "OTE") & (tr.entry == "LTF_FVG_CE") & (tr.target == "RH")]
    ok_all = True
    for per, g in pr.groupby("period"):
        ok = g.R.sum() > 0 and tstat(g.R) >= 2
        ok_all &= ok
        print(f"   {per}: n {len(g)}, win {100 * (g.R > 0).mean():.1f}%, {g.R.mean():+.3f} R/trade, total {g.R.sum():+.1f}R, t {tstat(g.R):.1f}, "
              f"median stop {g.stop_pips.median():.0f} pips -> {'PASS' if ok else 'FAIL'}")
    print(f"   VERDICT: {'PASS in both periods' if ok_all else 'FAIL'}\n")

    print("3) EXPLORATION GRID (both periods side by side; * = positive in both; only rows with n >= 60 per period)")
    print(f"   {'HTF':3s} {'LTF':3s} {'zone':4s} {'entry':14s} {'tgt':3s} {'fvg':4s} | {'untouched 2021-24':^30s} | {'discovery 2024-26':^30s}")
    rows = []
    for key, g in tr.groupby(["htf", "ltf", "zone", "entry", "target"]):
        for fv in ("all", "HTF FVG"):
            x = g if fv == "all" else g[g.htf_fvg.astype(bool)]
            a, b = x[x.period == "untouched 2021-24"].R, x[x.period == "discovery 2024-26"].R
            if len(a) < 60 or len(b) < 60:
                continue
            rows.append((min(tstat(a), tstat(b)), key, fv, a, b))
    for mt, key, fv, a, b in sorted(rows, key=lambda r: -r[0])[:30]:
        star = "*" if a.sum() > 0 and b.sum() > 0 else " "
        print(f"   {key[0]:3s} {key[1]:3s} {key[2]:4s} {key[3]:14s} {key[4]:3s} {fv[:4]:4s} | n {len(a):4d} {a.mean():+.3f}R {a.sum():+7.1f} t {tstat(a):4.1f} | "
              f"n {len(b):4d} {b.mean():+.3f}R {b.sum():+7.1f} t {tstat(b):4.1f} {star}")
    n_both = sum(1 for r in rows if r[3].sum() > 0 and r[4].sum() > 0)
    print(f"   {len(rows)} grid rows, {n_both} positive in both periods (rows sorted by the weaker period's t)")


if __name__ == "__main__":
    main()
