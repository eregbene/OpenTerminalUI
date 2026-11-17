"""Round 10 (PREREG.md, written before running): pre-FOMC dollar drift (H1a/H1b/H2), post-FOMC reversal (H3),
home-hours depreciation basket (H4). FXCM H1 2012-26, exact quotes, +0.3 bp slippage, carry - 0.5 bp/night. Read-only."""
from __future__ import annotations

import json
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy import stats

ROOT = "D:/Devops/trading/OpenTerminalUI/"
OUT = ROOT + "reports/round10/"
BARS = ROOT + "data/bars/fxcm/"
USDP = ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "USDJPY", "USDCHF", "USDCAD"]
ALL = ["AUDCAD", "AUDCHF", "AUDJPY", "AUDNZD", "AUDUSD", "CADCHF", "CADJPY", "EURAUD", "EURCHF", "EURGBP", "EURJPY", "EURNZD",
       "EURUSD", "GBPCAD", "GBPCHF", "GBPJPY", "GBPNZD", "GBPUSD", "NZDCAD", "NZDCHF", "NZDJPY", "NZDUSD", "USDCAD", "USDCHF", "USDJPY"]
USD_BP = {"EUR": 11.6, "GBP": 13.3, "NZD": 5.8, "CAD": 7.2, "AUD": 6.5, "USD": 10.0, "CHF": 12.5, "JPY": 0.0}  # $ per bp per lot
SLIP, MARKUP = 0.3, 0.5
Y = pd.read_csv(ROOT + "data/macro/yields_ext/yields_2y_plus.csv", index_col=0, parse_dates=True).ffill(limit=5)
Q: dict[str, pd.DataFrame] = {}
for s in ALL:
    z = np.load(BARS + f"{s}_H1.npz")
    Q[s] = pd.DataFrame({"bid": z["c"], "ask": z["c"] + z["spr"]}, index=pd.to_datetime(z["t"] + 3600, unit="s"))  # quote at bar CLOSE


def quote(s: str, ts: pd.Timestamp):
    q = Q[s]
    return (q.at[ts, "bid"], q.at[ts, "ask"]) if ts in q.index else (np.nan, np.nan)


def nights(a: pd.Timestamp, b: pd.Timestamp) -> int:
    """Rollovers (21 UTC) crossed between a and b; Wednesday night counts 3 (weekend swap)."""
    n, d = 0, a.normalize()
    while d <= b:
        r = d + pd.Timedelta(hours=21)
        if a < r <= b and d.weekday() < 5:
            n += 3 if d.weekday() == 2 else 1
        d += pd.Timedelta(days=1)
    return n


def carry(s: str, side: int, day: pd.Timestamp, n: int) -> float:
    b, q = s[:3], s[3:]
    yb = Y[b].asof(day) if b in Y else np.nan
    yq = Y[q].asof(day) if q in Y else np.nan
    gap = (yb - yq) if np.isfinite(yb) and np.isfinite(yq) else 0.0
    return side * gap * 100 / 365 * n - MARKUP * n


def trade(s: str, side: int, a: pd.Timestamp, b: pd.Timestamp, with_carry: bool = True) -> float:
    ba, aa = quote(s, a)
    bb, ab = quote(s, b)
    if not np.isfinite(ba + aa + bb + ab):
        return np.nan
    px_in, px_out = (aa, bb) if side == 1 else (ba, ab)
    r = side * (px_out - px_in) / ((ba + aa) / 2) * 1e4 - SLIP
    return r + (carry(s, side, a.normalize(), nights(a, b)) if with_carry else 0.0)


def usd_side(s: str, long_usd: int) -> int:
    return long_usd if s.startswith("USD") else -long_usd


def fomc() -> pd.DataFrame:
    D = pd.to_datetime(pd.read_csv(ROOT + "data/macro/fomc_dates.csv").date)
    rows = []
    for d in D:
        a = (d - pd.offsets.BDay(2)) + pd.Timedelta(hours=15)
        b = d + pd.Timedelta(hours=17)
        dy = Y.USD.asof(d - pd.offsets.BDay(3)) - Y.USD.asof(d - pd.offsets.BDay(25))
        # realised USD move over the pre window (mid to mid, avg of the 7 pairs, + = USD up) -> H3 direction
        mv = []
        for s in USDP:
            ba, aa = quote(s, a)
            bb, ab = quote(s, b)
            if np.isfinite(ba + aa + bb + ab):
                mv.append(usd_side(s, 1) * ((bb + ab) / 2 - (ba + aa) / 2) / ((ba + aa) / 2) * 1e4)
        pre = float(np.mean(mv)) if mv else np.nan
        a3, b3 = d + pd.Timedelta(hours=20), d + pd.offsets.BDay(1) + pd.Timedelta(hours=20)
        for s in USDP:
            cond = int(np.sign(dy)) if np.isfinite(dy) and dy != 0 else 0
            r = {"event": d, "year": d.year, "symbol": s, "dy2": dy, "pre_usd": pre}
            r["H1a"] = trade(s, usd_side(s, cond), a, b) if cond else np.nan
            r["H1b"] = r["H1a"] if np.isfinite(dy) and abs(dy) >= 0.10 else np.nan
            r["H2"] = trade(s, usd_side(s, 1), a, b)
            r["H3"] = trade(s, usd_side(s, -int(np.sign(pre))), a3, b3) if np.isfinite(pre) and pre != 0 else np.nan
            rows.append(r)
    return pd.DataFrame(rows)


