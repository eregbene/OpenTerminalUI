"""Round 28: imbalance / liquidity map on 5m..1D (wish list: reports/round28/PLAN.md).
One row per zone (FVG + the liquidity it started from), 7 pairs, both directions (bearish = mirrored prices),
discovery period 2024-11-07 .. 2026-11-03. Outcomes on the M5 path within W = 100 bars of the zone's own TF.
    python reports/round28/zone_map.py            # builds zone_map_events.csv once, then prints the report
    python reports/round28/zone_map.py --period 2021-10-04 2024-11-07 --tag untouched   # same rules, untouched years
"""
import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
OUT = Path(ROOT + "/reports/round28")
SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "XAUUSD", "USDJPY", "USDCHF", "USDCAD"]
TFS = {"M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "H12": 43200, "D1": 86400}
GROUP = {"M5": "LTF 5-30m", "M15": "LTF 5-30m", "M30": "LTF 5-30m", "H1": "MTF 1-4H", "H4": "MTF 1-4H", "H12": "HTF 12H-1D", "D1": "HTF 12H-1D"}
LTF = {"H1": 900, "H4": 3600, "H12": 3600, "D1": 14400}  # sweep-return confirmation timeframe
DEAD = {"H1": 2, "H4": 5, "H12": 7, "D1": 10}
WBARS = 100
DAY = 86400
CCY = {"EURUSD": "EUR USD", "GBPUSD": "GBP USD", "AUDUSD": "AUD USD", "XAUUSD": "USD", "USDJPY": "USD JPY",
       "USDCHF": "USD CHF", "USDCAD": "USD CAD"}
SPR = {s: float(np.mean(v)) for s, v in json.loads(Path(ROOT + "/data/costs/spread_samples_2026-11-03.json").read_text())["spread_pips"].items()}


def pip(sym):
    return 0.01 if sym == "USDJPY" else 0.1 if sym == "XAUUSD" else 1e-4


def first(mask):
    return int(np.argmax(mask)) if mask.any() else -1


def pivots(x, k=2, high=True):
    y = x if high else -x
    i = np.arange(k, len(y) - k)
    ok = np.ones(len(i), bool)
    for j in range(1, k + 1):
        ok &= (y[i] >= y[i - j]) & (y[i] > y[i + j])
    return i[ok]


def structure(b):
    """lastH/lastL = latest CONFIRMED swing high/low as of each bar; trend = +1 after a close above lastH, -1 below lastL."""
    n, h, lo, c = len(b["t"]), b["high"], b["low"], b["close"]
    out = {}
    for name, x, hi in (("H", h, True), ("L", lo, False)):
        p = pivots(x, high=hi)
        idx = np.searchsorted(p + 2, np.arange(n), "right") - 1
        out[name] = np.where(idx >= 0, x[p[np.maximum(idx, 0)]], np.nan)
    tr, cur = np.zeros(n, np.int8), 0
    for i in range(1, n):
        if c[i] > out["H"][i - 1]:
            cur = 1
        elif c[i] < out["L"][i - 1]:
            cur = -1
        tr[i] = cur
    return out["H"], out["L"], tr


def atr14(b):
    h, lo, c = b["high"], b["low"], b["close"]
    tr = np.maximum(h[1:], c[:-1]) - np.minimum(lo[1:], c[:-1])
    return np.r_[np.nan, pd.Series(tr).rolling(14).mean().to_numpy()]


def sim(m, z, entry, sl, tp, long=True, win=20 * DAY):
    """R before costs from M5 index z; long: sl below / tp above, short: the mirror. Same bar = stop first."""
    t, hi, lo, cl = m["t"], m["high"], m["low"], m["close"]
    end = max(np.searchsorted(t, t[z] + win), z + 1)
    if long:
        a, b = first(lo[z:end] <= sl), first(hi[z:end] >= tp)
    else:
        a, b = first(hi[z:end] >= sl), first(lo[z:end] <= tp)
    A, B = (a if a >= 0 else 10**9), (b if b >= 0 else 10**9)
    risk = abs(entry - sl)
    if A == B == 10**9:
        return (cl[end - 1] - entry) * (1 if long else -1) / risk
    return -1.0 if A <= B else abs(tp - entry) / risk


def session(ts):
    h = (ts % DAY) // 3600
    return "Asia 0-7" if h < 7 else "London 7-12" if h < 12 else "NY 12-17" if h < 17 else "late 17-24"


def run_sym(args):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # below normal
    sym, S, E = args
    from bensim import db
    from bensim.backtest import load_m5, resample
    lb = pd.Timestamp(S) - pd.Timedelta(days=200)
    raw = load_m5(sym, str(lb.date()), str((pd.Timestamp(E) + pd.Timedelta(days=60)).date()))
    s_ts, e_ts = pd.Timestamp(S, tz="UTC").timestamp(), pd.Timestamp(E, tz="UTC").timestamp()
    with db.connect() as c:
        news = np.array(sorted(r[0] for r in c.execute(
            "select extract(epoch from ts)::bigint from economic_events where impact ilike 'high%%' and currency = any(%s)",
            [CCY[sym].split()]).fetchall()), dtype=np.int64)

    def near_news(ts, w=3600):
        i = np.searchsorted(news, ts)
        return any(0 <= j < len(news) and abs(news[j] - ts) <= w for j in (i - 1, i))

    buf, cost_px = (3.0 if sym == "XAUUSD" else 5 * pip(sym)), (SPR.get(sym, 0.5) + 0.2) * pip(sym)
    rows = []
    for d in (1, -1):
        m = raw if d == 1 else {"t": raw["t"], "open": -raw["open"], "high": -raw["low"], "low": -raw["high"], "close": -raw["close"]}
        t, hi, lo = m["t"], m["high"], m["low"]
        bars = {tf: (m if tf == "M5" else resample(m, sec)) for tf, sec in TFS.items()}
        st = {tf: structure(bars[tf]) for tf in TFS}
        trend_at = {tf: (lambda ts, tf=tf: int(st[tf][2][max(np.searchsorted(bars[tf]["t"] + TFS[tf], ts, "right") - 1, 0)]))
                    for tf in ("H1", "H4", "D1")}
        piv = {sec: pivots(bars[n]["high"]) for n, sec in (("M15", 900), ("H1", 3600), ("H4", 14400))}
        zones = {}
        for tf, sec in TFS.items():
            b, (lastH, _, trend) = bars[tf], st[tf]
            at = atr14(b)
            for i in range(8, len(b["t"])):
                gap = b["low"][i] - b["high"][i - 2]
                ct = b["t"][i] + sec
                if not (gap > 0 and gap >= 0.3 * at[i] and s_ts <= ct < e_ts):
                    continue
                top, bot, liq = b["low"][i], b["high"][i - 2], b["low"][i - 6:i - 1].min()
                zones.setdefault(tf, []).append(dict(i=i, ct=ct, top=top, bot=bot, liq=liq, gap=gap, atr=at[i],
                                                     bos=bool(max(b["close"][i - 1], b["close"][i]) > lastH[i - 2]),
                                                     own_trend=int(trend[i - 3])))
        # break time of every zone (for the stacked check)
        brk = {}
        for tf, zs in zones.items():
            for z in zs:
                z0 = np.searchsorted(t, z["ct"])
                k = first(lo[z0:] <= z["liq"] - buf)
                z["z0"], z["t_break_any"] = z0, (t[z0 + k] if k >= 0 else 2**40)
            brk[tf] = {k2: np.array([z[k2] for z in zs]) for k2 in ("ct", "t_break_any", "top", "liq")}
        for tf, zs in zones.items():
            sec, b = TFS[tf], bars[tf]
            win = WBARS * sec
            higher = [h for h in ("H1", "H4", "D1") if TFS[h] > sec and h in brk]
            for z in zs:
                ct, top, bot, liq, z0 = z["ct"], z["top"], z["bot"], z["liq"], z["z0"]
                sl = liq - buf
                stp = top - sl
                zend = np.searchsorted(t, ct + win)
                row = dict(sym=sym, dir=d, tf=tf, group=GROUP[tf], created=ct, gap_pips=z["gap"] / pip(sym),
                           gap_atr=z["gap"] / z["atr"], liq_atr=(top - liq) / z["atr"], stop_pips=stp / pip(sym),
                           bos=z["bos"], own_trend=z["own_trend"], news_create=near_news(ct),
                           create_session=session(ct), **{f"trend_{h}": trend_at[h](ct) for h in ("H1", "H4", "D1")})
                for h in higher:
                    a = brk[h]
                    row[f"stack_{h}"] = bool(((a["ct"] < ct) & (a["t_break_any"] > ct) & (a["top"] >= sl) & (a["liq"] <= top)).any())
                # (a) BEFORE THE FILL: first bearish TF bar after the zone, sell its close, target the zone top
                i = z["i"]
                for j in range(i + 1, min(i + 11, len(b["t"]))):
                    if b["low"][j] <= top:
                        break
                    if b["close"][j] < b["open"][j]:
                        e_, sl_a = b["close"][j], b["high"][i:j + 1].max() + buf
                        za = np.searchsorted(t, b["t"][j] + sec)
                        if za < len(t) - 1 and e_ > top:
                            row["R_before_fill"] = sim(m, za, e_, sl_a, top, long=False, win=win) - cost_px / (sl_a - e_)
                            row["before_fill_rr"] = (e_ - top) / (sl_a - e_)
                        break
                k = first(lo[z0:zend] <= top)
                row["touched"] = k >= 0
                if k < 0:
                    rows.append(row)
                    continue
                k += z0
                row.update(hours_to_touch=(t[k] - ct) / 3600, touch_session=session(t[k]), news_touch=near_news(t[k]))
                ce, fill, br = first(lo[k:zend] <= (top + bot) / 2), first(lo[k:zend] <= bot), first(lo[k:zend] <= sl)
                row.update(ce=ce >= 0, filled=fill >= 0, hours_to_fill=(t[k + fill] - ct) / 3600 if fill >= 0 else np.nan,
                           fill_session=session(t[k + fill]) if fill >= 0 else None,
                           news_fill=near_news(t[k + fill]) if fill >= 0 else None,
                           broke=br >= 0, hours_held=(t[k + br] - t[k]) / 3600 if br >= 0 else np.nan,
                           news_break=near_news(t[k + br]) if br >= 0 else None)
                # touches before the break: a new touch = back in the zone after being >= 1R above it
                endk = k + br if br >= 0 else zend
                lab = np.where(lo[k:endk + 1] <= top, 1, np.where(hi[k:endk + 1] >= top + stp, 2, 0))
                lab = lab[lab > 0]
                if len(lab):
                    keep = np.r_[True, lab[1:] != lab[:-1]]
                    row["touches"] = int((lab[keep] == 1).sum())
                # (b) AT THE FILL: buy the zone top, stop under the liquidity, 1:2 and 1:3
                row["R_zone_2R"] = sim(m, k, top, sl, top + 2 * stp, win=win) - cost_px / stp
                row["R_zone_3R"] = sim(m, k, top, sl, top + 3 * stp, win=win) - cost_px / stp
                # (c) FOLLOW THE PUSH TO THE LIQUIDITY: a TF close below the zone bottom -> sell, target the liquidity
                jk = np.searchsorted(b["t"], t[k], "right") - 1
                for j in range(jk, len(b["t"])):
                    if b["t"][j] + sec > ct + win or b["high"][j] >= top + stp:
                        break
                    if b["close"][j] < bot:
                        e_, sl_c = b["close"][j], top + buf
                        zc = np.searchsorted(t, b["t"][j] + sec)
                        if e_ > liq and zc < len(t) - 1:
                            row["R_push_liq"] = sim(m, zc, e_, sl_c, liq, long=False, win=win) - cost_px / (sl_c - e_)
                            row["push_rr"] = (e_ - liq) / (sl_c - e_)
                        break
                # (d) SWEEP THEN RETURN: liquidity taken, lower-TF MSS, limit at the newest lower-TF FVG, 1:3
                sw = first(lo[z0:zend] < liq)
                row["swept"] = sw >= 0
                if sw >= 0 and tf in LTF:
                    sw += z0
                    row["hours_to_sweep"] = (t[sw] - ct) / 3600
                    L, ls = bars[{900: "M15", 3600: "H1", 14400: "H4"}[LTF[tf]]], LTF[tf]
                    P = piv[LTF[tf]]
                    js = np.searchsorted(L["t"], t[sw], "right") - 1
                    jd = min(np.searchsorted(L["t"], t[sw] + DEAD[tf] * DAY), len(L["t"]))
                    lowv, low_i = L["low"][js], js
                    for j in range(js, jd):
                        if L["low"][j] < lowv:
                            lowv, low_i = L["low"][j], j
                        q = np.searchsorted(P, low_i) - 1
                        while q >= 0 and P[q] + 2 > j:
                            q -= 1
                        if q >= 0 and L["close"][j] > L["high"][P[q]]:
                            row["hours_sweep_to_mss"] = (L["t"][j] + ls - t[sw]) / 3600
                            row["mss_session"] = session(L["t"][j] + ls)
                            fv = [L["low"][x] for x in range(low_i + 2, j + 1) if L["low"][x] > L["high"][x - 2]]
                            sld = lowv - buf
                            if fv and fv[-1] > sld:
                                z1 = np.searchsorted(t, L["t"][j] + ls)
                                f = first(lo[z1:np.searchsorted(t, L["t"][j] + ls * 25)] <= fv[-1])
                                if f >= 0:
                                    e_ = fv[-1]
                                    row["R_sweep_mss_fvg"] = sim(m, z1 + f, e_, sld, e_ + 3 * (e_ - sld)) - cost_px / (e_ - sld)
                            e_ = L["close"][j]
                            zm = np.searchsorted(t, L["t"][j] + ls)
                            if e_ > sld and zm < len(t) - 1:
                                row["R_sweep_mss"] = sim(m, zm, e_, sld, e_ + 3 * (e_ - sld)) - cost_px / (e_ - sld)
                            break
                rows.append(row)
    return rows


STRATS = {"R_before_fill": "a. before the fill (sell into the imbalance, target = zone)",
          "R_zone_2R": "b. at the fill, 1:2 (stop under liquidity)", "R_zone_3R": "b. at the fill, 1:3",
          "R_push_liq": "c. zone fails -> follow the push to the liquidity",
          "R_sweep_mss": "d. sweep -> lower-TF MSS close, 1:3", "R_sweep_mss_fvg": "d. sweep -> MSS -> lower-TF FVG limit, 1:3"}


def tstat(x):
    x = x.dropna()
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def report(ev, mid):
    ev["year"] = np.where(ev.created < mid, "Y1", "Y2")
    for c in [c for c in ev.columns if c.startswith(("stack_", "news_", "bos", "touched", "ce", "filled", "broke", "swept"))]:
        ev[c] = ev[c].astype(str).eq("True")
    tch = ev[ev.touched]
    print("1) WHAT HAPPENS TO A ZONE (window = 100 bars of its own TF)\n")
    print(f"{'TF':4s} {'zones':>6s} {'came back':>9s} {'h to touch':>10s} {'50% fill':>8s} {'full fill':>9s} {'h to fill':>9s} "
          f"{'took liq':>8s} {'held >1R first':>14s} {'h held':>7s} {'touches':>7s} {'swept->MSS':>10s} {'h to MSS':>8s}")
    for tf in TFS:
        g, x = ev[ev.tf == tf], tch[tch.tf == tf]
        held = (x.R_zone_2R > 0).mean()
        sw = x[x.swept]
        mss = sw.hours_sweep_to_mss.notna().mean() if "hours_sweep_to_mss" in sw and tf in LTF else np.nan
        print(f"{tf:4s} {len(g):6d} {100 * g.touched.mean():8.0f}% {x.hours_to_touch.median():10.1f} {100 * x.ce.mean():7.0f}% "
              f"{100 * x.filled.mean():8.0f}% {x.hours_to_fill.median():9.1f} {100 * x.broke.mean():7.0f}% {100 * held:13.0f}% "
              f"{x.hours_held.median():7.1f} {x.touches.mean():7.2f} {100 * mss:9.0f}% {sw.hours_sweep_to_mss.median() if tf in LTF else np.nan:8.1f}")
    print("   took liq = price went through the zone and took the liquidity under it; held >1R first = 1:2 trade at the zone won;")
    print("   h held = hours from the first touch until the liquidity was taken (zones that broke); touches = touches before the break\n")

    print("2) TOUCH BY TOUCH: of the zones that got to touch n, how many broke (took the liquidity) on that touch")
    for tf in TFS:
        x = tch[tch.tf == tf]
        n = x.touches.fillna(1)
        parts = []
        for k in (1, 2, 3, 4):
            reached = (n >= k).sum()
            if reached >= 30:
                parts.append(f"touch {k}: {reached} zones, broke {100 * (x.broke & (n == k)).sum() / reached:3.0f}%")
        print(f"   {tf:4s} " + " | ".join(parts) + f" | never broken in the window {100 * (~x.broke).mean():.0f}%")
    print()

    print("3) WHICH TRADE ON WHICH TIMEFRAME (R per trade at the same $ risk; * = both years positive; t = t-stat)")
    print(f"{'TF':4s} {'trade':58s} {'n':>6s} {'win%':>6s} {'avg R':>7s} {'total R':>8s} {'Y1':>7s} {'Y2':>7s} {'t':>5s} {'$ at $1k':>10s}")
    for tf in TFS:
        for col, lab in STRATS.items():
            if col not in ev:
                continue
            y = ev[(ev.tf == tf) & ev[col].notna()]
            if len(y) < 30:
                continue
            y1, y2 = y[y.year == "Y1"][col].sum(), y[y.year == "Y2"][col].sum()
            print(f"{tf:4s} {lab:58s} {len(y):6d} {100 * (y[col] > 0).mean():5.1f}% {y[col].mean():+7.3f} {y[col].sum():+8.1f} "
                  f"{y1:+7.1f} {y2:+7.1f} {tstat(y[col]):5.1f} {1000 * y[col].sum():+10,.0f} {'*' if y1 > 0 and y2 > 0 else ''}")
        print()

    print("4) WHY A ZONE HOLDS OR BREAKS (first touch: 'took liq' % and 1:2 trade avg R, by factor)")
    tch = tch.copy()
    tch["size"] = pd.cut(tch.gap_atr, [0, 0.5, 1, 2, 99], labels=["<0.5 ATR", "0.5-1", "1-2", ">2 ATR"])
    tch["liq_dist"] = pd.cut(tch.liq_atr, [0, 1, 2, 4, 999], labels=["<1 ATR", "1-2", "2-4", ">4 ATR"])
    tch["age"] = pd.cut(tch.hours_to_touch / tch.tf.map(TFS) * 3600, [-1, 3, 10, 30, 101], labels=["<3 bars", "3-10", "10-30", "30-100"])
    tch["htf_agree"] = (tch.trend_H4 == 1).astype(int) + (tch.trend_D1 == 1).astype(int)
    tch["stacked"] = tch[[c for c in tch.columns if c.startswith("stack_")]].fillna(False).astype(bool).any(axis=1)
    factors = {"imbalance size": "size", "impulse made a BOS": "bos", "own TF trend before (1 = same way)": "own_trend",
               "4H trend same way": "trend_H4", "1D trend same way": "trend_D1", "4H+1D agree (0/1/2)": "htf_agree",
               "inside an active higher-TF zone": "stacked", "distance zone -> liquidity": "liq_dist", "zone age at touch": "age",
               "touch session": "touch_session", "news +-1h at creation": "news_create", "news +-1h at the touch": "news_touch"}
    for grp in dict.fromkeys(GROUP.values()):
        x = tch[tch.group == grp]
        print(f"\n  == {grp}: {len(x)} touched zones, took liq {100 * x.broke.mean():.0f}%, 1:2 at the zone {x.R_zone_2R.mean():+.3f} R")
        for lab, f in factors.items():
            parts = []
            for v, z in x.groupby(f, observed=True):
                if len(z) >= 50:
                    parts.append(f"{v}: n {len(z)}, liq {100 * z.broke.mean():.0f}%, {z.R_zone_2R.mean():+.3f}R")
            print(f"     {lab:36s} " + " | ".join(parts))

    print("\n5) WHEN THE IMBALANCE FILLS / THE LIQUIDITY GETS TAKEN")
    for grp in dict.fromkeys(GROUP.values()):
        x = tch[tch.group == grp]
        fs = x[x.filled].fill_session.value_counts(normalize=True)
        print(f"   {grp}: full fills by session: " + ", ".join(f"{k} {100 * v:.0f}%" for k, v in fs.items())
              + f" | fills within 1h of high-impact news {100 * x[x.filled].news_fill.mean():.0f}%, "
                f"liquidity taken within 1h of news {100 * x[x.broke].news_break.mean():.0f}% "
                f"(touches near news {100 * x.news_touch.mean():.0f}% = the base rate)")

    print("\n6) BEST FILTERED SETUPS (discovery only -- every line here needs the untouched check). n >= 100, both years +, top 12 by t")
    tch["sess"] = tch.touch_session
    allz = ev.copy()  # strategy a. is decided at the zone's creation: use ALL zones and creation-time facts only
    allz["size"] = pd.cut(allz.gap_atr, [0, 0.5, 1, 2, 99], labels=["<0.5 ATR", "0.5-1", "1-2", ">2 ATR"])
    allz["liq_dist"] = pd.cut(allz.liq_atr, [0, 1, 2, 4, 999], labels=["<1 ATR", "1-2", "2-4", ">4 ATR"])
    allz["htf_agree"] = (allz.trend_H4 == 1).astype(int) + (allz.trend_D1 == 1).astype(int)
    allz["stacked"] = allz[[c for c in allz.columns if c.startswith("stack_")]].fillna(False).astype(bool).any(axis=1)
    allz["sess"] = allz.create_session
    cand = []
    for col in [c for c in STRATS if c in tch]:
        src = allz if col == "R_before_fill" else tch
        for tf in TFS:
            base = src[(src.tf == tf) & src[col].notna()]
            for f1 in ("size", "bos", "htf_agree", "stacked", "liq_dist", "sess", "own_trend"):
                for v1, z in base.groupby(f1, observed=True):
                    for f2 in ("size", "bos", "htf_agree", "stacked", "liq_dist", "sess", "own_trend"):
                        if f2 <= f1:
                            continue
                        for v2, w in z.groupby(f2, observed=True):
                            if len(w) < 100:
                                continue
                            y1, y2 = w[w.year == "Y1"][col].sum(), w[w.year == "Y2"][col].sum()
                            if y1 > 0 and y2 > 0:
                                cand.append((tstat(w[col]), tf, STRATS[col], f"{f1}={v1}, {f2}={v2}", len(w), w[col].mean(), w[col].sum(), y1, y2))
    print(f"   ({len(cand)} combinations passed both years -- many tests, so expect some to be luck)")
    for c in sorted(cand, reverse=True)[:12]:
        print(f"   t {c[0]:4.1f} {c[1]:4s} {c[2]:52s} {c[3]:38s} n {c[4]:5d} {c[5]:+.3f}R total {c[6]:+.0f}R (Y1 {c[7]:+.0f}, Y2 {c[8]:+.0f})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", nargs=2, default=["2024-11-07", "2026-11-03"])
    ap.add_argument("--tag", default="discovery")
    a = ap.parse_args()
    f = OUT / f"zone_map_events_{a.tag}.csv"
    if not f.exists():
        with ProcessPoolExecutor(4) as ex:
            rows = [r for part in ex.map(run_sym, [(s, *a.period) for s in SYMS]) for r in part]
        pd.DataFrame(rows).to_csv(f, index=False)
    ev = pd.read_csv(f, low_memory=False)
    s, e = (pd.Timestamp(x, tz="UTC").timestamp() for x in a.period)
    print(f"Round 28 zone map, {a.tag} {a.period[0]} .. {a.period[1]}, {len(ev)} zones, 7 pairs, buys + sells\n")
    report(ev, (s + e) / 2)


if __name__ == "__main__":
    main()
