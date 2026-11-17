"""2-month code check (user: "check the code or trade for 2 months without the new stuff and also with the new stuff").
Re-builds EVERY first-table trade (zone edge, stop under the liquidity, 1:3) created 2026-09-01 .. 2026-11-01 on the 7 IC
pairs with separate plain-loop code, compares each with the round 28 study rows (R, BOS, liquidity distance, Fibonacci
position, session), lists the trades without / with the new filters, and draws charts.
    python reports/round31/two_month_check.py
"""
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
OUT = Path(ROOT + "/reports/round31")
SYMS = ["EURUSD", "GBPUSD", "AUDUSD", "XAUUSD", "USDJPY", "USDCHF", "USDCAD"]
TFS = {"H1": 3600, "H4": 14400, "D1": 86400}
S, E = pd.Timestamp("2026-09-01", tz="UTC").timestamp(), pd.Timestamp("2026-11-01", tz="UTC").timestamp()
SPR = {s: float(np.mean(v)) for s, v in json.loads(Path(ROOT + "/data/costs/spread_samples_2026-11-03.json").read_text())["spread_pips"].items()}


def pip(sym):
    return 0.01 if sym == "USDJPY" else 0.1 if sym == "XAUUSD" else 1e-4


def bars_tf(m, sec):
    df = pd.DataFrame({k: m[k] for k in ("t", "open", "high", "low", "close")})
    g = df.groupby(df.t // sec)
    return pd.DataFrame({"t": g.t.first() // sec * sec, "open": g.open.first(), "high": g.high.max(),
                         "low": g.low.min(), "close": g.close.last()}).reset_index(drop=True)


def swing_high_before(h, upto):
    """Latest 2-bar fractal high p with p + 2 <= upto, by hand."""
    for p in range(upto - 2, 1, -1):
        if h[p] >= h[p - 1] and h[p] >= h[p - 2] and h[p] > h[p + 1] and h[p] > h[p + 2]:
            return h[p]
    return np.nan


def rebuild(sym, d, raw):
    m = raw if d == 1 else {"t": raw["t"], "open": -raw["open"], "high": -raw["low"], "low": -raw["high"], "close": -raw["close"]}
    t, hi, lo, cl = m["t"], m["high"], m["low"], m["close"]
    buf = 3.0 if sym == "XAUUSD" else 5 * pip(sym)
    cost = (SPR.get(sym, 0.5) + 0.2) * pip(sym)
    out = []
    for tf, sec in TFS.items():
        b = bars_tf(m, sec)
        H, L, C = b.high.to_numpy(), b.low.to_numpy(), b.close.to_numpy()
        for i in range(60, len(b)):
            ct = b.t[i] + sec
            if not S <= ct < E:
                continue
            trs = [max(H[k], C[k - 1]) - min(L[k], C[k - 1]) for k in range(i - 13, i + 1)]
            atr = sum(trs) / 14
            gap = L[i] - H[i - 2]
            if gap <= 0 or gap < 0.3 * atr:
                continue
            top, bot, liq = L[i], H[i - 2], min(L[i - 6:i - 1])
            sl = liq - buf
            risk = top - sl
            tp = top + 3 * risk
            # first touch within 100 bars of the zone's TF
            k = next((x for x in range(np.searchsorted(t, ct), np.searchsorted(t, ct + 100 * sec)) if lo[x] <= top), None)
            if k is None:
                continue
            end = np.searchsorted(t, t[k] + 100 * sec)
            res, x = None, k
            while x < end:
                if lo[x] <= sl:
                    res = -1.0
                    break
                if hi[x] >= tp:
                    res = 3.0
                    break
                x += 1
            if res is None:
                res, x = (cl[end - 1] - top) / risk, end - 1
            rlo, rhi = L[i - 50:i + 1].min(), H[i - 50:i + 1].max()
            hour = (t[k] % 86400) // 3600
            out.append(dict(sym=sym, dir=d, tf=tf, created=ct, t_touch=t[k], t_exit=t[x], top=top, bot=bot, liq=liq, sl=sl, tp=tp,
                            R=res - cost / risk, bos=bool(max(C[i - 1], C[i]) > swing_high_before(H, i - 2)),
                            liq_atr=(top - liq) / atr, fib_pos=(top - rlo) / (rhi - rlo), london_ny=7 <= hour < 17))
    return out


def chart(i, r, raw):
    s = r.dir
    sec = TFS[r.tf]
    lo_t, hi_t = r.created - 60 * sec, r.t_exit + 10 * sec
    k = (raw["t"] >= lo_t) & (raw["t"] <= hi_t)
    b = bars_tf({kk: raw[kk][k] for kk in ("t", "open", "high", "low", "close")}, max(sec // 4, 900))
    fig, ax = plt.subplots(figsize=(12, 5.5))
    x = pd.to_datetime(b.t, unit="s")
    ax.vlines(x, b.low, b.high, color="#555", lw=0.6)
    up = b.close >= b.open
    ax.vlines(x[up], b.open[up], b.close[up], color="#2e7d32", lw=2)
    ax.vlines(x[~up], b.close[~up], b.open[~up], color="#c62828", lw=2)
    real = lambda v: v if s == 1 else -v  # noqa: E731
    ax.axhspan(min(real(r.top), real(r.bot)), max(real(r.top), real(r.bot)), color="#90caf9", alpha=0.4, label=f"{r.tf} FVG")
    for v, lab, col in ((r.top, "entry (zone edge)", "#000"), (r.liq, "liquidity", "#6a1b9a"), (r.sl, "stop", "#c62828"), (r.tp, "target 1:3", "#1565c0")):
        ax.axhline(real(v), color=col, lw=1, ls="--", label=lab)
    for tt, lab in ((r.created, "zone formed"), (r.t_touch, "entry"), (r.t_exit, "exit")):
        ax.axvline(pd.to_datetime(tt, unit="s"), color="#888", lw=0.8)
        ax.text(pd.to_datetime(tt, unit="s"), ax.get_ylim()[1], lab, rotation=90, va="top", fontsize=8)
    flt = "NEW FILTERS: PASS" if r.new_pass else "new filters: skip"
    ax.set_title(f"{r.sym} {r.tf} {'BUY' if s == 1 else 'SELL'} {pd.to_datetime(r.t_touch, unit='s'):%Y-%m-%d %H:%M} -> {r.R:+.2f}R | "
                 f"BOS {r.bos}, liq {r.liq_atr:.1f} ATR, fib {r.fib_pos:.2f} | {flt}")
    ax.legend(fontsize=7, loc="lower left")
    fig.tight_layout()
    fig.savefig(OUT / f"two_month_{i:02d}.png", dpi=85)
    plt.close(fig)


def main():
    from bensim.backtest import load_m5
    rows, raws = [], {}
    for sym in SYMS:
        raws[sym] = load_m5(sym, "2026-01-01", "2026-12-31")
        for d in (1, -1):
            rows += rebuild(sym, d, raws[sym])
    me = pd.DataFrame(rows)
    me["new_pass"] = np.where(me.tf == "D1", me.fib_pos < 0.5, me.bos & (me.liq_atr > 2) & (me.fib_pos < 0.5) & me.london_ny)
    st = pd.read_pickle(ROOT + "/reports/round28/first_table_events.pkl")  # our own local cache (round 28 study rows)
    st = st[(st.created >= S) & (st.created < E)]
    j = me.merge(st[["sym", "dir", "tf", "created", "R_zone_3R", "bos", "liq_atr", "fib_pos"]], on=["sym", "dir", "tf", "created"],
                 how="outer", suffixes=("", "_study"), indicator=True)
    both = j[j._merge == "both"]
    same = ((both.R - both.R_zone_3R).abs() < 1e-6) & (both.bos == both.bos_study.astype(str).eq("True")) & \
           ((both.liq_atr - both.liq_atr_study).abs() < 1e-6) & ((both.fib_pos - both.fib_pos_study).abs() < 1e-6)
    print(f"2-MONTH CODE CHECK 2026-09-01 .. 2026-11-01, 7 pairs, 1H / 4H / 1D zones\n")
    print(f"trades rebuilt by the separate checker: {len(me)}; in the study: {len(st)}; matched {len(both)}, "
          f"identical (R, BOS, liquidity, Fibonacci) {int(same.sum())}; only in checker {int((j._merge == 'left_only').sum())}, "
          f"only in study {int((j._merge == 'right_only').sum())}")
    bad = both[~same]
    if len(bad):
        print("MISMATCHES:\n" + bad[["sym", "dir", "tf", "created", "R", "R_zone_3R", "bos", "bos_study", "fib_pos", "fib_pos_study"]].head(10).to_string())
    print("\nRESULT OF THE 2 MONTHS (R per trade; $ at $1k risk)")
    for tf in TFS:
        a, b = me[me.tf == tf], me[(me.tf == tf) & me.new_pass]
        print(f"   {tf}: WITHOUT the new filters {len(a):4d} trades, hit 1:3 {100 * (a.R > 2.5).mean():4.1f}%, total {a.R.sum():+6.1f}R ({1000 * a.R.sum():+,.0f} $)"
              f"  |  WITH the new filters {len(b):3d} trades, hit 1:3 {100 * (b.R > 2.5).mean() if len(b) else 0:4.1f}%, total {b.R.sum():+6.1f}R ({1000 * b.R.sum():+,.0f} $)")
    lst = me.sort_values("t_touch").assign(when=lambda x: pd.to_datetime(x.t_touch, unit="s").dt.strftime("%m-%d %H:%M"),
                                          side=lambda x: np.where(x.dir == 1, "BUY", "SELL"))
    for c in ("top", "sl", "tp"):
        lst[c] = np.where(lst.dir == 1, lst[c], -lst[c])
    lst[["when", "sym", "tf", "side", "top", "sl", "tp", "R", "bos", "liq_atr", "fib_pos", "london_ny", "new_pass"]].to_csv(OUT / "two_month_trades.csv", index=False)
    print("\nALL 1D TRADES + every trade that passed the new filters on 4H:")
    show = lst[(lst.tf == "D1") | ((lst.tf == "H4") & lst.new_pass)]
    print(show[["when", "sym", "tf", "side", "top", "sl", "tp", "R", "bos", "fib_pos", "new_pass"]].round(5).to_string(index=False))
    pick = pd.concat([lst[lst.tf == "D1"].head(4), lst[(lst.tf == "H4") & lst.new_pass].head(3), lst[(lst.tf == "H1") & lst.new_pass].head(3)])
    for i, (_, r) in enumerate(pick.iterrows(), 1):
        rr = me.loc[(me.sym == r.sym) & (me.tf == r.tf) & (me.t_touch == pd.Timestamp("2026-" + r.when, tz="UTC").timestamp() if False else True)]
        src = me[(me.sym == r.sym) & (me.tf == r.tf) & (me.dir == (1 if r.side == "BUY" else -1)) & (me.R == r.R)].iloc[0]
        chart(i, src, raws[r.sym] if src.dir == 1 else {"t": raws[r.sym]["t"], "open": raws[r.sym]["open"], "high": raws[r.sym]["high"],
                                                         "low": raws[r.sym]["low"], "close": raws[r.sym]["close"]})
    print(f"\nfull list: reports/round31/two_month_trades.csv; charts: reports/round31/two_month_01..{len(pick):02d}.png")


if __name__ == "__main__":
    main()
