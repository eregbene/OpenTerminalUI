"""Round 34 (2026-11-11): open pattern search around imbalances -- "think outside the box", 5m..4H, all FXCM pairs, 2012-2026.
User: "I say pattern -- find something; check 30/15/5 minutes too; 10 years of data, all pairs; what happens after an imbalance".

EVENT: an imbalance candle (big one-sided candle: body >= 1.5 x 20-bar average body and >= 60% of its range), up or down.
OUTCOME (no stop/target guessing): the move h bars later in the imbalance's direction, in ATR, minus the real cost
(FXCM median spread for that pair and UTC hour + 0.2 pip) -> + = CONTINUATION pays, - = the REVERSAL / fill pays (side = -1).
  h = 1, 3, 6, 12, 24 bars of the event's timeframe.
CONDITIONS (outside-the-box angles, all known at the candle close):
  size (1.5-2 / 2-3 / >3 x avg), run of imbalances (1 / 2 / 3+ in a row), it left a 3-candle FVG, session (Asia, London open,
  London, NY open, NY, late, rollover), weekday, news +-1h, volatility regime (ATR14 / ATR200), with / against the 200-bar trend,
  place in the 100-bar range (bottom / middle / top), an opposite imbalance in the last 5 bars (engulf / flip),
  CROSS-PAIR: how many other pairs printed an imbalance the same way for the same currency at the same bar (0 / 1-2 / 3+).
  + WEEKEND GAP (the Sunday opening gap = an imbalance): fade it toward Friday's close, h = 1..24 hours.
SEARCH: every single condition and every pair of conditions x timeframe x horizon x side.
PRE-REGISTERED PASS RULES (written before any run):
  discovery 2012-2019: >= 300 events and t >= 4 (t over DAILY averages, so clustered events are not counted many times)
  validation 2020-2022: same side, >= 100 events, t >= 2
  final 2023-2026 (untouched until then): reported once for the survivors; a pattern is REAL only if positive there too with t >= 2
    python reports/round34/discover.py
"""
import sys
from concurrent.futures import ProcessPoolExecutor
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
OUT = Path(ROOT + "/reports/round34")
CCY = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]
PAIRS = [a + b for i, a in enumerate(CCY) for b in CCY[i + 1:] if Path(ROOT + f"/data/bars/fxcm/{a + b}_M5.npz").exists()]
TFS = {"M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400}
HS = (1, 3, 6, 12, 24)
P1, P2 = pd.Timestamp("2020-01-01", tz="UTC").timestamp(), pd.Timestamp("2023-01-01", tz="UTC").timestamp()


def session(h):
    return np.select([h < 7, h < 9, h < 12, h < 15, h < 17, h < 21], ["Asia", "LonOpen", "London", "NYopen", "NY", "late"], "rollover")


def stage1(sym):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # below normal
    from bensim import db
    from bensim.backtest import resample
    from bensim.fxcm_history import load
    raw = load(sym, "M5")
    ok = (raw["high"] >= raw["low"]) & (raw["close"] > 0)
    raw = {k: v[ok] for k, v in raw.items()}
    pip = 0.01 if sym.endswith("JPY") else 1e-4
    hr = (raw["t"] % 86400) // 3600
    sp = pd.Series(raw["spr"]).where(raw["spr"] > 0)
    spr_hour = sp.groupby(hr).median().reindex(range(24)).fillna(sp.median()).to_numpy()
    with db.connect() as c:
        news = np.array(sorted(r[0] for r in c.execute("select extract(epoch from ts)::bigint from economic_events where impact ilike 'high%%' "
                                                       "and currency = any(%s)", [[sym[:3], sym[3:]]]).fetchall()), np.int64)
    out = []
    for tf, sec in TFS.items():
        b = resample({k: raw[k] for k in ("t", "open", "high", "low", "close")}, sec)
        O, H, L, C, T = (b[k] for k in ("open", "high", "low", "close", "t"))
        n = len(T)
        body = np.abs(C - O)
        rng = np.maximum(H - L, 1e-12)
        avg = pd.Series(body).rolling(20).mean().shift(1).to_numpy()
        tr = np.r_[np.nan, np.maximum(H[1:], C[:-1]) - np.minimum(L[1:], C[:-1])]
        atr = pd.Series(tr).rolling(14).mean().to_numpy()
        atr200 = pd.Series(tr).rolling(200).mean().to_numpy()
        ma200 = pd.Series(C).rolling(200).mean().to_numpy()
        lo100, hi100 = pd.Series(L).rolling(100).min().to_numpy(), pd.Series(H).rolling(100).max().to_numpy()
        e = np.where(C > O, 1, -1)
        disp = (body >= 1.5 * avg) & (body >= 0.6 * rng) & np.isfinite(atr) & np.isfinite(atr200)
        idx = np.flatnonzero(disp)
        idx = idx[(idx >= 210) & (idx < n - max(HS) - 1)]
        if not len(idx):
            continue
        ee = e[idx]
        a = atr[idx]
        close_t = T[idx] + sec
        hour = (close_t % 86400) // 3600
        run = np.ones(len(idx), np.int8)
        for k in (1, 2):
            run += (disp[idx - k] & (e[idx - k] == ee)).astype(np.int8) * (run == k)
        opp = np.zeros(len(idx), bool)
        for k in range(1, 6):
            opp |= disp[idx - k] & (e[idx - k] == -ee)
        fvg = np.where(ee == 1, L[idx] > H[idx - 2], H[idx] < L[idx - 2])
        pos = (C[idx] - lo100[idx]) / np.maximum(hi100[idx] - lo100[idx], 1e-12)
        pos_dir = np.where(ee == 1, pos, 1 - pos)  # 1 = the move ends at the top of the range in its own direction
        k_n = np.searchsorted(news, close_t)
        near = np.zeros(len(idx), bool)
        for j in (k_n - 1, k_n):
            jj = np.clip(j, 0, max(len(news) - 1, 0))
            near |= (len(news) > 0) & (np.abs(news[jj] - close_t) <= 3600)
        cost = (spr_hour[hour] + 0.2 * pip) / a
        row = dict(sym=sym, tf=tf, t=close_t, e=ee.astype(np.int8),
                   size=np.select([body[idx] / avg[idx] < 2, body[idx] / avg[idx] < 3], [0, 1], 2).astype(np.int8),
                   run=run, fvg=fvg, sess=session(hour), dow=(((close_t // 86400) + 3) % 7).astype(np.int8), news=near,
                   vol=np.select([a / atr200[idx] < 0.8, a / atr200[idx] < 1.25], [0, 1], 2).astype(np.int8),
                   trend=(np.sign(C[idx] - ma200[idx]) == ee), place=np.select([pos_dir < 0.33, pos_dir < 0.67], [0, 1], 2).astype(np.int8),
                   opp=opp, cost=cost.astype(np.float32))
        for h in HS:
            row[f"r{h}"] = (ee * (C[idx + h] - C[idx]) / a).astype(np.float32)
        out.append(pd.DataFrame(row))
    # weekend gap: Sunday's first bar open vs Friday's last close
    t = raw["t"]
    gaps = np.flatnonzero(np.diff(t) > 36 * 3600) + 1
    hb = resample({k: raw[k] for k in ("t", "open", "high", "low", "close")}, 3600)
    dtr = pd.Series(np.r_[np.nan, np.maximum(hb["high"][1:], hb["close"][:-1]) - np.minimum(hb["low"][1:], hb["close"][:-1])]).rolling(24 * 14).mean().to_numpy() * 24 ** 0.5
    wg = []
    for g in gaps:
        fri, sun = raw["close"][g - 1], raw["open"][g]
        j = np.searchsorted(hb["t"], t[g])
        if j + 25 >= len(hb["t"]) or not np.isfinite(dtr[j - 1]):
            continue
        s = -np.sign(sun - fri)  # fade toward Friday's close
        r = dict(sym=sym, tf="WEEKEND_GAP", t=t[g], gap_atr=abs(sun - fri) / dtr[j - 1],
                 cost=float((raw["spr"][g] if raw["spr"][g] > 0 else spr_hour[0]) + 0.2 * pip) / dtr[j - 1])
        for h in HS:
            r[f"r{h}"] = s * (hb["close"][j + h - 1] - sun) / dtr[j - 1]
        wg.append(r)
    return pd.concat(out, ignore_index=True), pd.DataFrame(wg)


def daily_t(net, t):
    d = pd.Series(net).groupby(t // 86400).mean()
    return d.mean() / (d.std(ddof=1) / np.sqrt(len(d))) if len(d) > 2 and d.std() > 0 else 0.0


def main():
    f = OUT / "events.pkl"  # our own local cache
    if not f.exists():
        with ProcessPoolExecutor(4) as ex:
            parts = list(ex.map(stage1, PAIRS))
        ev = pd.concat([p[0] for p in parts], ignore_index=True)
        wg = pd.concat([p[1] for p in parts], ignore_index=True)
        # cross-pair: other pairs printing an imbalance for the same currency the same way at the same bar
        rows = []
        for cpos, cc in ((0, 3), (3, 6)):
            rows.append(pd.DataFrame({"tf": ev.tf, "t": ev.t, "ccy": ev.sym.str[cpos:cc], "s": ev.e * (1 if cpos == 0 else -1)}))
        st = pd.concat(rows).groupby(["tf", "t", "ccy"]).s.agg(lambda x: int((x > 0).sum()) - int((x < 0).sum())).rename("net")
        base = st.reindex(pd.MultiIndex.from_arrays([ev.tf, ev.t, ev.sym.str[:3]])).to_numpy() * ev.e.to_numpy() - 1
        quote = -st.reindex(pd.MultiIndex.from_arrays([ev.tf, ev.t, ev.sym.str[3:]])).to_numpy() * ev.e.to_numpy() - 1
        conf = np.nan_to_num(base) + np.nan_to_num(quote)
        ev["cross"] = np.select([conf <= 0, conf <= 2], [0, 1], 2).astype(np.int8)
        ev.to_pickle(f)
        wg.to_pickle(OUT / "weekend_gaps.pkl")
    ev = pd.read_pickle(f)
    wg = pd.read_pickle(OUT / "weekend_gaps.pkl")
    ev["period"] = np.select([ev.t < P1, ev.t < P2], ["disc", "val"], "final")
    print(f"Round 34: {len(ev):,} imbalance candles, {ev.sym.nunique()} pairs, 5m..4H, 2012-2026 (+ {len(wg)} weekend gaps)\n")
    print("1) BASE RATE after costs (ATR per trade): + = follow the imbalance (continuation), - = fade it (fill)")
    for tf in TFS:
        x = ev[(ev.tf == tf) & (ev.period == "disc")]
        print(f"   {tf:4s} n {len(x):9,d}  " + "  ".join(f"h{h}: gross {x[f'r{h}'].mean():+.3f} cost {x.cost.mean():.3f}" for h in (1, 6, 24)))
    conds = {"size": [0, 1, 2], "run": [1, 2, 3], "fvg": [False, True], "sess": ["Asia", "LonOpen", "London", "NYopen", "NY", "late", "rollover"],
             "dow": [0, 1, 2, 3, 4], "news": [False, True], "vol": [0, 1, 2], "trend": [False, True], "place": [0, 1, 2], "opp": [False, True],
             "cross": [0, 1, 2]}
    singles = [(c, v) for c, vs in conds.items() for v in vs]
    tests = [(s,) for s in singles] + [(a, b) for a, b in combinations(singles, 2) if a[0] != b[0]]
    print(f"\n2) SEARCH: {len(tests)} conditions x {len(TFS)} TF x {len(HS)} horizons x 2 sides = {len(tests) * len(TFS) * len(HS) * 2:,} tests")
    found = []
    for tf in TFS:
        x = ev[ev.tf == tf]
        masks = {s: (x[s[0]] == s[1]).to_numpy() for s in singles}
        dsc, val, fin = (x.period == "disc").to_numpy(), (x.period == "val").to_numpy(), (x.period == "final").to_numpy()
        tt = x.t.to_numpy()
        for h in HS:
            g = x[f"r{h}"].to_numpy()
            cost = x.cost.to_numpy()
            for test in tests:
                mk = masks[test[0]] if len(test) == 1 else masks[test[0]] & masks[test[1]]
                md = mk & dsc
                if md.sum() < 300:
                    continue
                gm = g[md].mean()
                side = 1 if gm > 0 else -1
                net = side * g - cost
                td = daily_t(net[md], tt[md])
                if td < 4:
                    continue
                mv = mk & val
                if mv.sum() < 100:
                    continue
                tv = daily_t(net[mv], tt[mv])
                if tv < 2:
                    continue
                mf = mk & fin
                found.append(dict(tf=tf, h=h, side="FOLLOW" if side == 1 else "FADE", cond=" & ".join(f"{c}={v}" for c, v in test),
                                  n_d=int(md.sum()), net_d=net[md].mean(), t_d=td, n_v=int(mv.sum()), net_v=net[mv].mean(), t_v=tv,
                                  n_f=int(mf.sum()), net_f=net[mf].mean() if mf.any() else np.nan, t_f=daily_t(net[mf], tt[mf]) if mf.sum() > 2 else np.nan,
                                  pairs_pos_f=int((pd.Series(net[mf]).groupby(x.sym.to_numpy()[mf]).mean() > 0).sum()) if mf.any() else 0))
    fd = pd.DataFrame(found)
    if fd.empty:
        print("   nothing passed discovery (t >= 4) AND validation (t >= 2)")
    else:
        fd = fd.sort_values("t_d", ascending=False)
        fd.to_csv(OUT / "survivors.csv", index=False)
        print(f"   {len(fd)} passed discovery AND validation; final 2023-26 (untouched) shown once:")
        print(f"   {'TF':4s} {'h':>3s} {'side':6s} {'condition':40s} | {'2012-19':^18s} | {'2020-22':^18s} | {'2023-26 FINAL':^26s}")
        for _, r in fd.head(40).iterrows():
            ok = r.net_f > 0 and r.t_f >= 2
            print(f"   {r.tf:4s} {r.h:3d} {r.side:6s} {r.cond[:40]:40s} | n {r.n_d:6d} {r.net_d:+.3f} t{r.t_d:4.1f} | n {r.n_v:5d} {r.net_v:+.3f} t{r.t_v:4.1f} | "
                  f"n {r.n_f:5d} {r.net_f:+.3f} t{r.t_f:4.1f} pairs+ {r.pairs_pos_f:2d} {'REAL' if ok else 'fails'}")
        print(f"   -> REAL in all three periods: {int(((fd.net_f > 0) & (fd.t_f >= 2)).sum())} of {len(fd)}")
    print("\n3) WEEKEND GAP (fade the Sunday gap toward Friday's close; cost = Sunday's real spread), ATR(daily) per trade")
    wg["period"] = np.select([wg.t < P1, wg.t < P2], ["2012-19", "2020-22"], "2023-26")
    for lab, m in (("all gaps", wg.gap_atr > 0), ("gap > 0.1 daily ATR", wg.gap_atr > 0.1), ("gap > 0.25 daily ATR", wg.gap_atr > 0.25)):
        parts = []
        for per in ("2012-19", "2020-22", "2023-26"):
            y = wg[m & (wg.period == per)]
            parts.append(f"{per} n {len(y)} " + " ".join(f"h{h} {(y[f'r{h}'] - y.cost).mean():+.3f}" for h in (1, 6, 24)))
        print(f"   {lab:22s} " + " | ".join(parts))


if __name__ == "__main__":
    main()
