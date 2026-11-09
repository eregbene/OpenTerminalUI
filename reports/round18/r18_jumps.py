"""Round 18: fade no-news FX M5 jumps (H1), follow news jumps (H2). Rules: PREREG.md (frozen)."""
import json, math
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
PAIRS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"]
USD_PER_BP = {"EURUSD": 11.6, "GBPUSD": 13.3, "AUDUSD": 6.5, "NZDUSD": 5.8, "USDJPY": 10, "USDCHF": 10, "USDCAD": 10}
SLIP, HOLD, WIN = 0.3, 3600, 288
PERIODS = {"DISC": (2012, 2017), "VAL": (2018, 2021), "LOCK": (2022, 2026)}
NY = ZoneInfo("America/New_York")


def ny_epochs(dates, hh, mm):
    return np.array(sorted(int(pd.Timestamp(d).replace(hour=hh, minute=mm).tz_localize(NY).timestamp()) for d in dates))


rel = pd.read_csv(ROOT / "data/macro/us_releases/dates.csv")
fomc = pd.read_csv(ROOT / "data/macro/fomc_dates.csv")
US_EV = np.sort(np.concatenate([ny_epochs(rel.date, 8, 30), ny_epochs(fomc.date, 14, 0)]))
CB = {c: set(pd.read_csv(ROOT / f"data/macro/cb_meetings/{c}.csv").date.astype(str)) for c in
      ["EUR", "GBP", "JPY", "CAD", "CHF", "AUD", "NZD"]}


def near_us_event(tc):
    """bar close tc within [ev-5min, ev+30min] of any US event."""
    k = np.searchsorted(US_EV, tc - 1800)  # first event >= tc-1800  <=> tc <= ev+1800
    k = np.minimum(k, len(US_EV) - 1)
    ev = US_EV[k]
    return (tc >= ev - 300) & (tc <= ev + 1800)


def load(pair):
    d = np.load(ROOT / f"data/bars/fxcm/{pair}_M5.npz")
    t, c, spr = d["t"], d["c"], d["spr"]
    mid = c + spr / 2
    r = np.empty_like(mid); r[0] = np.nan
    r[1:] = 1e4 * np.diff(np.log(mid))
    sig = pd.Series(r).rolling(WIN, min_periods=WIN).std().shift(1).to_numpy()  # previous 288, excl. current
    ts = pd.to_datetime(t, unit="s", utc=True)
    ok = (ts.hour < 20) & (ts.weekday < 5)
    tc = t + 300
    news = near_us_event(tc)
    nonusd = pair[:3] if pair[3:] == "USD" else pair[3:]
    cbday = pd.to_datetime(tc, unit="s", utc=True).strftime("%Y-%m-%d").isin(CB[nonusd])
    news = news | np.asarray(cbday)
    exit_idx = np.searchsorted(tc, tc + HOLD)  # first bar with close >= entry close + 60 min
    return dict(t=t, tc=tc, c=c, spr=spr, mid=mid, r=r, sig=sig, ok=np.asarray(ok), news=news, exit_idx=exit_idx)


def trades(pair, D, k, hyp):
    is_news = hyp == "H2"
    with np.errstate(invalid="ignore"):
        jump = D["ok"] & (np.abs(D["r"]) > k * D["sig"]) & (D["news"] == is_news)
    cand = np.flatnonzero(jump)
    n = len(D["t"])
    rows, busy_until, holes = [], -1, 0
    for i in cand:
        if D["tc"][i] < busy_until:
            continue
        j = D["exit_idx"][i]
        if j >= n:
            continue
        if D["tc"][j] > D["tc"][i] + HOLD + 1800:  # data hole: no bar within 90 min, skip
            holes += 1
            continue
        side = -np.sign(D["r"][i]) if hyp == "H1" else np.sign(D["r"][i])
        bid0, ask0, bid1, ask1 = D["c"][i], D["c"][i] + D["spr"][i], D["c"][j], D["c"][j] + D["spr"][j]
        if side > 0:
            net = 1e4 * (bid1 / ask0 - 1)
            ent, ex = ask0, bid1
        else:
            net = 1e4 * (bid0 / ask1 - 1)
            ent, ex = bid0, ask1
        net -= SLIP
        gross = side * 1e4 * (D["mid"][j] / D["mid"][i] - 1)
        rows.append((pair, hyp, k, int(D["tc"][i]), int(D["tc"][j]), int(side), D["r"][i], D["sig"][i], ent, ex, gross, net))
        busy_until = D["tc"][j]
    return rows, holes


def cl_t(x, wk):
    x = np.asarray(x, float)
    if len(x) < 3:
        return float("nan")
    m = x.mean()
    g = pd.Series(x - m).groupby(np.asarray(wk)).sum().to_numpy()
    se = math.sqrt((g ** 2).sum()) / len(x)
    return m / se if se > 0 else float("nan")


