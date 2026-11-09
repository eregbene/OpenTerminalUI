"""Round 23 (2026-11-09): three ideas from the ICT folder videos on the 10-year trade history (fib618, Bennett, imbalance,
enigma, sflow; same trades as round 21, 2016-11..2026-10). Flow ML/AM untouched.

PRE-REGISTERED (written before the first run):
 1 CAP   Creamer: no new entry on a UTC day once 2 kept trades (any book) have closed at a loss that day before it.
         Also per book. PASS = total net R higher than base in the full period AND both halves (skipped trades lose).
 2 PD    Chart Fanatics premium/discount: long only if entry <= 50% of the H4 range, short only if >= 50%.
         PD30 = range of the last 30 completed H4 bars; PDSW = last confirmed 3-candle H4 swing high .. swing low.
 3 KEY   Jesse "no key level, no trade": entry price inside a same-direction H4 FVG formed in the last 30 completed
         H4 bars and not closed through (far edge) since.
 Filters PASS = kept R/trade >= base +0.03 in full AND both halves (2016-11..2021-06 / 2021-07..2026-10), >= 20% kept.
R = stored net R per trade. $ = R x stop pips x pip value per lot (1/5/7 lots on the 100k) and R x $500.
    python reports/round23/video_ideas_10y.py
"""
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
from bensim import db  # noqa: E402
from bensim.strategy import pip  # noqa: E402

SP = "D:/ClaudeTemp/claude/14620cf7b9ae7a95/c018d285-be3f-456e-a306-37977c08ec0a/scratchpad/"
D = "D:/Devops/trading/OpenTerminalUI/data/"
T0, T1, MID = pd.Timestamp("2016-11-01").timestamp(), pd.Timestamp("2026-10-31").timestamp(), pd.Timestamp("2021-07-01").timestamp()
SYMS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "XAUUSD"]


def h4(t, h, l, c):
    b = t // 14400
    st = np.r_[0, np.flatnonzero(np.diff(b)) + 1]
    return b[st] * 14400, np.maximum.reduceat(h, st), np.minimum.reduceat(l, st), c[np.r_[st[1:] - 1, len(c) - 1]]


H4 = {}
for s in SYMS:
    try:
        z = np.load(D + f"bars/fxcm/{s}_M5.npz")
        H4[("fxcm", s)] = h4(z["t"].astype(np.int64), z["h"], z["l"], z["c"])
    except FileNotFoundError:
        pass
    with db.connect() as cn:
        r = cn.execute("select extract(epoch from ts)::bigint, high, low, close from candles where symbol=%s and tf='M5' "
                       "and ts >= '2021-09-01' order by ts", (s,)).fetchall()
    if r:
        a = np.array(r, dtype=float)
        H4[("ic", s)] = h4(a[:, 0].astype(np.int64), a[:, 1], a[:, 2], a[:, 3])
print("bars loaded", flush=True)


