"""Round 21b (2026-11-09): stop / target variants on the 10-year trade history of fib618, Bennett, imbalance, enigma, sflow.

PRE-REGISTERED (written before the first run; nothing below is changed after seeing results):
  ALL       base | SL+1/+2/+3 pips | TP-1/-2/-3 pips | SL+2 & TP-2
  fib618    TP at the leg's 0% (swing extreme) | TP at the 127.2% extension
  imbalance TP at the nearest untouched opposite M15 FVG ahead of the entry (its near edge), else no trade
  sflow     skip stop < 8 x costs | skip planned RR < 3 | both
  Bennett   50% at T1 / rest to T2: NOT testable here (T2 is not in the trade file) -> stated, not faked
Pass bar for a variant: better than base by >= +0.03 R/trade AND better in BOTH halves (2016-11..2021 and 2021-10..2026).
Pips: strategy convention (0.0001, JPY 0.01, gold 0.10).

Replay: the trade's own entry/stop/target/time from the round-4 histories (FXCM 2012-20, IC 2021-26), walked on M5 bars
(IC bars from the DB where they exist, else FXCM): long SL/TP on bid, short on ask (bid + live spread), stop first when
one bar touches both, exit at the original exit time if it was a time / Friday exit, else at the close after 10 days.
R = gross path R - round-trip cost / stop distance (cost = Bennett cost_pips). Baseline must reproduce the stored R.
    python reports/round21/exit_variants_10y.py
"""
import json
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
from bensim import db  # noqa: E402
from bensim.strategy import pip  # noqa: E402

SP = "D:/ClaudeTemp/claude/14620cf7b9ae7a95/c018d285-be3f-456e-a306-37977c08ec0a/scratchpad/"
D = "D:/Devops/trading/OpenTerminalUI/data/"
T0, T1, MID = pd.Timestamp("2016-11-01").timestamp(), pd.Timestamp("2026-10-31").timestamp(), pd.Timestamp("2021-07-01").timestamp()
COST = {"EURUSD": 0.9, "GBPUSD": 1.2, "USDJPY": 1.0, "AUDUSD": 1.0, "USDCAD": 1.1, "USDCHF": 1.1, "XAUUSD": 3.0}
SPREAD = {k: v["spread_pips"] for k, v in json.load(open("D:/ClaudeTemp/claude/14620cf7b9ae7a95/4332fb73-f2e2-476e-8c00-71166ba97e2e/scratchpad/spreads.json")).items()}
SPREAD["XAUUSD"] = SPREAD["XAUUSD"] / 10  # spreads.json uses 1-cent gold pips; strategy pip = $0.10
MAXHOLD = 10 * 86400


def bars():
    out = {}
    for s in COST:
        try:
            z = np.load(D + f"bars/fxcm/{s}_M5.npz")
            out[("fxcm", s)] = (z["t"].astype(np.int64), z["h"], z["l"], z["c"])
        except FileNotFoundError:
            pass
        with db.connect() as c:
            r = c.execute("select extract(epoch from ts)::bigint, high, low, close from candles where symbol=%s and tf='M5' "
                          "and ts >= '2021-09-01' order by ts", (s,)).fetchall()
        if r:
            a = np.array(r, dtype=float)
            out[("ic", s)] = (a[:, 0].astype(np.int64), a[:, 1], a[:, 2], a[:, 3])
    return out


B = bars()


def series(src, sym, t):
    for key in ((src, sym), ("fxcm", sym), ("ic", sym)):
        if key in B and B[key][0][0] <= t <= B[key][0][-1]:
            return B[key]
    return None


