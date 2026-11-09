"""Round 20: OeNB WP258 FOMC-shock carry test. Rules fixed in PREREG.md."""
import csv, datetime as dt, math, sys
from pathlib import Path
from zoneinfo import ZoneInfo
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
NY = ZoneInfo("America/New_York")
CCYS = ["AUD", "CAD", "CHF", "EUR", "GBP", "JPY", "NZD"]
PAIR = {"EUR": "EURUSD", "GBP": "GBPUSD", "AUD": "AUDUSD", "NZD": "NZDUSD",
        "JPY": "USDJPY", "CHF": "USDCHF", "CAD": "USDCAD"}
SLIP, MARKUP = 0.3e-4, 0.5e-4

bars = {c: np.load(ROOT / f"data/bars/fxcm/{p}_H1.npz") for c, p in PAIR.items()}
LAST = min(int(b["t"][-1]) + 3600 for b in bars.values())

def quote(c, ts):
    b = bars[c]; cl = b["t"] + 3600
    i = np.searchsorted(cl, ts, "right") - 1
    if i < 0 or ts - cl[i] > 3 * 86400: return None
    return float(b["c"][i]), float(b["c"][i] + b["spr"][i]), int(cl[i])

def ny17(d): return int(dt.datetime(d.year, d.month, d.day, 17, tzinfo=NY).timestamp())

# policy rates, daily ffill
rates, last = {}, {}
for r in csv.DictReader(open(ROOT / "data/macro/policy/policy_rates_daily.csv")):
    for k, v in r.items():
        if k != "date" and v: last[k] = float(v)
    rates[dt.date.fromisoformat(r["date"])] = dict(last)

def rate(d, c):
    while d not in rates: d -= dt.timedelta(days=1)
    return rates[d][c]

def leg(c, side, d0, d1, cost=True):
    """side +1 = long foreign ccy c vs USD. Return on USD notional."""
    q0, q1 = quote(c, ny17(d0)), quote(c, min(ny17(d1), LAST))
    if q0 is None or q1 is None or q1[2] <= q0[2]: return None
    (b0, a0, _), (b1, a1, _) = q0, q1
    s = SLIP if cost else 0.0
    if not cost: a0, a1 = b0, b1
    usd_base = PAIR[c].startswith("USD")
    buy_pair = (side > 0) != usd_base       # long foreign = buy XXXUSD / sell USDXXX
    if buy_pair: px = (b1 * (1 - s)) / (a0 * (1 + s)) - 1
    else:        px = (b0 * (1 - s)) / (a1 * (1 + s)) - 1
    sw, d = 0.0, d0
    while d < d1:
        diff = (rate(d, c) - rate(d, "USD")) * side / 100 / 365
        sw += diff - (MARKUP if cost else 0.0); d += dt.timedelta(days=1)
    return px + sw

def hml(d0, d1, cost=True):
    rk = sorted(CCYS, key=lambda c: (rate(d0, c), c))
    lo, hi = rk[:2], rk[-2:]
    r = [leg(c, 1, d0, d1, cost) for c in hi] + [leg(c, -1, d0, d1, cost) for c in lo]
    return None if None in r else 0.5 * sum(r)

def usd_long(d0, d1, cost=True):
    r = [leg(c, -1, d0, d1, cost) for c in CCYS]
    return None if None in r else sum(r) / len(r)

fomc = [dt.date.fromisoformat(r["date"]) for r in csv.DictReader(open(ROOT / "data/macro/fomc_dates.csv"))]
jk = {}
for r in csv.DictReader(open(OUT / "shocks_fed_jk_t.csv")):
    jk[dt.date.fromisoformat(r["start"][:10])] = (float(r["pc1"]), float(r["SP500"]))
END = dt.datetime.fromtimestamp(LAST, NY).date()

