"""Round 13 (PREREG.md, written before running): post-fix reversal (H1a/b/c), calendar-month seasonality (H2),
cross mean reversion H4 (H3). Read-only."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy import stats

ROOT = "D:/Devops/trading/OpenTerminalUI/"
OUT = ROOT + "reports/round13/"
ALL = ["AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "AUDUSD", "CADCHF", "CADJPY", "EURAUD", "EURCHF", "EURGBP", "EURJPY", "EURNZD",
       "EURUSD", "GBPCAD", "GBPCHF", "GBPJPY", "GBPNZD", "GBPUSD", "NZDCAD", "NZDCHF", "NZDJPY", "NZDUSD", "USDCAD", "USDCHF", "USDJPY"]
USD_BP = {"EUR": 11.6, "GBP": 13.3, "NZD": 5.8, "CAD": 7.2, "AUD": 6.5, "USD": 10.0, "CHF": 12.5}
SLIP = 0.3


def ex(bid, ask, side_in, bid2, ask2):
    pin, pout = (ask, bid2) if side_in == 1 else (bid, ask2)
    return side_in * (pout - pin) / ((bid + ask) / 2) * 1e4 - SLIP


def london(day: pd.Timestamp, hh: int, mm: int) -> pd.Timestamp:
    return pd.Timestamp(day.year, day.month, day.day, hh, mm, tz="Europe/London").tz_convert("UTC").tz_localize(None)


def fix_tests() -> pd.DataFrame:
    rows = []
    days = pd.bdate_range("2012-01-02", "2026-10-09")
    last = set(pd.Series(days).groupby(pd.Series(days).dt.to_period("M")).max())
    T = {k: [london(d, *k) for d in days] for k in [(15, 30), (15, 55), (16, 5), (16, 30), (17, 0)]}
    for s in ALL:
        z = np.load(ROOT + f"data/bars/fxcm/{s}_M5.npz")
        q = pd.DataFrame({"b": z["c"], "a": z["c"] + z["spr"]}, index=pd.to_datetime(z["t"] + 300, unit="s"))  # quote at bar close
        q = q[~q.index.duplicated()]
        get = {k: q.reindex(v) for k, v in T.items()}
        for i, d in enumerate(days):
            b30, a30 = get[(15, 30)].iloc[i]
            b55, a55 = get[(15, 55)].iloc[i]
            if not np.isfinite(b30 + a30 + b55 + a55):
                continue
            pre = ((b55 + a55) - (b30 + a30)) / (b30 + a30) * 1e4
            if pre == 0:
                continue
            side = -int(np.sign(pre))
            r = {"t": d, "year": d.year, "symbol": s, "pre_bp": pre, "month_end": d in last}
            b5, a5 = get[(16, 5)].iloc[i]
            b7, a7 = get[(17, 0)].iloc[i]
            b630, a630 = get[(16, 30)].iloc[i]
            r["H1a"] = ex(b5, a5, side, b7, a7) if np.isfinite(b5 + a5 + b7 + a7) else np.nan
            r["H1c"] = ex(b55, a55, side, b630, a630) if np.isfinite(b630 + a630) else np.nan
            rows.append(r)
    D = pd.DataFrame(rows)
    D["H1b"] = np.where(D.month_end, D.H1a, np.nan)
    return D


def seasonality() -> pd.Series:
    CC = ["USD", "EUR", "JPY", "GBP", "CAD", "CHF", "AUD", "NZD"]
    fx = pd.read_csv(ROOT + "data/macro/cpi/WS_XRU_g8_eop.csv", index_col=0)
    fx["USD"] = 1.0
    ret = (fx.shift(1) / fx - 1)[CC]
    pol = pd.read_csv(ROOT + "data/macro/policy/policy_rates_monthly_eop.csv", index_col=0)[CC].reindex(ret.index).ffill()
    mo = pd.Series(ret.index.str[5:7], index=ret.index)
    out, prev = {}, pd.Series(0.0, index=CC)
    months = list(ret.index)
    for i in range(1, len(months)):
        m, n = months[i - 1], months[i]
        if n < "2000-01":
            continue
        hist = ret.loc[:m][mo.loc[:m] == n[5:7]]
        sc = hist.mean()
        if hist.notna().sum().min() < 6 or sc.isna().any():
            continue
        r = sc.rank()
        w = pd.Series(0.0, index=CC)
        w[r[r >= 6].index], w[r[r <= 3].index] = 1 / 3, -1 / 3
        out[n] = (w * ret.loc[n]).sum() + (w * pol.loc[m]).sum() / 100 / 12 - (w - prev).abs().drop("USD").sum() * 3e-4 - 0.75 / 100 / 12 * 2
        prev = w
    return pd.Series(out)


def cross_mr() -> pd.DataFrame:
    y = pd.read_csv(ROOT + "data/macro/yields_ext/yields_2y_plus.csv", index_col=0, parse_dates=True).ffill(limit=5)
    rows = []
    for s, start in (("AUDNZD", "2012-01-01"), ("EURGBP", "2012-01-01"), ("EURCHF", "2015-03-01")):
        z = np.load(ROOT + f"data/bars/fxcm/{s}_H1.npz")
        h = pd.DataFrame({"b": z["c"], "a": z["c"] + z["spr"]}, index=pd.to_datetime(z["t"] + 3600, unit="s"))
        h = h[h.index.hour % 4 == 0]  # H4 closes at 00/04/08/12/16/20 UTC
        h = h[h.index >= start]
        mid = (h.b + h.a) / 2
        zz = ((mid - mid.rolling(50).mean()) / mid.rolling(50).std()).to_numpy()
        i, n = 50, len(h)
        while i < n - 1:
            if np.isfinite(zz[i]) and abs(zz[i]) > 2 and abs(zz[i - 1]) <= 2:
                side = -int(np.sign(zz[i]))
                j = i + 1
                while j < n - 1 and j - i < 30 and np.sign(zz[j]) == np.sign(zz[i]):
                    j += 1
                t0, t1 = h.index[i], h.index[j]
                r = ex(h.b.iat[i], h.a.iat[i], side, h.b.iat[j], h.a.iat[j])
                nights = int(((pd.date_range(t0, t1, freq="h").hour == 21) & (pd.date_range(t0, t1, freq="h").weekday < 5)).sum())
                gap = (y[s[:3]].asof(t0) - y[s[3:]].asof(t0)) if s[:3] in y and s[3:] in y else 0.0
                gap = gap if np.isfinite(gap) else 0.0
                r += side * gap * 100 / 365 * nights - 0.5 * nights
                rows.append({"t": t0, "year": t0.year, "symbol": s, "side": side, "bars": j - i, "bp": r})
                i = j
            i += 1
    return pd.DataFrame(rows)


def per3(y, cuts):
    return pd.cut(y, cuts, labels=["P1", "P2", "P3"])


def ctest(x, key):
    c = x.groupby(key).sum()
    t = c.mean() / (c.std(ddof=1) / np.sqrt(len(c)))
    return float(t), float(stats.t.sf(t, len(c) - 1))


def usd(df, col, when):
    u = df[col] * df.symbol.str[:3].map(USD_BP)
    m = u.groupby(df[when].dt.to_period("M")).sum()
    c = m.cumsum()
    yrs = (df[when].max() - df[when].min()).days / 365.25
    return {f"{L}lot": {"per_year": int(u.sum() * L / yrs), "worst_month": int(m.min() * L), "max_dd": int((c - c.cummax()).min() * L)} for L in (1, 5, 7)}


def main():
    F, S, M = fix_tests(), seasonality(), cross_mr()
    F.to_csv(OUT + "r13_fix_trades.csv", index=False)
    M.to_csv(OUT + "r13_crossmr_trades.csv", index=False)
    S.to_csv(OUT + "r13_season_monthly.csv")
    claims = []
    for c in ("H1a", "H1b", "H1c"):
        d = F.dropna(subset=[c])
        t, p = ctest(d[c], d.t.dt.to_period("W"))
        claims.append({"claim": c, "n": len(d), "mean_bp": round(d[c].mean(), 3), "t": round(t, 2), "p": p,
                       **d.groupby(per3(d.year, [2011, 2017, 2021, 2026]), observed=False)[c].mean().round(3).to_dict(), "usd": usd(d, c, "t")})
    yrs = pd.Series(S.index.str[:4].astype(int), index=S.index)
    t = S.mean() / (S.std(ddof=1) / np.sqrt(len(S)))
    claims.append({"claim": "H2", "n": len(S), "mean_pct_month": round(S.mean() * 100, 3), "t": round(t, 2), "p": float(stats.t.sf(t, len(S) - 1)),
                   "P1": S[yrs <= 2008].mean(), "P2": S[(yrs >= 2009) & (yrs <= 2017)].mean(), "P3": S[yrs >= 2018].mean(),
                   "usd": {"1lot_per_year": int(S.mean() * 12 * 1e5), "1lot_worst_month": int(S.min() * 1e5)}})
    t, p = ctest(M.bp, M.t.dt.to_period("W"))
    claims.append({"claim": "H3", "n": len(M), "mean_bp": round(M.bp.mean(), 2), "t": round(t, 2), "p": p,
                   **M.groupby(per3(M.year, [2011, 2017, 2021, 2026]), observed=False).bp.mean().round(2).to_dict(), "usd": usd(M, "bp", "t"),
                   "by_pair": M.groupby("symbol").bp.agg(["count", "mean"]).round(2).to_dict(orient="index")})
    ks = sorted(range(len(claims)), key=lambda i: claims[i]["p"])
    kmax = max([j + 1 for j, i in enumerate(ks) if claims[i]["p"] <= 0.05 * (j + 1) / len(claims)] or [0])
    for j, i in enumerate(ks):
        c = claims[i]
        c["BH"] = j < kmax
        c["PASS"] = bool(c["BH"] and c["t"] >= 2 and all(c[x] > 0 for x in ("P1", "P2", "P3")))
    rep = {"H1a_by_pair": F.groupby("symbol").H1a.mean().round(2).to_dict(), "H1_gross_reversal_corr": round(float(np.corrcoef(F.pre_bp, F.H1a.fillna(0))[0, 1]), 3)}
    json.dump({"claims": claims, "reported": rep}, open(OUT + "r13_results.json", "w"), indent=1, default=str)
    for c in claims:
        print({k: v for k, v in c.items() if k not in ("usd", "by_pair")}, "\n  ", c["usd"], c.get("by_pair", ""))
    print(rep)


if __name__ == "__main__":
    main()
