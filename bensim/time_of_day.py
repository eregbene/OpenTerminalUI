"""Flow-based edge: does a currency weaken during its own business hours? (Ranaldo 2009 style) -- discovery 2012-2019,
untouched test 2020-now, 25 FXCM pairs, H1 bars.

    python -m bensim.time_of_day

Rules fixed BEFORE the first run (2026-10-14):
  1. For every pair and every UTC hour, the mean H1 log return (bp) and its t-stat in DISCOVERY (2012-01 .. 2019-12).
  2. A window = a block of 2-4 consecutive UTC hours. Candidate windows are picked ONLY from discovery: mean return of
     the block |t| >= 3 AND same sign in >= 6 of the 8 discovery years AND |mean block return| > 1.5 x the pair's
     median spread (in bp) -- so a round trip can pay for itself. Direction = the discovery sign.
  3. TEST (2020-01 .. now, never looked at): trade each selected window every weekday: enter at the close of the
     hour before the block, exit at its last hour's close, on MID prices, paying half the REAL spread at each of
     those two moments (not a median -- rollover spreads are huge). Report per window and in
     total, by year.
  Also printed: the home-currency hypothesis check (does each currency weaken in its local business hours?).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .fxcm_history import OUT, load

HOME = {"JPY": range(0, 8), "AUD": range(22, 24), "NZD": range(21, 24), "EUR": range(7, 15), "CHF": range(7, 15),
        "GBP": range(7, 15), "USD": range(13, 21), "CAD": range(13, 21)}  # rough local business hours in UTC


def hourly(sym: str) -> tuple[pd.DataFrame, float]:
    """MID-price hourly returns (bid close + half the real spread at that close) and the real spread per hour.
    Bid-only returns are biased around the 21-22 UTC rollover: the bid dips as the spread blows out and recovers
    after -- a quote artifact that looked like a huge edge in the first run (2026-10-14). Mid removes it."""
    m = load(sym, "H1")
    t = pd.to_datetime(m["t"], unit="s")
    mid = pd.Series(m["close"] + m["spr"] / 2, index=t)
    r = np.log(mid).diff() * 1e4  # previous hour's mid close -> this hour's mid close, bp
    spr = pd.Series(m["spr"] / m["close"] * 1e4, index=t)
    df = pd.DataFrame({"r": r, "spr": spr, "hour": t.hour, "year": t.year, "date": t.normalize(), "wd": t.dayofweek})
    df = df[df.wd < 5]
    spr_bp = float(np.median(m["spr"] / m["close"]) * 1e4)
    return df, spr_bp


def windows(df: pd.DataFrame, spr: float) -> list[tuple[int, int, int, float, float]]:
    """(start hour, length, direction, discovery mean bp/day, t) for qualifying blocks, best first, non-overlapping."""
    disc = df[df.year <= 2019]
    daily = disc.pivot_table(index="date", columns="hour", values="r", aggfunc="sum")
    out = []
    for L in (2, 3, 4):
        for h in range(24):
            hrs = [(h + i) % 24 for i in range(L)]
            if any(x not in daily.columns for x in hrs) or (h + L > 24):  # keep blocks inside one UTC day
                continue
            blk = daily[hrs].dropna().sum(axis=1)
            if len(blk) < 500:
                continue
            mean, t = blk.mean(), blk.mean() / blk.std() * np.sqrt(len(blk))
            yrs = blk.groupby(blk.index.year).mean()
            if abs(t) >= 3 and (np.sign(yrs) == np.sign(mean)).sum() >= 6 and abs(mean) > 1.5 * spr:
                out.append((h, L, int(np.sign(mean)), float(mean), float(t)))
    out.sort(key=lambda x: -abs(x[4]))
    chosen, used = [], set()
    for w in out:
        hrs = set(range(w[0], w[0] + w[1]))
        if not hrs & used:
            chosen.append(w)
            used |= hrs
    return chosen


def main() -> None:
    pairs = sorted(f.stem.split("_")[0] for f in OUT.glob("*_H1.npz"))
    print("HOME-HOURS check (discovery 2012-2019): mean bp per hour of the pair during each currency's local hours")
    rows, tests = [], []
    for s in pairs:
        df, spr = hourly(s)
        disc = df[df.year <= 2019]
        by_h = disc.groupby("hour").r.mean()
        base, quote = s[:3], s[3:]
        rows.append((s, by_h[list(HOME[base])].mean(), by_h[list(HOME[quote])].mean(), spr))
        for h, L, d, mean, t in windows(df, spr):
            test = df[df.year >= 2020]
            daily = test[test.hour.isin(range(h, h + L))].groupby("date").r.sum()
            # real cost: half the spread at the entry close (bar before the block) + half at the exit close
            sp = test.set_index(["date", "hour"]).spr
            cost = (sp.xs(h - 1, level="hour").reindex(daily.index) + sp.xs(h + L - 1, level="hour").reindex(daily.index)) / 2 if h > 0 else spr
            pnl = (d * daily - cost).dropna()
            ys = pnl.groupby(pnl.index.year).sum()
            tests.append({"pair": s, "window_utc": f"{h:02d}-{h + L:02d}", "dir": "long" if d == 1 else "short",
                          "disc_bp_day": round(mean, 2), "disc_t": round(t, 1), "spread_bp": round(spr, 2),
                          "test_days": len(pnl), "test_bp_day": round(pnl.mean(), 2),
                          "test_t": round(pnl.mean() / pnl.std() * np.sqrt(len(pnl)), 2), "test_years+": f"{(ys > 0).sum()}/{len(ys)}"})
    h = pd.DataFrame(rows, columns=["pair", "bp/h in BASE ccy home hours", "bp/h in QUOTE ccy home hours", "spread_bp"])
    print(h.round(3).to_string(index=False))
    print("  (base-currency weakness in its home hours = negative first column; quote weakness = positive second column)")
    t = pd.DataFrame(tests)
    print(f"\nWINDOWS picked on 2012-2019 only, traded untouched 2020-now ({len(t)} windows):")
    if len(t):
        print(t.to_string(index=False))
        print(f"\nALL windows together, test: mean {t.test_bp_day.mean():+.2f} bp/day per window, "
              f"{(t.test_bp_day > 0).sum()}/{len(t)} windows positive after spread")
    t.to_csv("data/time_of_day_windows.csv", index=False)


if __name__ == "__main__":
    main()