WIN = {"JPY": ("Asia/Tokyo", 9, 15), "AUD": ("Australia/Sydney", 9, 15), "NZD": ("Australia/Sydney", 9, 15),
       "EUR": ("Europe/London", 8, 12), "GBP": ("Europe/London", 8, 12), "CHF": ("Europe/London", 8, 12),
       "USD": ("America/New_York", 8, 16), "CAD": ("America/New_York", 8, 16)}


def home() -> pd.DataFrame:
    rows = []
    days = pd.bdate_range("2012-01-02", "2026-10-09")
    wins = {w for w in WIN.values()}
    for tz, h0, h1 in sorted(wins):
        cur = {c for c, w in WIN.items() if w == (tz, h0, h1)}
        for day in days:
            z = ZoneInfo(tz)
            a = pd.Timestamp(day.year, day.month, day.day, h0, tzinfo=z).tz_convert("UTC").tz_localize(None)
            b = pd.Timestamp(day.year, day.month, day.day, h1, tzinfo=z).tz_convert("UTC").tz_localize(None)
            for s in ALL:
                side = (1 if s[3:] in cur else 0) - (1 if s[:3] in cur else 0)
                if side:
                    r = trade(s, side, a, b, with_carry=False)  # intraday: no rollover inside any window
                    if np.isfinite(r):
                        rows.append({"t": a, "year": a.year, "symbol": s, "window": tz, "bp": r})
    return pd.DataFrame(rows)


def per(y: pd.Series) -> pd.Series:
    return pd.cut(y, [2011, 2017, 2021, 2026], labels=["DISC", "VAL", "LOCK"])


def clustered(x: pd.Series, key: pd.Series) -> tuple[float, float, int]:
    c = x.groupby(key.to_numpy()).sum()
    t = c.mean() / (c.std(ddof=1) / np.sqrt(len(c)))
    return float(t), float(stats.t.sf(t, len(c) - 1)), len(c)


def dollars(df: pd.DataFrame, col: str, when: str) -> dict:
    u = df[col] * df.symbol.str[:3].map(USD_BP).replace(0.0, np.nan).fillna(10.0)
    g = u.groupby(df[when].dt.to_period("M")).sum()
    c = g.cumsum()
    yrs = (df[when].max() - df[when].min()).days / 365.25
    return {f"{L}lot": {"per_year": int(u.sum() * L / yrs), "worst_month": int(g.min() * L), "max_dd": int((c - c.cummax()).min() * L)} for L in (1, 5, 7)}


def main() -> None:
    F, H = fomc(), home()
    F.to_csv(OUT + "r10_fomc_trades.csv", index=False)
    H.to_csv(OUT + "r10_home_trades.csv", index=False)
    claims = []
    for c in ("H1a", "H1b", "H2", "H3"):
        d = F.dropna(subset=[c])
        t, p, n = clustered(d[c], d.event)
        pm = d.groupby(per(d.year), observed=False)[c].mean().round(2).to_dict()
        claims.append({"claim": c, "trades": len(d), "events": n, "mean_bp": round(d[c].mean(), 2), "t": round(t, 2), "p": p, **pm,
                       "usd": dollars(d, c, "event")})
    wk = H.t.dt.to_period("W")
    t, p, n = clustered(H.bp, wk)
    claims.append({"claim": "H4", "trades": len(H), "weeks": n, "mean_bp": round(H.bp.mean(), 2), "t": round(t, 2), "p": p,
                   **H.groupby(per(H.year), observed=False).bp.mean().round(2).to_dict(), "usd": dollars(H.rename(columns={"bp": "H4"}), "H4", "t")})
    ps = sorted(range(len(claims)), key=lambda i: claims[i]["p"])
    k = max([j + 1 for j, i in enumerate(ps) if claims[i]["p"] <= 0.05 * (j + 1) / len(claims)] or [0])
    for j, i in enumerate(ps):
        c = claims[i]
        c["BH"] = j < k
        c["PASS"] = bool(c["BH"] and c["t"] >= 2 and all(c[x] > 0 for x in ("DISC", "VAL", "LOCK")))
    extra = {"H4_by_window": H.groupby("window").bp.agg(["count", "mean"]).round(2).to_dict(orient="index"),
             "H2_by_pair": F.groupby("symbol").H2.mean().round(2).to_dict(), "H1a_by_year": F.groupby("year").H1a.mean().round(2).to_dict()}
    json.dump({"claims": claims, "reported_only": extra}, open(OUT + "r10_results.json", "w"), indent=1, default=str)
    for c in claims:
        print({k: v for k, v in c.items() if k != "usd"}, "\n   $:", c["usd"])
    print(json.dumps(extra, indent=0, default=str))


if __name__ == "__main__":
    main()
