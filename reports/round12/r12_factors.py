"""Round 12 (PREREG.md): monthly G8 currency factor portfolios from BIS data. Read-only."""
import json
import numpy as np, pandas as pd
from scipy import stats

D = "D:/Devops/trading/OpenTerminalUI/data/macro/"
OUT = "D:/Devops/trading/OpenTerminalUI/reports/round12/"
CC = ["USD", "EUR", "JPY", "GBP", "CAD", "CHF", "AUD", "NZD"]
fx = pd.read_csv(D + "cpi/WS_XRU_g8_eop.csv", index_col=0)  # foreign units per USD
fx["USD"] = 1.0
ret = (fx.shift(1) / fx - 1)[CC]  # month return of holding the currency vs USD
pol = pd.read_csv(D + "policy/policy_rates_monthly_eop.csv", index_col=0)[CC]
reer = pd.read_csv(D + "cpi/WS_EER_g8.csv", index_col=0)[CC]
idx = ret.index.intersection(pol.index).intersection(reer.index)
ret, pol, reer = ret.loc[idx], pol.loc[idx], reer.loc[idx]
sig = {"CARRY": pol,
       "MOM": (fx.shift(3) / fx - 1).loc[idx][CC],
       "VALUE": -(np.log(reer.shift(1)) - np.log(reer.shift(61)))}
z = {k: v.sub(v.mean(axis=1), axis=0).div(v.std(axis=1), axis=0) for k, v in sig.items()}
sig["COMBO"] = pd.concat(z.values()).groupby(level=0).mean().loc[idx]


def book(s: pd.DataFrame, markup: float) -> pd.Series:
    out, prev = {}, pd.Series(0.0, index=CC)
    months = list(idx)
    for i in range(len(months) - 1):
        m, n = months[i], months[i + 1]
        x = s.loc[m].dropna()
        if len(x) < 8 or m < "1999-01":
            continue
        r = x.rank()
        w = pd.Series(0.0, index=CC)
        w[r[r >= 6].index] = 1 / 3
        w[r[r <= 3].index] = -1 / 3
        spot = (w * ret.loc[n]).sum()
        carry = (w * pol.loc[m]).sum() / 100 / 12
        trade = (w - prev).abs().drop("USD").sum() * 3e-4
        out[n] = spot + carry - trade - markup / 100 / 12 * 2
        prev = w
    return pd.Series(out)


def summary(x: pd.Series) -> dict:
    p = pd.Series(x.index.str[:4].astype(int), index=x.index)
    t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
    c = (1 + x).cumprod()
    return {"months": len(x), "mean_pct": round(x.mean() * 100, 3), "t": round(t, 2), "p": float(stats.t.sf(t, len(x) - 1)),
            "sharpe": round(x.mean() / x.std() * np.sqrt(12), 2),
            "P1": round(x[p <= 2007].mean() * 100, 3), "P2": round(x[(p >= 2008) & (p <= 2016)].mean() * 100, 3), "P3": round(x[p >= 2017].mean() * 100, 3),
            "max_dd_pct": round(((c / c.cummax()) - 1).min() * 100, 1), "worst_month_pct": round(x.min() * 100, 2),
            "since2022_pct_per_month": round(x[p >= 2022].mean() * 100, 3),
            "usd_per_year_1lot": int(x.mean() * 12 * 100000), "usd_worst_month_1lot": int(x.min() * 100000)}


res = {}
for k in ("CARRY", "MOM", "VALUE", "COMBO"):
    res[k] = summary(book(sig[k], 0.75))
    res[k]["markup0"] = summary(book(sig[k], 0.0))["mean_pct"]
    res[k]["markup1.5"] = summary(book(sig[k], 1.5))["mean_pct"]
ks = sorted(res, key=lambda k: res[k]["p"])
kmax = max([j + 1 for j, k in enumerate(ks) if res[k]["p"] <= 0.05 * (j + 1) / 4] or [0])
for j, k in enumerate(ks):
    r = res[k]
    r["BH"] = j < kmax
    r["PASS"] = bool(r["BH"] and r["t"] >= 2 and r["P1"] > 0 and r["P2"] > 0 and r["P3"] > 0)
json.dump(res, open(OUT + "r12_results.json", "w"), indent=1)
for k, v in res.items():
    print(k, v)
book(sig["COMBO"], 0.75).to_csv(OUT + "r12_combo_monthly.csv")
