"""Round 9: pre-registered test (PREREG_ATR.md). Stronger ATR thresholds + 12 h exits for Flow ML. Read-only."""
import json
import numpy as np, pandas as pd
from scipy import stats

R8 = "D:/Devops/trading/OpenTerminalUI/reports/round8/"
BARS = "D:/Devops/trading/OpenTerminalUI/data/bars/fxcm/"
LIVE5 = ["EURUSD", "USDJPY", "USDCAD", "EURJPY", "CADJPY"]
TOP10 = ["GBPJPY", "NZDJPY", "EURJPY", "CADJPY", "USDJPY", "AUDJPY", "CADCHF", "USDCAD", "EURUSD", "EURAUD"]
USD_BP = {"EUR": 11.6, "GBP": 13.3, "NZD": 5.8, "CAD": 7.2, "AUD": 6.5, "USD": 10.0}  # $ per bp per lot (2026 approx)
PER = {"DISC": (2016, 2018), "VAL": (2019, 2022), "LOCK": (2023, 2026)}

T = pd.read_csv(R8 + "r8h_5seeds_trades_GLOBAL.csv", parse_dates=["t"])
T = T[~T.hour.isin([20, 21, 22, 23, 0])].copy()
T["d"] = np.where(T.side == "long", 1, -1)
parts = []
for s, g in T.groupby("symbol"):
    z = np.load(BARS + f"{s}_H1.npz")
    ct = pd.to_datetime(z["t"] + 3600, unit="s")  # bar close time
    r = pd.Series(z["h"] - z["l"])
    atr = pd.Series((r.rolling(6).mean() / r.rolling(48).mean()).to_numpy(), index=ct)
    bid = pd.Series(z["c"], index=ct); spr = pd.Series(z["spr"], index=ct)
    g = g.assign(atr=atr.reindex(atr.index.union(g.t)).ffill().loc[g.t].to_numpy())
    e6, e12 = g.t + pd.Timedelta(hours=6), g.t + pd.Timedelta(hours=12)
    def px(e, d):  # exact exit quote: long sells at bid, short buys at ask
        b = bid.reindex(e).to_numpy(); a = b + spr.reindex(e).to_numpy()
        return np.where(d == 1, b, a)
    p6, p12 = px(e6, g.d.to_numpy()), px(e12, g.d.to_numpy())
    ref = bid.reindex(g.t).to_numpy()
    g = g.assign(ext=g.d * (p12 - p6) / ref * 1e4, roll=((g.hour + 6) % 24 >= 21) | ((g.hour + 12) >= 21))
    parts.append(g)
T = pd.concat(parts)
T["grp"] = np.where(T.symbol.isin(LIVE5), "LIVE5", "OTHER20")
T["per"] = pd.cut(T.year, [2015, 2018, 2022, 2026], labels=list(PER))
T["usd1"] = T.bp * T.symbol.str[:3].map(USD_BP)


def wk_t(x, t):
    w = pd.Series(x.to_numpy(), index=t.to_numpy()).groupby(pd.Grouper(freq="W")).sum()
    w = w[w != 0] if len(w) else w
    return float(w.mean() / (w.std(ddof=1) / np.sqrt(len(w)))) if len(w) > 2 else np.nan


def sharpe(x, t):
    d = pd.Series(x.to_numpy(), index=t.dt.normalize().to_numpy()).groupby(level=0).sum()
    d = d.reindex(pd.bdate_range("2016-01-01", T.t.max()), fill_value=0.0)
    return float(d.mean() / d.std() * np.sqrt(252))


def book(g):
    return {"n": len(g), "bp": round(g.bp.mean(), 2), "t_wk": round(wk_t(g.bp, g.t), 2), "sharpe": round(sharpe(g.bp, g.t), 2),
            **{p: round(g[g.per == p].bp.mean(), 2) for p in PER}, "years_pos": int((g.groupby("year").bp.sum() > 0).sum())}


