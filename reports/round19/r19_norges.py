"""Round 19: Norges Bank FX-transaction announcement effect on EURNOK (rules: PREREG.md)."""
import glob, json, os, zipfile, io
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd
from scipy import stats

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(ROOT, "reports", "round19")
NOK = os.path.join(ROOT, "data", "bars", "nok")
RAW = r"D:\ClaudeTemp\nokdl\histdata"
OSLO = ZoneInfo("Europe/Oslo")
THR, COST, MARKUP, USD_BP = 300, 4.3, 0.5, 11.6
PERIODS = [(2015, 2018), (2019, 2022), (2023, 2026)]


def load_m1():
    npz = os.path.join(NOK, "EURNOK_M1_histdata.npz")
    if not os.path.exists(npz):
        parts = []
        for zp in sorted(glob.glob(os.path.join(RAW, "eurnok_*.zip"))):
            if os.path.getsize(zp) == 0:
                continue
            z = zipfile.ZipFile(zp)
            name = [n for n in z.namelist() if n.endswith(".csv")][0]
            parts.append(pd.read_csv(z.open(name), sep=";", header=None,
                                     names=["t", "o", "h", "l", "c", "v"]))
        d = pd.concat(parts).drop_duplicates("t")
        # histdata timestamps: fixed EST (UTC-5) all year
        ts = pd.to_datetime(d.t, format="%Y%m%d %H%M%S") + pd.Timedelta(hours=5)
        np.savez_compressed(npz, t=ts.values.astype("datetime64[s]").astype(np.int64),
                            o=d.o.values, h=d.h.values, l=d.l.values, c=d.c.values)
    z = np.load(npz)
    s = pd.DataFrame({"o": z["o"], "c": z["c"]},
                     index=pd.to_datetime(z["t"], unit="s", utc=True)).sort_index()
    return s[~s.index.duplicated()]


def price_at(m1, day, hh, mm):
    """open of first M1 bar in [local hh:mm, +10min]; returns (price, utc ts) or (nan, None)"""
    t0 = pd.Timestamp(day.year, day.month, day.day, hh, mm, tz=OSLO).tz_convert("UTC")
    w = m1.loc[t0:t0 + pd.Timedelta(minutes=10)]
    return (w.o.iloc[0], w.index[0]) if len(w) else (np.nan, None)


def rates():
    e = pd.read_csv(os.path.join(ROOT, "data/macro/policy/policy_rates_daily.csv"),
                    parse_dates=["date"]).set_index("date")["EUR"]
    z = zipfile.ZipFile(os.path.join(ROOT, "data/macro/policy/cbpol.zip"))
    b = pd.read_csv(z.open("WS_CBPOL_csv_flat.csv"), low_memory=False)
    b.columns = [c.split(":")[0] for c in b.columns]
    b = b[b.REF_AREA.str.startswith("NO") & b.FREQ.str.startswith("D")]
    n = pd.Series(b.OBS_VALUE.values, index=pd.to_datetime(b.TIME_PERIOD)).sort_index()
    idx = pd.date_range("2014-01-01", "2026-12-31")
    return e.reindex(idx).ffill(), n.reindex(idx).ffill()


def announcements():
    a = pd.read_csv(os.path.join(NOK, "norges_ANN_FX_SPU.csv"), sep=";", thousands=",")
    a["date"] = pd.to_datetime(a.TIME_PERIOD)
    a["A"] = np.where(a.TRANSACTION_TYPE == "B", 1, -1) * a.OBS_VALUE.astype(float)
    a = a.sort_values("date").reset_index(drop=True)
    a["S"] = a.A.diff()
    a["regular"] = pd.PeriodIndex(a.TRANSACTION_MONTH, freq="M") > a.date.dt.to_period("M")
    return a[a.regular & (a.date >= "2015-01-01") & (a.date <= "2026-10-31")].reset_index(drop=True)


def summarize(df, col):
    x = df[col].dropna().values
    n = len(x)
    t = x.mean() / (x.std(ddof=1) / np.sqrt(n)) if n > 2 else np.nan
    p = stats.t.sf(t, n - 1) if n > 2 else np.nan
    per = {}
    for a, b in PERIODS:
        y = df[(df.year >= a) & (df.year <= b)][col].dropna()
        per[f"{a}-{b}"] = {"n": int(len(y)), "mean": float(y.mean()) if len(y) else None}
    return {"n": n, "mean_bp": float(x.mean()), "sd_bp": float(x.std(ddof=1)), "t": float(t),
            "p_one_sided": float(p), "hit": float((x > 0).mean()), "periods": per}


