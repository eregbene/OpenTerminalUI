"""Round 29 (2026-11-11): FVG lab -- SMC / ICT fair-value-gap setups with the entry, stop and target chosen by the data
(no fixed 1:3, no fixed $). User: "you don't need 1:3 ... figure out something for those timeframes ... SMC: liquidity sweep,
MSS, FVG -- focusing on the FVG".

Events (bullish; bearish = mirrored prices), on M5 M15 M30 H1 H4 H12 D1, 7 pairs:
  FVG    low[i] > high[i-2] (gap >= 0.1 ATR14). zone top = low[i], bottom = high[i-2]
  IFVG   a BEARISH FVG that price closes back above within 20 bars (inversion) -> it becomes support; event at that close
SMC context recorded per event (all known at the event's close):
  displacement  body of the middle candle / ATR      swept   the leg into the FVG took the last confirmed swing low
  mss_type      MSS = the FVG leg closed above the last swing high while the trend was down; BOS = trend already up
  discount      entry in the lower half of the last 50 bars' range       ny_hour  New York local hour (DST-correct)
  silver bullet NY 03-04 / 10-11 / 14-15                                 news     high-impact news +-1h
Trade grid (every event, every combination):
  entry  CLOSE (market at the event close) | TOP (proximal edge) | CE (50%) | BOT (distal edge); limits valid 20 bars,
         cancelled if price first runs 2R away
  stop   C1 (candle-1 low; IFVG: gap bottom) | DISP (displacement candle low; IFVG: inversion candle low) |
         LIQ (lowest low of the leg = the sweep low) ; minus 1 pip (gold $1)
  target 0.5 .. 6 R | EXT = the nearest confirmed swing high above the entry (external liquidity), if >= 0.5R
Outcome on M5: max favourable move before the stop (stop-bar high excluded), 100 bars of the event TF, costs in R.
    python reports/round29/fvg_lab.py                                   # discovery 2024-11-07 .. 2026-11-03
    python reports/round29/fvg_lab.py --period 2021-10-04 2024-11-07 --tag untouched --check   # pre-registered only
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
OUT = Path(ROOT + "/reports/round29")
SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "XAUUSD", "USDJPY", "USDCHF", "USDCAD"]
TFS = {"M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "H12": 43200, "D1": 86400}
ENTRIES, STOPS = ("CLOSE", "TOP", "CE", "BOT"), ("C1", "DISP", "LIQ")
KS = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0)
WBARS, LIMIT_BARS, DAY = 100, 20, 86400
CCY = {"EURUSD": ["EUR", "USD"], "GBPUSD": ["GBP", "USD"], "AUDUSD": ["AUD", "USD"], "XAUUSD": ["USD"], "USDJPY": ["USD", "JPY"],
       "USDCHF": ["USD", "CHF"], "USDCAD": ["USD", "CAD"]}
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


def last_confirmed(x, p, n):
    idx = np.searchsorted(p + 2, np.arange(n), "right") - 1
    return np.where(idx >= 0, x[p[np.maximum(idx, 0)]], np.nan)


def run_sym(args):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # below normal
    sym, S, E = args
    from bensim import db
    from bensim.backtest import load_m5, resample
    raw = load_m5(sym, str((pd.Timestamp(S) - pd.Timedelta(days=200)).date()), str((pd.Timestamp(E) + pd.Timedelta(days=60)).date()))
    s_ts, e_ts = pd.Timestamp(S, tz="UTC").timestamp(), pd.Timestamp(E, tz="UTC").timestamp()
    with db.connect() as c:
        news = np.array(sorted(r[0] for r in c.execute("select extract(epoch from ts)::bigint from economic_events "
                                                       "where impact ilike 'high%%' and currency = any(%s)", [CCY[sym]]).fetchall()), np.int64)
    buf, cost_px = (1.0 if sym == "XAUUSD" else pip(sym)), (SPR.get(sym, 0.5) + 0.2) * pip(sym)
    rows = []
    for d in (1, -1):
        m = raw if d == 1 else {"t": raw["t"], "open": -raw["open"], "high": -raw["low"], "low": -raw["high"], "close": -raw["close"]}
        t, hi, lo, cl = m["t"], m["high"], m["low"], m["close"]
        for tf, sec in TFS.items():
            b = m if tf == "M5" else resample(m, sec)
            n, H, L, C, O, T = len(b["t"]), b["high"], b["low"], b["close"], b["open"], b["t"]
            tr = np.maximum(H[1:], C[:-1]) - np.minimum(L[1:], C[:-1])
            atr = np.r_[np.nan, pd.Series(tr).rolling(14).mean().to_numpy()]
            pH, pL = pivots(H), pivots(L, high=False)
            lastH, lastL = last_confirmed(H, pH, n), last_confirmed(L, pL, n)
            trend, cur = np.zeros(n, np.int8), 0
            for i in range(1, n):
                cur = 1 if C[i] > lastH[i - 1] else -1 if C[i] < lastL[i - 1] else cur
                trend[i] = cur
            win = WBARS * sec
            events = []
            for i in range(10, n - 1):
                ct = T[i] + sec
                if not s_ts <= ct < e_ts or not np.isfinite(atr[i]):
                    continue
                if L[i] > H[i - 2] and L[i] - H[i - 2] >= 0.1 * atr[i]:  # FVG
                    events.append(dict(kind="FVG", i=i, ct=ct, top=L[i], bot=H[i - 2], gap=L[i] - H[i - 2],
                                       stops={"C1": L[i - 2], "DISP": L[i - 1], "LIQ": L[i - 8:i - 1].min()},
                                       disp=abs(C[i - 1] - O[i - 1]) / atr[i], leg_lo=L[i - 8:i - 1].min(), ref=i - 9))
                if H[i] < L[i - 2] and L[i - 2] - H[i] >= 0.1 * atr[i]:  # bearish FVG -> look for the inversion close
                    top, bot = L[i - 2], H[i]
                    for j in range(i + 1, min(i + 21, n - 1)):
                        if C[j] > top:
                            cj = T[j] + sec
                            if s_ts <= cj < e_ts:
                                events.append(dict(kind="IFVG", i=j, ct=cj, top=top, bot=bot, gap=top - bot,
                                                   stops={"C1": bot, "DISP": L[j], "LIQ": L[i:j + 1].min()},
                                                   disp=abs(C[j] - O[j]) / atr[j], leg_lo=L[i:j + 1].min(), ref=i - 1))
                            break
            for ev in events:
                i = ev["i"]
                z0 = np.searchsorted(t, ev["ct"])
                if z0 >= len(t) - 2:
                    continue
                zend = np.searchsorted(t, ev["ct"] + win)
                zlim = np.searchsorted(t, ev["ct"] + LIMIT_BARS * sec)
                rng_lo, rng_hi = L[max(0, i - 50):i + 1].min(), H[max(0, i - 50):i + 1].max()
                ph = pH[(pH + 2 <= i)][-60:]
                row = dict(sym=sym, dir=d, tf=tf, kind=ev["kind"], created=ev["ct"], gap_atr=ev["gap"] / atr[i],
                           disp=ev["disp"], swept=bool(ev["leg_lo"] < lastL[ev["ref"]]),
                           mss_type=("MSS" if trend[max(ev["ref"], 0)] == -1 else "BOS") if max(C[i - 1], C[i]) > lastH[max(i - 2, 0)] else "none",
                           news=bool(len(news) and np.min(np.abs(news[max(0, np.searchsorted(news, ev["ct"]) - 1):np.searchsorted(news, ev["ct"]) + 1] - ev["ct"]), initial=10**9) <= 3600))
                for en in ENTRIES:
                    px = {"CLOSE": C[i], "TOP": ev["top"], "CE": (ev["top"] + ev["bot"]) / 2, "BOT": ev["bot"]}[en]
                    row[f"disc_{en}"] = (px - rng_lo) / (rng_hi - rng_lo) if rng_hi > rng_lo else np.nan
                    for sn in STOPS:
                        key = f"{en}_{sn}"
                        sl = ev["stops"][sn] - buf
                        risk = px - sl
                        if risk <= 0 or risk < 0.05 * atr[i]:
                            continue
                        if en == "CLOSE":
                            F = z0
                        else:
                            f = first(lo[z0:zlim] <= px)
                            c_ = first(hi[z0:zlim] >= px + 2 * risk)
                            if f < 0 or (0 <= c_ < f):
                                continue
                            F = z0 + f
                        end = max(np.searchsorted(t, t[F] + win), F + 1)
                        a = first(lo[F:end] <= sl)
                        if en != "CLOSE" and a == 0:
                            row[f"{key}_mfe"], row[f"{key}_stop"], row[f"{key}_end"] = 0.0, True, -1.0
                        else:
                            stop_at = F + a if a >= 0 else end
                            seg = hi[F + 1:stop_at] if en != "CLOSE" else hi[F:stop_at]
                            row[f"{key}_mfe"] = (seg.max() - px) / risk if len(seg) else 0.0
                            row[f"{key}_stop"] = a >= 0
                            row[f"{key}_end"] = -1.0 if a >= 0 else (cl[end - 1] - px) / risk
                        row[f"{key}_cost"] = cost_px / risk
                        above = H[ph][H[ph] > px]
                        row[f"{key}_ext"] = (above.min() - px) / risk if len(above) else np.nan
                rows.append(row)
    return rows


def outcome(df, key, k):
    """R per trade for target k (float R or 'EXT') on the rows that have this entry/stop."""
    mfe, stop, end, cost = df[f"{key}_mfe"], df[f"{key}_stop"].astype(bool), df[f"{key}_end"], df[f"{key}_cost"]
    kk = df[f"{key}_ext"].where(df[f"{key}_ext"] >= 0.5) if k == "EXT" else pd.Series(k, index=df.index)
    r = np.where(mfe >= kk, kk, np.where(stop, -1.0, np.minimum(end, kk)))
    return pd.Series(r - cost, index=df.index)[mfe.notna() & kk.notna()]


def tstat(x):
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def filters(ev):
    ny = pd.to_datetime(ev.created, unit="s", utc=True).dt.tz_convert("America/New_York").dt.hour
    sb = ny.isin([3, 10, 14])
    kz = ny.isin([2, 3, 4, 7, 8, 9, 10])
    fv, iv = ev.kind == "FVG", ev.kind == "IFVG"
    sw, mss, bos, dsp = ev.swept.astype(str).eq("True"), ev.mss_type == "MSS", ev.mss_type != "none", ev.disp >= 1.0
    news = ev.news.astype(str).eq("True")
    return {
        "all FVGs": fv,
        "FVG with displacement (body >= 1 ATR)": fv & dsp,
        "FVG + sweep": fv & sw,
        "FVG + structure break (MSS or BOS)": fv & bos,
        "FVG + MSS (trend turn)": fv & mss,
        "SMC: sweep + MSS + FVG": fv & sw & mss,
        "SMC: sweep + MSS + displacement + FVG": fv & sw & mss & dsp,
        "SMC: sweep + break + FVG in a killzone": fv & sw & bos & kz,
        "Silver Bullet: FVG in NY 3-4/10-11/14-15": fv & sb,
        "Silver Bullet + sweep": fv & sb & sw,
        "FVG + break + no news": fv & bos & ~news,
        "IFVG (inverted FVG)": iv,
        "IFVG + displacement": iv & dsp,
        "IFVG + sweep": iv & sw,
    }


def best_config(df, year, discount=None):
    """Best entry/stop/target on this subset by total R, needing n >= 100 and both years positive."""
    best = None
    for en in ENTRIES:
        for sn in STOPS:
            key = f"{en}_{sn}"
            if f"{key}_mfe" not in df:
                continue
            sub = df if discount is None else df[df[f"disc_{en}"] < 0.5]
            for k in (*KS, "EXT"):
                r = outcome(sub, key, k)
                if len(r) < 100:
                    continue
                y1, y2 = r[year[r.index] == "Y1"].sum(), r[year[r.index] == "Y2"].sum()
                if y1 > 0 and y2 > 0 and (best is None or r.sum() > best[0]):
                    best = (r.sum(), key, k, r, y1, y2)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", nargs=2, default=["2024-11-07", "2026-11-03"])
    ap.add_argument("--tag", default="discovery")
    ap.add_argument("--check", action="store_true", help="only score the pre-registered setups in PREREG.json")
    a = ap.parse_args()
    f = OUT / f"fvg_lab_{a.tag}.pkl"
    if not f.exists():
        with ProcessPoolExecutor(4) as ex:
            rows = [r for part in ex.map(run_sym, [(s, *a.period) for s in SYMS]) for r in part]
        pd.DataFrame(rows).to_pickle(f)
    ev = pd.read_pickle(f)
    s, e = (pd.Timestamp(x, tz="UTC").timestamp() for x in a.period)
    year = pd.Series(np.where(ev.created < (s + e) / 2, "Y1", "Y2"), index=ev.index)
    fl = filters(ev)
    print(f"Round 29 FVG lab, {a.tag} {a.period[0]} .. {a.period[1]}: {len(ev)} events, 7 pairs, buys + sells\n")
    if a.check:
        for p in json.loads((OUT / "PREREG.json").read_text()):
            sub = ev[(ev.tf == p["tf"]) & fl[p["filter"]]]
            if p.get("discount"):
                sub = sub[sub[f"disc_{p['key'].split('_')[0]}"] < 0.5]
            r = outcome(sub, p["key"], p["target"] if p["target"] == "EXT" else float(p["target"]))
            y = year[r.index]
            ok = r.sum() > 0 and tstat(r) >= 2
            print(f"{p['tf']:4s} {p['filter']:42s}{' +discount' if p.get('discount') else '':10s} {p['key']:9s} target {p['target']!s:4s}: "
                  f"n {len(r):5d} win {100 * (r > 0).mean():5.1f}% {r.mean():+.3f}R total {r.sum():+7.1f}R "
                  f"(halves {r[y == 'Y1'].sum():+.0f} / {r[y == 'Y2'].sum():+.0f}) t {tstat(r):4.1f} -> {'PASS' if ok else 'FAIL'}")
        return
    print("1) THE TARGET IS NOT FIXED: avg R per trade by target, all FVGs, entry at the 50% (CE), stop under candle 1 (C1)")
    print("   " + f"{'TF':4s} " + " ".join(f"{k!s:>7s}" for k in (*KS, "EXT")))
    for tf in TFS:
        sub = ev[(ev.tf == tf) & fl["all FVGs"]]
        print("   " + f"{tf:4s} " + " ".join(f"{outcome(sub, 'CE_C1', k).mean():+7.3f}" for k in (*KS, "EXT")))
    print("\n2) BEST ENTRY / STOP / TARGET PER SETUP AND TIMEFRAME (n >= 100, both years positive; '-' = nothing qualifies)")
    print(f"   {'TF':4s} {'setup':44s} {'entry_stop':10s} {'target':6s} {'n':>6s} {'win%':>6s} {'avg R':>7s} {'total R':>8s} {'Y1':>7s} {'Y2':>7s} {'t':>5s} {'stop pips':>9s}")
    found = []
    for tf in TFS:
        for name, mask in fl.items():
            sub = ev[(ev.tf == tf) & mask]
            for disc in (None, True):
                bc = best_config(sub, year, disc)
                lab = name + (" +discount" if disc else "")
                if bc is None:
                    print(f"   {tf:4s} {lab:44s} -")
                    continue
                tot, key, k, r, y1, y2 = bc
                print(f"   {tf:4s} {lab:44s} {key:10s} {k!s:6s} {len(r):6d} {100 * (r > 0).mean():5.1f}% {r.mean():+7.3f} {tot:+8.1f} "
                      f"{y1:+7.1f} {y2:+7.1f} {tstat(r):5.1f}")
                found.append(dict(t=tstat(r), tf=tf, filter=name, discount=bool(disc), key=key, target=k, n=len(r), avg=r.mean(), total=tot))
        print()
    print("3) TOP 10 BY t (every one of these was CHOSEN on this data -- the untouched check decides)")
    for x in sorted(found, key=lambda x: -x["t"])[:10]:
        print(f"   t {x['t']:4.1f} {x['tf']:4s} {x['filter']:42s}{' +discount' if x['discount'] else '':10s} {x['key']:9s} target {x['target']!s:4s} "
              f"n {x['n']:5d} {x['avg']:+.3f}R total {x['total']:+.0f}R")
    pd.DataFrame(found).to_csv(OUT / f"fvg_lab_best_{a.tag}.csv", index=False)


if __name__ == "__main__":
    main()