def ctx(src, sym, t):
    """Last 60 completed H4 bars before t, or None."""
    for k in ((src, sym), ("fxcm", sym), ("ic", sym)):
        if k in H4:
            tb, H, L, C = H4[k]
            j = int(np.searchsorted(tb, (t // 14400) * 14400))  # bars starting before the current H4 bar = completed
            if j >= 60 and t - tb[j - 1] < 3 * 86400:
                return H[j - 60:j], L[j - 60:j], C[j - 60:j]
    return None


def pd30(x, d, e):
    H, L, _ = x
    mid = (H[-30:].max() + L[-30:].min()) / 2
    return e <= mid if d == 1 else e >= mid


def pdsw(x, d, e):
    H, L, _ = x
    sh = [i for i in range(1, len(H) - 1) if H[i] > H[i - 1] and H[i] > H[i + 1]]
    sl = [i for i in range(1, len(L) - 1) if L[i] < L[i - 1] and L[i] < L[i + 1]]
    if not sh or not sl:
        return np.nan
    mid = (H[sh[-1]] + L[sl[-1]]) / 2
    return e <= mid if d == 1 else e >= mid


def key(x, d, e):
    H, L, C = x
    if d == -1:
        H, L, C, e = -L, -H, -C, -e
    for k in range(len(H) - 30, len(H) - 1):
        if L[k + 1] > H[k - 1]:  # bullish gap H[k-1]..L[k+1]
            lo, hi = H[k - 1], L[k + 1]
            if lo <= e <= hi and C[k + 2:].min(initial=np.inf) >= lo:
                return True
    return False


def pipval(sym, price):
    return 1000 / price if sym == "USDJPY" else 10 / price if sym in ("USDCAD", "USDCHF") else 10.0


im = pd.concat([pd.read_csv(SP + "im_out/trades_fxcm.csv"), pd.read_csv(SP + "im_out/trades_ic.csv")], ignore_index=True)
im = im[["book", "symbol", "dir", "t_entry", "t_exit", "entry", "sl", "r", "source"]]
sf = pd.concat([pd.read_csv(D + "sflow_fxcm_ours_2012_2021.csv").assign(source="fxcm"), pd.read_csv(D + "sflow_5y.csv").assign(source="ic")])
sf = sf[sf.kind == "A"]
sf = pd.DataFrame({"book": "sflow", "symbol": sf.symbol, "dir": sf.dir, "t_entry": sf.fill_t, "t_exit": sf.exit_t, "entry": sf.entry,
                   "sl": sf.stop, "r": sf.r, "source": sf.source})
T = pd.concat([im, sf], ignore_index=True)
T = T[(T.t_entry >= T0) & (T.t_entry <= T1) & T.r.notna() & T.t_exit.notna()].sort_values("t_entry").reset_index(drop=True)
T["usd1"] = T.r * (T.entry - T.sl).abs() / T.symbol.map(pip) * [pipval(s, p) for s, p in zip(T.symbol, T.entry)]
T["h2"] = T.t_entry >= MID
print(f"{len(T)} trades", T.book.value_counts().to_dict(), flush=True)

X = [ctx(r.source, r.symbol, int(r.t_entry)) for r in T.itertuples()]
for name, f in (("PD30", pd30), ("PDSW", pdsw), ("KEY", key)):
    T[name] = [f(x, r.dir, r.entry) if x is not None else np.nan for x, r in zip(X, T.itertuples())]


def cap(df):
    """Keep mask: skip entries after 2 kept losses closed earlier the same UTC day."""
    losses, keep = defaultdict(list), []
    for r in df.itertuples():
        ok = sum(te < r.t_entry for te in losses[int(r.t_entry // 86400)]) < 2
        keep.append(ok)
        if ok and r.r < 0:
            losses[int(r.t_exit // 86400)].append(r.t_exit)
    return np.array(keep)


def dd(r, t_exit):
    eq = np.r_[0, np.cumsum(r.values[np.argsort(t_exit.values)])]
    return (np.maximum.accumulate(eq) - eq).max()


def money(g):
    u = g.usd1.sum()
    return f"${g.r.sum() * 500:+9,.0f} @$500 | 1/5/7 lots ${u:+9,.0f} / ${5 * u:+10,.0f} / ${7 * u:+10,.0f}"


print("\n== 1 CAP (2 losses/day) -- total R diff full (h1/h2), max drawdown R")
for scope, groups in (("all books together", [T]), ("per book", [g for _, g in T.groupby("book")])):
    mask = pd.concat([pd.Series(cap(g), index=g.index) for g in groups]).reindex(T.index)
    k = T[mask]
    d = [k.r.sum() - T.r.sum(), k[~k.h2].r.sum() - T[~T.h2].r.sum(), k[k.h2].r.sum() - T[T.h2].r.sum()]
    print(f"  {scope:19} kept {len(k)}/{len(T)}  total {k.r.sum():+7.1f}R vs base {T.r.sum():+7.1f}R  diff {d[0]:+6.1f} "
          f"({d[1]:+.1f}/{d[2]:+.1f})  DD {dd(k.r, k.t_exit):.1f}R vs {dd(T.r, T.t_exit):.1f}R  "
          f"skipped mean {T[~mask].r.mean():+.3f}R  {'PASS' if min(d) > 0 else ''}")
    print(f"      kept {money(k)}\n      base {money(T)}")
    for b, g in k.groupby("book"):
        bb = T[T.book == b]
        print(f"      {b:9} {len(g):5}/{len(bb):5}  {g.r.sum():+7.1f}R vs {bb.r.sum():+7.1f}R")

print("\n== 2/3 FILTERS -- R/trade kept vs base (same trades with bars) diff full (h1/h2)")
for name in ("PD30", "PDSW", "KEY"):
    print(f"-- {name}")
    for b, g in list(T.groupby("book")) + [("ALL", T)]:
        g = g[g[name].notna()]
        k = g[g[name].astype(bool)]
        if len(k) < 20:
            print(f"  {b:9} only {len(k)} kept")
            continue
        d = [k.r.mean() - g.r.mean(), k[~k.h2].r.mean() - g[~g.h2].r.mean(), k[k.h2].r.mean() - g[g.h2].r.mean()]
        ok = min(d) >= 0.03 and len(k) >= 0.2 * len(g)
        print(f"  {b:9} kept {len(k):5}/{len(g):5}  {k.r.mean():+.3f}R vs {g.r.mean():+.3f}R  diff {d[0]:+.3f} "
              f"({d[1]:+.3f}/{d[2]:+.3f}) {'PASS' if ok else ''}")
        if b != "ALL":
            print(f"            kept {money(k)}   base {money(g)}")