def walk(tr, stop, target):
    """Gross R of one trade with this stop/target (target None/0 = none)."""
    s = series(tr.source, tr.symbol, tr.t_entry)
    if s is None:
        return np.nan
    t, h, l, c = s
    d, e = tr.dir, tr.entry
    risk = (e - stop) * d
    if risk <= 0:
        return np.nan
    i0 = int(np.searchsorted(t, tr.t_entry, side="left"))
    t_end = tr.t_exit if tr.exit_reason in ("time", "friday_close") else tr.t_entry + MAXHOLD
    i1 = int(np.searchsorted(t, t_end, side="right"))
    if i1 <= i0:
        return np.nan
    spr = SPREAD[tr.symbol] * pip(tr.symbol)
    hh, ll = h[i0:i1] + (spr if d == -1 else 0), l[i0:i1] + (spr if d == -1 else 0)  # short: ask = bid + spread
    hit_sl = (ll <= stop) if d == 1 else (hh >= stop)
    hit_tp = ((hh >= target) if d == 1 else (ll <= target)) if target else np.zeros_like(hit_sl)
    a = int(np.argmax(hit_sl)) if hit_sl.any() else 1 << 30
    b = int(np.argmax(hit_tp)) if hit_tp.any() else 1 << 30
    if a == b == 1 << 30:
        x = c[i1 - 1] + (spr if d == -1 else 0)
        return (x - e) * d / risk
    return -1.0 if a <= b else (target - e) * d / risk


def net(tr, stop, target):
    g = walk(tr, stop, target)
    return g - COST[tr.symbol] * pip(tr.symbol) / ((tr.entry - stop) * tr.dir)


