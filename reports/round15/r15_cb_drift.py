"""Round 15 (PREREG.md, written before running): conditional pre-decision drift before non-US central bank meetings.
H1 level proxy X = 2y - policy at D-11; H2 change proxy X2 over 20 bd ending D-11; H3 no-change control. Read-only."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from scipy import stats

ROOT = "D:/Devops/trading/OpenTerminalUI/"
OUT = ROOT + "reports/round15/"
PAIR = {"EUR": ("EURUSD", 1), "GBP": ("GBPUSD", 1), "AUD": ("AUDUSD", 1), "NZD": ("NZDUSD", 1),
        "JPY": ("USDJPY", -1), "CHF": ("USDCHF", -1), "CAD": ("USDCAD", -1)}  # +1: buying pair = long c
USD_BP = {"EURUSD": 11.6, "GBPUSD": 13.3, "AUDUSD": 6.5, "NZDUSD": 5.8, "USDJPY": 10, "USDCHF": 10, "USDCAD": 10}
ASIA = {"JPY", "AUD", "NZD"}
SLIP, MARKUP = 0.3, 0.5
BD = pd.offsets.BDay()

Y = pd.read_csv(ROOT + "data/macro/yields_ext/yields_2y_plus.csv", index_col=0, parse_dates=True)
P = pd.read_csv(ROOT + "data/macro/policy/policy_rates_daily.csv", index_col=0, parse_dates=True)
days = pd.bdate_range("2010-01-01", "2026-11-30")
Y = Y.reindex(Y.index.union(days)).ffill(limit=5).reindex(days)
P = P.reindex(P.index.union(days)).ffill().reindex(days)


def val(df, c, d):
    return float(df.at[d, c]) if c in df and not pd.isna(df.at[d, c]) else np.nan


def bars(pair):
    z = np.load(ROOT + f"data/bars/fxcm/{pair}_H1.npz")
    close_t = pd.to_datetime(z["t"] + 3600, unit="s")
    return pd.DataFrame({"bid": z["c"], "ask": z["c"] + z["spr"]}, index=close_t)


def nights(t0, t1):
    n, d = 0, t0.normalize()
    while d <= t1:
        r = d + pd.Timedelta(hours=21)
        if t0 < r <= t1 and r.weekday() < 5:
            n += 3 if r.weekday() == 2 else 1
        d += pd.Timedelta(days=1)
    return n


def build():
    rows = []
    for c, (pair, pdir) in PAIR.items():
        B = bars(pair)
        for D in pd.to_datetime(pd.read_csv(ROOT + f"data/macro/cb_meetings/{c}.csv").date):
            d11, d31, d10 = D - 11 * BD, D - 31 * BD, D - 10 * BD
            sp = lambda d: val(Y, c, d) - val(P, c, d)  # noqa: E731
            X = sp(d11)
            X2 = (val(P, c, d11) - val(P, c, d31)) if c == "AUD" else X - sp(d31)
            t_in = d10 + pd.Timedelta(hours=15)
            ry_c, ry_u = val(Y, c, d10), val(Y, "USD", d10)
            if np.isnan(ry_c) or np.isnan(ry_u):
                ry_c, ry_u = val(P, c, d10), val(P, "USD", d10)
            for variant, t_out in (("literal", D + pd.Timedelta(hours=6)),
                                   ("clean", (D - BD + pd.Timedelta(hours=6)) if c in ASIA else D + pd.Timedelta(hours=6))):
                if variant == "clean" and c not in ASIA:
                    continue  # identical to literal; copied below
                ok = t_in in B.index and t_out in B.index
                r = {"ccy": c, "pair": pair, "D": D.date(), "variant": variant, "X": X, "X2": X2,
                     "entry_t": t_in, "exit_t": t_out, "ok": ok, "carry_long_bp": (ry_c - ry_u) * 100 / 365,
                     "nights": nights(t_in, t_out)}
                if ok:
                    a, b = B.loc[t_in], B.loc[t_out]
                    mid = (a.bid + a.ask) / 2
                    r.update(in_bid=a.bid, in_ask=a.ask, out_bid=b.bid, out_ask=b.ask,
                             buy_bp=(b.bid - a.ask) / mid * 1e4 - SLIP, sell_bp=(a.bid - b.ask) / mid * 1e4 - SLIP)
                rows.append(r)
    M = pd.DataFrame(rows)
    lit = M[M.variant == "literal"]
    M = pd.concat([lit, lit[~lit.ccy.isin(ASIA)].assign(variant="clean"), M[M.variant == "clean"]], ignore_index=True)
    return M


def trades(M, claim):
    if claim == "H1":
        s = np.where(M.X >= 0.15, 1, np.where(M.X <= -0.15, -1, 0))
    elif claim == "H2":
        s = np.where(M.X2 >= 0.10, 1, np.where(M.X2 <= -0.10, -1, 0))
    else:  # H3 control
        s = np.where(M.X.abs() < 0.05, 1, 0)
    T = M[(s != 0) & M.ok].copy()
    T["claim"], T["side_c"] = claim, s[(s != 0) & M.ok.to_numpy()]
    pdir = T.pair.map({p: d for p, d in PAIR.values()})
    T["side_pair"] = T.side_c * pdir
    T["gross_bp"] = np.where(T.side_pair == 1, T.buy_bp, T.sell_bp)
    T["swap_bp"] = T.side_c * T.carry_long_bp.fillna(0) * T.nights - MARKUP * T.nights
    T["net_bp"] = T.gross_bp + T.swap_bp
    T["usd_1lot"] = T.net_bp * T.pair.map(USD_BP)
    T["period"] = pd.cut(pd.to_datetime(T.D).dt.year, [2011, 2017, 2021, 2026], labels=["DISC", "VAL", "LOCK"])
    return T


def tstat(T):
    c = T.groupby("D").net_bp.sum()
    if len(c) < 3:
        return np.nan, np.nan
    t = c.mean() / (c.std(ddof=1) / np.sqrt(len(c)))
    return float(t), float(stats.t.sf(t, len(c) - 1))


def dollars(T):
    m = T.groupby(pd.to_datetime(T.exit_t).dt.to_period("M")).usd_1lot.sum()
    cum = m.cumsum()
    yrs = (pd.to_datetime(T.exit_t).max() - pd.to_datetime(T.entry_t).min()).days / 365.25
    return {f"{L}lot": {"per_year": int(T.usd_1lot.sum() * L / yrs), "worst_month": int(min(m.min(), 0) * L),
                        "max_dd": int((cum - cum.cummax().clip(lower=0)).min() * L)} for L in (1, 5, 7)}


def summ(T):
    t, p = tstat(T)
    per = {k: {"n": int(len(g)), "mean_bp": round(float(g.net_bp.mean()), 2) if len(g) else None}
           for k, g in T.groupby("period", observed=False)}
    return {"n": int(len(T)), "n_long": int((T.side_c == 1).sum()), "n_short": int((T.side_c == -1).sum()),
            "mean_net_bp": round(float(T.net_bp.mean()), 2), "mean_gross_bp": round(float(T.gross_bp.mean()), 2),
            "mean_swap_bp": round(float(T.swap_bp.mean()), 2), "win": round(float((T.net_bp > 0).mean()), 3),
            "t": round(t, 2), "p": p, "periods": per, "usd": dollars(T),
            "by_bank": {c: {"n": int(len(g)), "mean_bp": round(float(g.net_bp.mean()), 2), "t": round(tstat(g)[0], 2)}
                        for c, g in T.groupby("ccy")},
            "by_side": {int(s): {"n": int(len(g)), "mean_bp": round(float(g.net_bp.mean()), 2)} for s, g in T.groupby("side_c")}}


def bh(ps, q=0.05):
    o = np.argsort(ps)
    k = max([i + 1 for i, j in enumerate(o) if ps[j] <= q * (i + 1) / len(ps)], default=0)
    return [bool(r < k) for r in np.argsort(o)]


def main():
    M = build()
    res, allT = {"skipped_no_bars": {}, "missing_X": {}}, []
    for c, g in M[M.variant == "literal"].groupby("ccy"):
        res["skipped_no_bars"][c] = int((~g.ok).sum())
        res["missing_X"][c] = {"X": int(g.X.isna().sum()), "X2": int(g.X2.isna().sum()), "meetings": int(len(g))}
    for v in ("literal", "clean"):
        Mv = M[M.variant == v]
        out = {}
        for claim in ("H1", "H2", "H3"):
            T = trades(Mv, claim)
            allT.append(T)
            out[claim] = summ(T)
        ps = [out[h]["p"] for h in ("H1", "H2")]
        for h, b in zip(("H1", "H2"), bh(ps)):
            s = out[h]
            allpos = all(v2["mean_bp"] is not None and v2["mean_bp"] > 0 for v2 in s["periods"].values())
            s["bh_pass"], s["all_periods_pos"] = b, allpos
            s["PASS"] = bool(allpos and s["t"] >= 2 and b)
        res[v] = out
    T = pd.concat(allT)
    T.to_csv(OUT + "r15_trades.csv", index=False)
    json.dump(res, open(OUT + "r15_results.json", "w"), indent=1, default=str)
    print("sanity (literal H1 first trades per bank):")
    S = T[(T.variant == "literal") & (T.claim.isin(["H1", "H2"]))].groupby("ccy").head(1)
    print(S[["claim", "ccy", "pair", "D", "X", "X2", "side_c", "entry_t", "in_bid", "in_ask", "exit_t", "out_bid", "out_ask",
             "nights", "gross_bp", "swap_bp", "net_bp"]].to_string())
    for v in ("literal", "clean"):
        for h in ("H1", "H2", "H3"):
            s = res[v][h]
            print(v, h, "n", s["n"], "L/S", s["n_long"], s["n_short"], "mean", s["mean_net_bp"], "gross", s["mean_gross_bp"],
                  "t", s["t"], {k: x["mean_bp"] for k, x in s["periods"].items()}, "PASS", s.get("PASS"))
    print(json.dumps({k: res[k] for k in ("skipped_no_bars", "missing_X")}))


if __name__ == "__main__":
    main()
