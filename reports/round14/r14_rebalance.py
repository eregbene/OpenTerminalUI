"""Round 14: equity-rebalancing FX flow test. Rules: reports/round14/PREREG.md. Run from repo root."""
import json, math, datetime as dt
import numpy as np, pandas as pd
from scipy import stats

OUT = "reports/round14/"
IDX = {"EUR": "GDAXI", "JPY": "N225", "GBP": "FTSE", "CAD": "GSPTSE",
       "AUD": "AXJO", "CHF": "SSMI", "NZD": "NZ50", "USD": "GSPC"}
CCY = [c for c in IDX if c != "USD"]
LAST = pd.Period("2026-10", "M")
TC, SWAP = 0.0003, 0.0075 / 12


def month_end(sym, nth=-1):
    j = json.load(open(f"data/indices/{sym}.json"))["chart"]["result"][0]
    off = j["meta"]["gmtoffset"]
    d = pd.to_datetime(np.array(j["timestamp"]) + off, unit="s").normalize()
    s = pd.Series(j["indicators"]["quote"][0]["close"], index=d, dtype=float).dropna()
    s = s[s > 0]
    m = s.groupby(s.index.to_period("M")).nth(nth)
    m.index = m.index.to_period("M")
    m = m[m.index <= LAST]
    full = pd.period_range(m.index[0], m.index[-1], freq="M")
    gaps = [str(p) for p in full if p not in m.index]
    return m.reindex(full), gaps


px, cov = {}, {}
for c, sym in IDX.items():
    m, gaps = month_end(sym)
    px[c] = m
    cov[c] = {"index": "^" + sym, "first": str(m.index[0]), "last": str(m.index[-1]), "missing_months": gaps}
    print(f"{c} ^{sym}: {m.index[0]} .. {m.index[-1]}  missing months: {gaps}")
px = pd.DataFrame(px)


def signals(px):
    lr = np.log(px).diff()
    R1 = lr[CCY].sub(lr["USD"], axis=0)
    R3 = R1.rolling(3, min_periods=3).sum()  # NaN if any of t-2..t missing
    mu = R1.shift(1).rolling(36, min_periods=36).mean()
    sd = R1.shift(1).rolling(36, min_periods=36).std()
    return R1, R3, (R1 - mu) / sd


R1, R3, S = signals(px)

# FX & carry, indexed by month n = trade month
fx = pd.read_csv("data/macro/cpi/WS_XRU_g8_eop.csv", index_col="m")
fx.index = pd.PeriodIndex(fx.index, freq="M")
fxret = fx.shift(1) / fx - 1                       # holding c over month n
pr = pd.read_csv("data/macro/policy/policy_rates_monthly_eop.csv", index_col="m")
pr.index = pd.PeriodIndex(pr.index, freq="M")
pr = pr.ffill()
carry = pr[CCY].sub(pr["USD"], axis=0).shift(1) / 100 / 12   # rates at end of n-1
hold = (fxret[CCY] + carry).loc["2000-01":LAST]       # return of long c vs USD in month n

months = hold.index


def h1_weights(S):
    sig = S.shift(1).reindex(months)  # signal at end of n-1 -> trade month n
    pos = pd.DataFrame(0.0, index=months, columns=CCY)
    pos[sig > 1] = -1.0
    pos[sig < -1] = 1.0
    n = pos.abs().sum(axis=1)
    return pos.div(n.where(n > 0, 1), axis=0)


def xs_weights(R):
    sig = R.shift(1).reindex(months)
    w = pd.DataFrame(0.0, index=months, columns=CCY)
    for m in months:
        r = sig.loc[m].dropna()
        if len(r) < 4:
            continue
        r = pd.concat([r, pd.Series({"USD": 0.0})]).sort_values(ascending=False)
        for c in r.index[:2]:
            if c != "USD": w.loc[m, c] = -0.5
        for c in r.index[-2:]:
            if c != "USD": w.loc[m, c] = 0.5
    return w


def run(w):
    gross = (w * hold.fillna(0)).sum(axis=1)
    turn = w.diff().abs().sum(axis=1)
    turn.iloc[0] = w.iloc[0].abs().sum()
    net = gross - TC * turn - SWAP * w.abs().sum(axis=1)
    return gross, net


def summ(net):
    def m(a, b): return float(net.loc[a:b].mean())
    t = float(net.mean() / net.std() * math.sqrt(len(net)))
    eq = net.cumsum()
    yr = net.groupby(net.index.year).sum()
    out = {"n_months": len(net), "mean_pct": 100 * float(net.mean()), "t": t,
           "p_one_sided": float(stats.t.sf(t, len(net) - 1)),
           "P1_pct": 100 * m("2000-01", "2008-12"), "P2_pct": 100 * m("2009-01", "2017-12"),
           "P3_pct": 100 * m("2018-01", "2026-10"), "since2022_pct": 100 * m("2022-01", "2026-10"),
           "sharpe": float(net.mean() / net.std() * math.sqrt(12)),
           "maxdd_pct": 100 * float((eq - eq.cummax().clip(lower=0)).min()),
           "worst_month_pct": 100 * float(net.min()), "worst_month": str(net.idxmin()),
           "pct_months_active": None}
    for lots in (1, 5, 7):
        out[f"usd_per_year_{lots}lot"] = float(net.mean() * 12 * 1e5 * lots)
        out[f"usd_worst_month_{lots}lot"] = float(net.min() * 1e5 * lots)
        out[f"usd_maxdd_{lots}lot"] = out["maxdd_pct"] / 100 * 1e5 * lots
    out["yearly_pct"] = {int(k): round(100 * v, 3) for k, v in yr.items()}
    return out