def opp_fvg_target(tr):
    """imbalance: near edge of the nearest UNTOUCHED opposite M15 FVG ahead of the entry (formed in the last 5 days)."""
    s = series(tr.source, tr.symbol, tr.t_entry)
    if s is None:
        return None
    t, h, l, c = s
    i = int(np.searchsorted(t, tr.t_entry, side="left"))
    lo = max(0, i - 5 * 288)
    k = (t[lo:i] // 900)
    st = np.r_[0, np.flatnonzero(np.diff(k)) + 1]
    H, L = np.maximum.reduceat(h[lo:i], st), np.minimum.reduceat(l[lo:i], st)
    d, e, best = tr.dir, tr.entry, None
    for j in range(len(H) - 2):
        if d == 1 and H[j + 2] < L[j]:  # bearish gap above (candle 3 high < candle 1 low): zone H[j+2]..L[j]
            edge, far = H[j + 2], L[j]
            if edge > e and H[j + 3:].max(initial=-np.inf) < edge:  # untouched since
                best = edge if best is None else min(best, edge)
        if d == -1 and L[j + 2] > H[j]:  # bullish gap below: zone H[j]..L[j+2]
            edge = L[j + 2]
            if edge < e and L[j + 3:].min(initial=np.inf) > edge:
                best = edge if best is None else max(best, edge)
    return best


im = pd.concat([pd.read_csv(SP + "im_out/trades_fxcm.csv"), pd.read_csv(SP + "im_out/trades_ic.csv")], ignore_index=True)
im = im[["book", "symbol", "dir", "t_entry", "t_exit", "entry", "sl", "tp", "exit_reason", "r", "source"]].rename(columns={"r": "r_stored"})
sf = pd.concat([pd.read_csv(D + "sflow_fxcm_ours_2012_2021.csv").assign(source="fxcm"), pd.read_csv(D + "sflow_5y.csv").assign(source="ic")])
sf = sf[sf.kind == "A"]
sf = pd.DataFrame({"book": "sflow", "symbol": sf.symbol, "dir": sf.dir, "t_entry": sf.fill_t, "t_exit": sf.exit_t, "entry": sf.entry,
                   "sl": sf.stop, "tp": sf.target, "exit_reason": sf.how.map(lambda x: "time" if "time" in str(x) else str(x)),
                   "r_stored": sf.r, "source": sf.source, "rr": sf.rr})
T = pd.concat([im, sf], ignore_index=True)
T = T[(T.t_entry >= T0) & (T.t_entry <= T1)].reset_index(drop=True)
print(f"{len(T)} trades 2016-11..2026-10", T.groupby("book").size().to_dict(), flush=True)

V = {}
P = T.symbol.map(pip)
V["base"] = [net(r, r.sl, r.tp) for r in T.itertuples()]
for n in (1, 2, 3):
    V[f"SL+{n}"] = [net(r, r.sl - r.dir * n * p, r.tp) for r, p in zip(T.itertuples(), P)]
    V[f"TP-{n}"] = [net(r, r.sl, (r.tp - r.dir * n * p) if r.tp else r.tp) for r, p in zip(T.itertuples(), P)]
V["SL+2 TP-2"] = [net(r, r.sl - r.dir * 2 * p, (r.tp - r.dir * 2 * p) if r.tp else r.tp) for r, p in zip(T.itertuples(), P)]
R = pd.DataFrame(V)
R["book"], R["t"], R["r_stored"] = T.book, T.t_entry, T.r_stored

# strategy-specific
fib = T.book == "fib618"
leg = (T.entry - T.sl) * T.dir / 0.382  # stop ~ the leg origin (100%); 61.8% entry -> leg size
R.loc[fib, "fib 0%"] = [net(r, r.sl, r.entry + r.dir * 0.618 * lg) for r, lg in zip(T[fib].itertuples(), leg[fib])]
R.loc[fib, "fib 127.2%"] = [net(r, r.sl, r.sl + r.dir * 1.272 * lg) for r, lg in zip(T[fib].itertuples(), leg[fib])]
imb = T.book == "imbalance"
tg = [opp_fvg_target(r) for r in T[imb].itertuples()]
R.loc[imb, "opp zone TP"] = [net(r, r.sl, x) if x is not None else np.nan for r, x in zip(T[imb].itertuples(), tg)]
sfl = T.book == "sflow"
stop_ok = ((T.entry - T.sl) * T.dir) >= 8 * T.symbol.map(COST) * P
R.loc[sfl, "min stop 8x"] = np.where(stop_ok[sfl], R.loc[sfl, "base"], np.nan)
R.loc[sfl, "RR>=3"] = np.where(T.loc[sfl, "rr"] >= 3, R.loc[sfl, "base"], np.nan)
R.loc[sfl, "both"] = np.where(stop_ok[sfl] & (T.loc[sfl, "rr"] >= 3), R.loc[sfl, "base"], np.nan)
R.to_csv("D:/Devops/trading/OpenTerminalUI/reports/round21/exit_variants_10y.csv", index=False)

print("\nbaseline check (replay vs stored R):")
for b, g in R.groupby("book"):
    ok = g.dropna(subset=["base"])
    print(f"  {b:10} n {len(ok):5}  stored {ok.r_stored.mean():+.3f}R  replay {ok.base.mean():+.3f}R  corr {np.corrcoef(ok.r_stored, ok.base)[0, 1]:.2f}")

cols = [c for c in R.columns if c not in ("book", "t", "r_stored")]
print("\nR/trade net (n) | first half | second half   -- PASS = >= +0.03 vs base in all three")
for b, g in R.groupby("book"):
    print(f"\n== {b}")
    base = g.base
    for cname in cols:
        x = g[cname]
        if x.notna().sum() < 20:
            continue
        both = x.notna() & base.notna()
        dif = lambda m: (x[both & m] - base[both & m]).mean() if cname.split()[0] not in ("min", "RR>=3", "both") else x[both & m].mean() - base[both & m].mean()  # noqa: E731
        h1, h2 = g.t < MID, g.t >= MID
        full, a, c2 = x[both].mean() - base[both].mean() if cname in ("min stop 8x", "RR>=3", "both") else (x - base)[both].mean(), \
            (x[both & h1].mean() - base[both & h1].mean()), (x[both & h2].mean() - base[both & h2].mean())
        if cname in ("min stop 8x", "RR>=3", "both"):  # filters: compare the kept trades' mean with ALL base trades
            full, a, c2 = x.mean() - base.mean(), x[h1].mean() - base[h1].mean(), x[h2].mean() - base[h2].mean()
        flag = "PASS" if cname != "base" and full >= 0.03 and a >= 0.03 and c2 >= 0.03 else ""
        print(f"  {cname:12} {x.mean():+.3f}R ({x.notna().sum():4})  | {x[h1].mean():+.3f} | {x[h2].mean():+.3f}   vs base {full:+.3f} "
              f"({a:+.3f}/{c2:+.3f}) {flag}")