res = {"H12": [], "books": {}, "exits": {}, "dollars": {}}
for grp in ("LIVE5", "OTHER20"):
    G = T[T.grp == grp]
    base = book(G[G.atr > 1.2]); res["books"][f"{grp} 1.2"] = base; res["books"][f"{grp} all"] = book(G)
    for th in (1.4, 1.7):
        b = book(G[G.atr > th]); res["books"][f"{grp} {th}"] = b
        crit = {"a_all_periods": all(b[p] > base[p] for p in PER), "b_t2": b["t_wk"] >= 2, "c_sharpe": b["sharpe"] >= base["sharpe"], "d_lock": b["LOCK"] > 0}
        res["H12"].append({"claim": f"{grp} {th}", "p": float(stats.norm.sf(b["t_wk"])), **crit})
ps = sorted(range(4), key=lambda i: res["H12"][i]["p"])
kmax = max([k + 1 for k, i in enumerate(ps) if res["H12"][i]["p"] <= 0.05 * (k + 1) / 4] or [0])
for k, i in enumerate(ps):
    c = res["H12"][i]; c["e_BH"] = k < kmax
    c["PASS"] = all(c[x] for x in ("a_all_periods", "b_t2", "c_sharpe", "d_lock", "e_BH"))

for th in (1.2, 1.7):
    for grp in ("LIVE5", "OTHER20"):
        G = T[(T.grp == grp) & (T.atr > th)].dropna(subset=["ext"])
        for name, sel in (("E1 hold 12h", G), ("E2 winners to 12h", G[G.bp > 0])):
            x = sel.ext - (0 if name.startswith("E1") else 0)
            r = {"n": len(sel), "extra_bp": round(x.mean(), 2), "t_wk": round(wk_t(x, sel.t), 2),
                 **{p: round(sel[sel.per == p].ext.mean(), 2) for p in PER}, "share_cross_21utc": round(sel["roll"].mean(), 2)}
            r["PASS"] = bool(all(r[p] > 0 for p in PER) and r["t_wk"] >= 2)
            res["exits"][f"ATR>{th} {grp} {name}"] = r


def capped(df, cap=2):
    keep, op = [], []
    for r in df.sort_values("t").itertuples():
        op = [o for o in op if o[0] > r.t]
        legs = [(r.symbol[:3], r.d), (r.symbol[3:], -r.d)]
        ok = all(sum(1 for o in op for l in o[1] if l == lg) < cap for lg in legs)
        keep.append(ok)
        if ok: op.append((r.t + pd.Timedelta(hours=6), legs))
    return df.sort_values("t")[keep]


for set_name, pairs in (("LIVE5", LIVE5), ("TOP10 (in-sample pick)", TOP10), ("ALL25", sorted(T.symbol.unique()))):
    for th in (1.2, 1.4, 1.7):
        for span, yrs in (("10y", (2016, 2026)), ("2026", (2026, 2026))):
            D = capped(T[T.symbol.isin(pairs) & (T.atr > th) & T.year.between(*yrs)])
            day = D.groupby(D.t.dt.date).usd1.sum(); c = day.cumsum(); ny = max(D.t.max().year - D.t.min().year + (D.t.max().dayofyear / 365), 0.1) if span == "10y" else 1
            res["dollars"][f"{set_name} ATR>{th} {span}"] = {"trades": len(D), "bp": round(D.bp.mean(), 2), "win": round((D.bp > 0).mean(), 3),
                **{f"{L}lot": {"total": int(D.usd1.sum() * L), "per_year": int(D.usd1.sum() * L / ny) if span == "10y" else None,
                               "worst_trade": int(D.usd1.min() * L), "worst_day": int(day.min() * L), "max_dd": int((c - c.cummax()).min() * L)} for L in (1, 5, 7)}}
json.dump(res, open("r9_results.json", "w"), indent=1, default=str)
for k, v in res["books"].items(): print("BOOK", k, v)
for c in res["H12"]: print("CLAIM", c)
for k, v in res["exits"].items(): print("EXIT", k, v)
for k, v in res["dollars"].items(): print("USD", k, v)