def run(cost):
    res = {"C1": [], "C2": [], "C3": []}
    for i, d0 in enumerate(fomc):
        if d0 not in jk or d0 >= END: continue
        d1 = fomc[i + 1] - dt.timedelta(days=1) if i + 1 < len(fomc) else END
        pc1, sp = jk[d0]
        shock = pc1 > 0 and sp < 0
        r2 = hml(d0, d1, cost)
        r1 = usd_long(d0, d1, cost) if shock else r2
        if r1 is not None: res["C1"].append((d0, r1, shock))
        if r2 is not None: res["C2"].append((d0, r2, False))
    # C3 end-of-month: last weekday of each month
    eom = []
    d = dt.date(2012, 1, 1)
    while d < END:
        nxt = (d.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
        m = nxt - dt.timedelta(days=1)  # last day of d's month
        while m.weekday() > 4: m -= dt.timedelta(days=1)
        eom.append(m); d = nxt
    for a, b in zip(eom, eom[1:] + [END]):
        if a >= END: break
        r = hml(a, b, cost)
        if r is not None: res["C3"].append((a, r, False))
    return res

def per(y): return "DISC" if y <= 2017 else "VAL" if y <= 2021 else "LOCK"

def tstat(x): x = np.asarray(x); return x.mean() / (x.std(ddof=1) / math.sqrt(len(x))) if len(x) > 2 else float("nan")

def ycl_t(rows):
    ys = sorted({d.year for d, _, _ in rows}); x = np.array([r for _, r, _ in rows])
    g = np.array([d.year for d, _, _ in rows]); e = x - x.mean()
    s = sum(e[g == y].sum() ** 2 for y in ys); G = len(ys)
    return x.mean() / (math.sqrt(s * G / (G - 1)) / len(x))

def norm_sf(t): return 0.5 * math.erfc(t / math.sqrt(2))

def main():
    lines = []
    for cost in (False, True):
        res = run(cost)
        lines.append(f"\n## {'NET (spread+0.3bp slip+swap-0.5bp/night)' if cost else 'GROSS (bid-to-bid, raw swap)'}\n")
        lines.append("| claim | n | mean bp/period | DISC | VAL | LOCK | t | year-cl t | ann ret % | ann SR | skew | p1 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        ps = {}
        for k, rows in list(res.items()):
            x = np.array([r for _, r, _ in rows]); yrs = (rows[-1][0] - rows[0][0]).days / 365.25
            ppy = len(x) / yrs
            pm = {p: np.mean([r for d, r, _ in rows if per(d.year) == p]) * 1e4 for p in ("DISC", "VAL", "LOCK")}
            t = tstat(x); ps[k] = norm_sf(t)
            sk = float(((x - x.mean()) ** 3).mean() / x.std() ** 3)
            lines.append(f"| {k} | {len(x)} | {x.mean()*1e4:.1f} | {pm['DISC']:.1f} | {pm['VAL']:.1f} | {pm['LOCK']:.1f} | "
                         f"{t:.2f} | {ycl_t(rows):.2f} | {x.mean()*ppy*100:.2f} | {x.mean()/x.std(ddof=1)*math.sqrt(ppy):.2f} | {sk:.2f} | {ps[k]:.3f} |")
            if cost:
                ok = all(v > 0 for v in pm.values()) and t >= 2
                res[k + "_ok"] = (ok, pm, x, ppy)
        if cost:
            srt = sorted(ps, key=ps.get); m = len(srt); bh = {}
            for j, k in enumerate(srt, 1): bh[k] = ps[k] * m / j <= 0.05
            lines.append("\n| claim | all 3 periods > 0 | t>=2 | BH q<0.05 | verdict | $/yr 1 lot | 5 lots | 7 lots | total $ 1 lot |")
            lines.append("|---|---|---|---|---|---|---|---|---|")
            for k in ("C1", "C2", "C3"):
                ok, pm, x, ppy = res[k + "_ok"]
                allp = all(v > 0 for v in pm.values()); t = tstat(x)
                v = "PASS" if allp and t >= 2 and bh[k] else "FAIL"
                ay = x.mean() * ppy * 1e5
                lines.append(f"| {k} | {allp} | {t>=2} | {bh[k]} | {v} | {ay:,.0f} | {ay*5:,.0f} | {ay*7:,.0f} | {x.sum()*1e5:,.0f} |")
            sh = [(d, r) for d, r, s in res["C1"] if s]
            lines.append(f"\nShock (long-USD) periods in C1: {len(sh)}; mean {np.mean([r for _, r in sh])*1e4:.1f} bp; "
                         f"dates {', '.join(str(d) for d, _ in sh)}")
            lines.append(f"Last bar used: {dt.datetime.fromtimestamp(LAST, dt.timezone.utc)} UTC")
    txt = "\n".join(lines); print(txt); (OUT / "results.md").write_text(txt)

def selftest():
    # leg sanity: zero-length period returns None; costs make net < gross
    d0 = dt.date(2015, 3, 18); d1 = dt.date(2015, 4, 28)
    g, n = leg("EUR", 1, d0, d1, False), leg("EUR", 1, d0, d1, True)
    assert n < g, (g, n)
    assert abs(leg("EUR", 1, d0, d1, False) + leg("EUR", -1, d0, d1, False)) < 1e-3
    assert abs(leg("JPY", 1, d0, d1, False) + leg("JPY", -1, d0, d1, False)) < 2e-3

if __name__ == "__main__":
    selftest(); main()