def dollars(df, col):
    d = df.dropna(subset=[col])
    out = {}
    for lots in (1, 5, 7):
        usd = d[col] * USD_BP * lots
        eq = usd.cumsum()
        out[str(lots)] = {
            "per_year": {int(k): round(v, 0) for k, v in usd.groupby(d.year).sum().items()},
            "avg_per_year": round(usd.sum() / (d.date.max() - d.date.min()).days * 365.25, 0),
            "worst_month": round(usd.groupby(d.date.dt.to_period("M")).sum().min(), 0),
            "max_dd": round((eq - eq.cummax().clip(lower=0)).min(), 0),
            "total": round(usd.sum(), 0)}
    return out


def main():
    m1 = load_m1()
    rE, rN = rates()
    ev = announcements()
    days = pd.Index(sorted(set(m1.index.tz_convert(OSLO).date)))
    rows = []
    for _, r in ev.iterrows():
        d = r.date.date()
        p955, _ = price_at(m1, d, 9, 55)
        pin, tin = price_at(m1, d, 10, 5)
        p16, _ = price_at(m1, d, 16, 0)
        # next weekday after d that has a 16:00 Oslo bar (Sunday-evening bars are not a trading day)
        d2, p2 = None, np.nan
        for dd in days[days > d][:6]:
            if dd.weekday() < 5 and not np.isnan(price_at(m1, dd, 16, 0)[0]):
                d2, p2 = dd, price_at(m1, dd, 16, 0)[0]
                break
        dirn = 1 if r.S >= THR else (-1 if r.S <= -THR else 0)
        rec = dict(date=r.date, year=r.date.year, month_for=r.TRANSACTION_MONTH, type=r.TRANSACTION_TYPE,
                   amount=r.OBS_VALUE, A=r.A, S=r.S, dir=dirn, p0955=p955, entry=pin,
                   entry_utc=str(tin), exit_h1=p16, exit_day2=str(d2), exit_h2=p2,
                   day0_bp=(p16 / p955 - 1) * 1e4, first5_bp=(pin / p955 - 1) * 1e4)
        if dirn:
            rec["h1_gross"] = dirn * (p16 / pin - 1) * 1e4
            rec["h1_net"] = rec["h1_gross"] - COST
            nights = (pd.Timestamp(d2) - pd.Timestamp(d)).days if d2 else np.nan
            ts = pd.Timestamp(d)
            carry = dirn * (rE[ts] - rN[ts]) * 100 / 365 - MARKUP
            rec.update(nights=nights, r_eur=rE[ts], r_nok=rN[ts],
                       h2_gross=dirn * (p2 / pin - 1) * 1e4,
                       h2_net=dirn * (p2 / pin - 1) * 1e4 - COST + carry * nights,
                       first5_signed=dirn * rec["first5_bp"])
        rows.append(rec)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT, "r19_events.csv"), index=False)
    tr = df[df.dir != 0]
    res = {"H1": summarize(tr, "h1_net"), "H2": summarize(tr, "h2_net")}
    res["H1"]["gross_mean_bp"] = float(tr.h1_gross.mean())
    res["H2"]["gross_mean_bp"] = float(tr.h2_gross.mean())
    # BH over two p-values
    ps = sorted([(res[k]["p_one_sided"], k) for k in ("H1", "H2")])
    m = len(ps); ok = 0
    for i, (p, k) in enumerate(ps, 1):
        if p <= 0.05 * i / m:
            ok = i
    for i, (p, k) in enumerate(ps, 1):
        h = res[k]
        h["bh_sig"] = i <= ok
        h["PASS"] = bool(h["bh_sig"] and h["t"] >= 2 and
                         all(v["mean"] is not None and v["mean"] > 0 for v in h["periods"].values()))
        h["dollars"] = dollars(tr, "h1_net" if k == "H1" else "h2_net")
    # diagnostics
    full = df.dropna(subset=["day0_bp"])
    sl = stats.linregress(full.S / 1000, full.day0_bp)
    res["diag"] = {
        "events_regular": int(len(df)), "events_traded": int(len(tr)),
        "missing_entry_or_exit_h1": int(tr.h1_net.isna().sum()), "missing_h2": int(tr.h2_net.isna().sum()),
        "first5_signed_mean_bp": float(tr.first5_signed.mean()),
        "day0_on_S_slope_bp_per_bn": float(sl.slope), "day0_on_S_t": float(sl.slope / sl.stderr),
        "day0_on_S_n": int(len(full)),
        "m1_coverage": [str(m1.index.min()), str(m1.index.max()), int(len(m1))],
        "costs": "4.0bp fixed RT spread + 0.3bp slip (histdata bid only)"}
    json.dump(res, open(os.path.join(OUT, "r19_results.json"), "w"), indent=1, default=str)
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
