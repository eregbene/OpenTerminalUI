"""Round 25 (2026-11-09): 2-YEAR tests (2024-11-07 .. 2026-11-06, IC M5 from the DB, 7 symbols) of every mentor version,
old and mentor-matched, each with three stop styles (own structure stop | user's stop >= target 1:1 | stop >= 1 ATR(15M)).
PASS bar FIXED BEFORE THE RUN: total R > 0 in BOTH years (Y1 2024-11..2025-11, Y2 2025-11..2026-11), overall t >= 2,
and $ > 0 at $500 risk. Anything else = not proven.
Engines: Bennett run_symbol (LIVE / BASE / LATEST1 / LATEST2F), bennett_channel, Faiz orderflow (OLD / MATCHED),
JeaFx sflow entry A (OLD H1 / MATCHED H1 / MATCHED M15). Stop styles replayed by one M5 walker (stop first on ties,
short on ask, Friday 20:00 UTC close, 10-day max), R net of the round-trip cost; $ at $500 risk and fixed 1/5/7 lots.
    python reports/round25/two_year.py
"""
import dataclasses
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
S, E, MID = "2024-11-07", "2026-11-07", pd.Timestamp("2025-11-07").timestamp()
SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "XAUUSD", "USDJPY", "USDCHF", "USDCAD"]
OUT = "D:/Devops/trading/OpenTerminalUI/reports/round25/"
COST = {"EURUSD": 0.9, "GBPUSD": 1.2, "USDJPY": 1.0, "AUDUSD": 1.0, "USDCAD": 1.1, "USDCHF": 1.1, "XAUUSD": 3.0}
JOBS = [(e, c) for e, c in [("bennett", "LIVE"), ("bennett", "BASE"), ("bennett", "LATEST1"), ("bennett", "LATEST2F"),
                            ("channel", "CHANNEL"), ("faiz", "OLD"), ("faiz", "MATCHED"),
                            ("jeafx", "OLD H1/M5"), ("jeafx", "MATCHED H1/M5"), ("jeafx", "MATCHED M15/M5")]]


def job(a):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # below normal
    eng, cfg, sym = a
    s, e = S + "T00:00:00+00:00", E + "T00:00:00+00:00"
    if eng == "bennett":
        from bensim import bennett_strategy as bs
        from bensim.backtest import exec_profile, run_symbol
        B = bs.BennettParams
        p = {"LIVE": bs.LIVE, "BASE": B(), "LATEST1": B(leg_anchor="latest", leg_swing_atr=1.0, need_fvg=False),
             "LATEST2F": B(leg_anchor="latest", leg_swing_atr=2.0, need_fvg=True)}[cfg]
        t = run_symbol((sym, s, e, p, exec_profile("observed_account"), "bennett"))
        rows = [] if t.empty else [dict(dir=r.dir, t_entry=int(r.t_entry), entry=r.entry, sl=r.sl, tp=r.tp, r=r.r) for r in t.itertuples()]
    elif eng == "channel":
        from bensim import bennett_channel as bc
        rows = [dict(dir=x["dir"], t_entry=x["t_entry"], entry=x["entry"], sl=x["sl"], tp=x["tp"], r=x["r"]) for x in bc.run(sym, S, E)]
    elif eng == "faiz":
        from bensim import orderflow_strategy as of
        from bensim.backtest import exec_profile, run_symbol
        P = of.OrderFlowParams
        p = P(mode="flow", breaks="any", flow_min_rr=2.0) if cfg == "OLD" else \
            P(mode="flow", breaks="any", structure="swing", entry_sec=300, plan_bars=288, flow_min_rr=1.0)
        t = run_symbol((sym, s, e, p, exec_profile("observed_account"), "orderflow"))
        rows = [] if t.empty else [dict(dir=r.dir, t_entry=int(r.t_entry), entry=r.entry, sl=r.sl, tp=r.tp, r=r.r) for r in t.itertuples()]
    else:
        from bensim import sflow
        sflow.PAIRS["M15/M5"] = (900, 300)
        sflow.ZONE_LIFE = "touch" if cfg.startswith("MATCHED") else "next_bos"
        rows = [dict(dir=x["dir"], t_entry=x["fill_t"], entry=x["entry"], sl=x["stop"], tp=x["target"], r=x["r"])
                for x in sflow.run((sym, cfg.split()[1], S, E, "ic")) if x["kind"] == "A"]
    return [dict(r, strategy=f"{eng} {cfg}", sym=sym) for r in rows]