W = {"H1": h1_weights(S), "H2": xs_weights(R1), "H3": xs_weights(R3)}
res = {}
for k, w in W.items():
    g, n = run(w)
    res[k] = summ(n)
    res[k]["gross_mean_pct"] = 100 * float(g.mean())
    res[k]["pct_months_active"] = 100 * float((w.abs().sum(axis=1) > 0).mean())
    res[k]["avg_turnover"] = float(w.diff().abs().sum(axis=1).mean())

# Benjamini-Hochberg q=0.05
ps = sorted((res[k]["p_one_sided"], k) for k in res)
thr = 0
for i, (p, k) in enumerate(ps, 1):
    if p <= 0.05 * i / 3: thr = i
for i, (p, k) in enumerate(ps, 1):
    res[k]["bh_reject"] = i <= thr
for k, r in res.items():
    r["PASS"] = bool(r["P1_pct"] > 0 and r["P2_pct"] > 0 and r["P3_pct"] > 0 and r["t"] >= 2 and r["bh_reject"])

# Sanity: no lookahead + sample rows
print("\nSanity rows (signal month t -> trade month t+1):")
for m in ["2008-09", "2015-07", "2020-02", "2026-08"]:
    t = pd.Period(m, "M"); n = t + 1
    print(f" signal {t}: R1 " + " ".join(f"{c}{R1.loc[t, c]:+.3f}" for c in CCY))
    print(f"            S  " + " ".join(f"{c}{S.loc[t, c]:+.2f}" for c in CCY))
    for k in W:
        print(f"   {k} weights in {n}: " + " ".join(f"{c}{W[k].loc[n, c]:+.2f}" for c in CCY if W[k].loc[n, c]))
    print(f"   hold ret {n}: " + " ".join(f"{c}{100*hold.loc[n, c]:+.2f}%" for c in CCY))
# mechanical lookahead check: weights of month n equal function of data <= n-1
assert (W["H1"].loc["2020-03"] != 0).sum() == ((S.loc["2020-02"].abs() > 1).sum())
# BIS check: EURUSD up => long EUR positive
assert abs(hold["EUR"].loc["2026-10"] - (fx["EUR"].loc["2026-09"] / fx["EUR"].loc["2026-10"] - 1 + carry["EUR"].loc["2026-10"])) < 1e-12

h1n = W["H1"].abs().gt(0).sum().to_dict()
first_sig = {c: str(S[c].dropna().index[0] + 1) for c in CCY}
# POST-HOC (not part of the pre-registered test): BIS month-end FX is an ECB/central-bank fixing
# taken before the US close, so the last day overlaps the signal. Re-run with index closes from the
# PENULTIMATE trading day of each month (signal fully known before the FX start), and show the
# reversed sign (momentum) gross/net for information only.
px2 = pd.DataFrame({c: month_end(sym, -2)[0] for c, sym in IDX.items()})
a1, a3, aS = signals(px2)
posthoc = {}
for k, w in {"H1": h1_weights(aS), "H2": xs_weights(a1), "H3": xs_weights(a3)}.items():
    g, n = run(w)
    posthoc[k + "_penultimate_day"] = {x: summ(n)[x] for x in ("mean_pct", "t", "P1_pct", "P2_pct", "P3_pct", "since2022_pct", "sharpe")}
    posthoc[k + "_penultimate_day"]["gross_mean_pct"] = 100 * float(g.mean())
for k, w in W.items():
    g, n = run(-w)
    posthoc[k + "_reversed_sign"] = {x: summ(n)[x] for x in ("mean_pct", "t", "P1_pct", "P2_pct", "P3_pct", "since2022_pct", "sharpe", "maxdd_pct")}
    posthoc[k + "_reversed_sign"]["gross_mean_pct"] = 100 * float(g.mean())
print("\nPOST-HOC:")
for k, v in posthoc.items():
    print(f" {k:22} " + " ".join(f"{x}={y:+.3f}" for x, y in v.items()))

json.dump({"posthoc": posthoc, "coverage": cov, "h1_position_months_per_ccy": h1n, "first_H1_trade_month": first_sig,
           "results": res}, open(OUT + "r14_results.json", "w"), indent=1)

print("\nclaim  mean%  t     P1%    P2%    P3%   since22%  Sharpe  maxDD%  worst%  BH  PASS")
for k, r in res.items():
    print(f"{k:5} {r['mean_pct']:+.3f} {r['t']:+.2f} {r['P1_pct']:+.3f} {r['P2_pct']:+.3f} {r['P3_pct']:+.3f} "
          f"{r['since2022_pct']:+.3f}  {r['sharpe']:+.2f}  {r['maxdd_pct']:.2f}  {r['worst_month_pct']:.2f} "
          f"{r['bh_reject']}  {r['PASS']}")
print("first H1 trade month:", first_sig)