def money(df, lots):
    usd = df.net * df.pair.map(USD_PER_BP) * lots
    s = pd.Series(usd.to_numpy(), index=pd.to_datetime(df.exit_tc, unit="s", utc=True)).sort_index()
    eq = s.cumsum()
    dd = float((eq - np.maximum.accumulate(np.maximum(eq, 0))).min())
    years = (pd.Timestamp("2026-10-31", tz="UTC") - pd.Timestamp("2012-01-01", tz="UTC")).days / 365.25
    monthly = s.groupby(s.index.strftime("%Y-%m")).sum()
    return dict(usd_per_year=float(s.sum() / years), worst_month=float(monthly.min()), max_dd=dd, total=float(s.sum()))


def summarize(df):
    out = {"n": len(df), "gross_bp": float(df.gross.mean()) if len(df) else None,
           "net_bp": float(df.net.mean()) if len(df) else None,
           "t_week": cl_t(df.net, df.week) if len(df) else None, "periods": {}, "by_pair": {}}
    for p, (a, b) in PERIODS.items():
        s = df[(df.year >= a) & (df.year <= b)]
        out["periods"][p] = {"n": len(s), "gross_bp": float(s.gross.mean()) if len(s) else None,
                             "net_bp": float(s.net.mean()) if len(s) else None,
                             "t_week": cl_t(s.net, s.week) if len(s) > 2 else None}
    for pr, s in df.groupby("pair"):
        out["by_pair"][pr] = {"n": len(s), "gross_bp": float(s.gross.mean()), "net_bp": float(s.net.mean()),
                              "t_week": cl_t(s.net, s.week)}
    return out


def main():
    allrows, holes = [], 0
    for pair in PAIRS:
        D = load(pair)
        for k in (3, 4, 6):
            for hyp in ("H1", "H2"):
                r, h = trades(pair, D, k, hyp)
                allrows += r; holes += h if k == 4 else 0
        del D
        print("done", pair, flush=True)
    df = pd.DataFrame(allrows, columns=["pair", "hyp", "k", "entry_tc", "exit_tc", "side", "jump_bp", "sigma_bp",
                                        "entry_px", "exit_px", "gross", "net"])
    dt = pd.to_datetime(df.entry_tc, unit="s", utc=True)
    df["entry_utc"] = dt.dt.strftime("%Y-%m-%d %H:%M")
    df["year"] = dt.dt.year
    df["week"] = (df.entry_tc // (7 * 86400)).astype(int)
    df[df.k == 4].drop(columns=["week"]).to_csv(OUT / "r18_trades.csv", index=False)

    res = {"holes_skipped_4x": holes, "primary": {}, "variants": {}}
    from statistics import NormalDist
    pvals = {}
    for hyp in ("H1", "H2"):
        s = df[(df.k == 4) & (df.hyp == hyp)]
        sm = summarize(s)
        sm["money"] = {f"{L}_lots": money(s, L) for L in (1, 5, 7)}
        t = sm["t_week"]
        pvals[hyp] = 1 - NormalDist().cdf(t) if t == t else 1.0
        sm["p_one_sided"] = pvals[hyp]
        res["primary"][hyp] = sm
        for k in (3, 6):
            res["variants"][f"{hyp}_{k}x"] = summarize(df[(df.k == k) & (df.hyp == hyp)])
    # Benjamini-Hochberg across H1, H2
    order = sorted(pvals, key=pvals.get)
    m, bh = len(order), {}
    for rank, h in enumerate(order, 1):
        bh[h] = pvals[h] <= 0.05 * rank / m
    # step-up: if the larger p passes, both pass
    if bh[order[-1]]:
        bh = {h: True for h in order}
    for hyp in ("H1", "H2"):
        sm = res["primary"][hyp]
        allpos = all(v["net_bp"] is not None and v["net_bp"] > 0 for v in sm["periods"].values())
        sm["checks"] = {"all_periods_net_pos": allpos, "t_ge_2": bool(sm["t_week"] >= 2), "bh_q05": bool(bh[hyp])}
        sm["PASS"] = all(sm["checks"].values())
    (OUT / "r18_results.json").write_text(json.dumps(res, indent=2, default=float))

    # sanity: a few jumps
    p4 = df[df.k == 4]
    for hyp in ("H1", "H2"):
        print(f"\n--- sanity {hyp} ---")
        print(p4[p4.hyp == hyp].sample(4, random_state=1)[["pair", "entry_utc", "sigma_bp", "jump_bp", "side",
                                                             "entry_px", "exit_px", "gross", "net"]].to_string())
    print(json.dumps({h: {k: res["primary"][h][k] for k in ("n", "gross_bp", "net_bp", "t_week", "PASS", "checks")}
                      for h in res["primary"]}, indent=1, default=float))


def selfcheck():
    tc = np.array([US_EV[0] - 301, US_EV[0] - 300, US_EV[0] + 1800, US_EV[0] + 1801])
    assert near_us_event(tc).tolist() == [False, True, True, False]
    assert abs(cl_t([1., 3, 2, 4], [0, 0, 1, 1]) - 2.5 / (2 ** 0.5 / 4)) < 1e-9


if __name__ == "__main__":
    selfcheck()
    main()