def pipval(sym, price):
    return 1000 / price if sym == "USDJPY" else 10 / price if sym in ("USDCAD", "USDCHF") else 10.0


def walk(m, d, t_in, entry, sl, tp, p, sym):
    t, h, lo, c = m["t"], m["high"], m["low"], m["close"]
    spr = (2.5 if sym == "XAUUSD" else 0.8) * p
    i0 = int(np.searchsorted(t, t_in))
    i1 = min(len(t), int(np.searchsorted(t, t_in + 10 * 86400)))
    risk = (entry - sl) * d
    for i in range(i0, i1):
        ts = pd.Timestamp(int(t[i]), unit="s")
        if ts.dayofweek == 4 and ts.hour >= 20:
            return (c[i] + (spr if d == -1 else 0) - entry) * d / risk
        hh, ll = h[i] + (spr if d == -1 else 0), lo[i] + (spr if d == -1 else 0)
        if (ll <= sl) if d == 1 else (hh >= sl):
            return -1.0
        if (hh >= tp) if d == 1 else (ll <= tp):
            return (tp - entry) * d / risk
    return (c[i1 - 1] + (spr if d == -1 else 0) - entry) * d / risk if i1 > i0 else np.nan


if __name__ == "__main__":
    jobs = [(e, c, s) for e, c in JOBS for s in SYMS]
    with ProcessPoolExecutor(3) as pool:
        rows = []
        for (e, c, s), r in zip(jobs, pool.map(job, jobs)):
            rows += r
            print("done", e, c, s, len(r), flush=True)
    T = pd.DataFrame(rows)
    T = T[(T.tp - T.entry) * T.dir > 0].reset_index(drop=True)
    T.to_csv(OUT + "two_year_trades.csv", index=False)
    from bensim.backtest import load_m5, resample
    from bensim.strategy import atr, pip
    M5, A15 = {}, {}
    for s in SYMS:
        M5[s] = load_m5(s, "2024-11-01", "2026-11-20")
        b = resample(M5[s], 900)
        A15[s] = (b["t"], atr(b))
    T["atr15"] = [float(A15[s][1][max(0, int(np.searchsorted(A15[s][0] + 900, t, side="right")) - 1)]) for s, t in zip(T.sym, T.t_entry)]
    P = T.sym.map(pip)
    res = []
    for rule in ("own stop", "stop >= target (1:1)", "stop >= 1 ATR(15M)"):
        own = (T.entry - T.sl).abs()
        rk = own if rule == "own stop" else (np.maximum(own, (T.tp - T.entry).abs()) if "1:1" in rule else np.maximum(own, T.atr15))
        sl = T.entry - T.dir * rk
        r = np.array([walk(M5[x.sym], x.dir, int(x.t_entry), x.entry, s_, x.tp, pp, x.sym) for x, s_, pp in zip(T.itertuples(), sl, P)])
        r = r - T.sym.map(COST) * P / rk
        pv = (rk / P) * [pipval(s, e) for s, e in zip(T.sym, T.entry)]
        res.append(pd.DataFrame({"strategy": T.strategy, "sym": T.sym, "rule": rule, "t": T.t_entry, "r": r, "usd1": r * pv}))
    R = pd.concat(res).dropna(subset=["r"])
    R.to_csv(OUT + "two_year_results.csv", index=False)
    print(f"\n2 YEARS {S} .. {E}; PASS = both years > 0, t >= 2, $ > 0 at $500 risk; engine R shown for reference")
    for st, g0 in R.groupby("strategy", sort=False):
        eng_r = T[T.strategy == st].r
        print(f"\n== {st}: {len(eng_r)} trades | engine (as traded) {eng_r.sum():+.1f}R")
        for rule, g in g0.groupby("rule", sort=False):
            y1, y2 = g[g.t < MID].r.sum(), g[g.t >= MID].r.sum()
            t = g.r.mean() / g.r.std() * np.sqrt(len(g)) if len(g) > 1 else 0
            ok = y1 > 0 and y2 > 0 and t >= 2
            u = g.usd1.sum()
            print(f"   {rule:22} win {np.mean(g.r > 0):4.0%} {g.r.mean():+.3f}R t {t:+.2f} total {g.r.sum():+7.1f}R (Y1 {y1:+.1f} / Y2 {y2:+.1f}) | "
                  f"${g.r.sum() * 500:+9,.0f} @$500 | 1/5/7 lots ${u:+9,.0f} / ${5 * u:+10,.0f} / ${7 * u:+10,.0f} {'PASS' if ok else ''}")
