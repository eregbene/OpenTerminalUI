"""Round 31: the user's first table + the daily-discount idea on FXCM, 25 pairs, 2012-2026 (pre-registration: PREREG.md).
Same zone and trade as round 28 (reuses zone_map.py's helpers so the definitions are identical).
    python reports/round31/fxcm_zones.py
"""
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = "D:/Devops/trading/OpenTerminalUI"
sys.path.insert(0, ROOT)
sys.path.insert(0, ROOT + "/reports/round28")
OUT = Path(ROOT + "/reports/round31")
CCY = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]
PAIRS = [a + b for i, a in enumerate(CCY) for b in CCY[i + 1:] if Path(ROOT + f"/data/bars/fxcm/{a + b}_M5.npz").exists()]
TFS = {"H1": 3600, "H4": 14400, "D1": 86400}
SPLIT = pd.Timestamp("2021-10-01", tz="UTC").timestamp()


def run_sym(sym):
    import ctypes
    ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)  # below normal
    from zone_map import atr14, first, sim, structure, session
    from bensim import db
    from bensim.backtest import resample
    from bensim.fxcm_history import load
    raw = load(sym, "M5")
    ok = (raw["high"] >= raw["low"]) & (raw["close"] > 0)
    raw = {k: v[ok] for k, v in raw.items()}
    pip = 0.01 if sym.endswith("JPY") else 1e-4
    spr_med = float(np.median(raw["spr"][raw["spr"] > 0])) if (raw["spr"] > 0).any() else 2 * pip
    cost_px, buf = spr_med + 0.2 * pip, 5 * pip
    with db.connect() as c:
        news = np.array(sorted(r[0] for r in c.execute("select extract(epoch from ts)::bigint from economic_events where impact ilike 'high%%' "
                                                       "and currency = any(%s)", [[sym[:3], sym[3:]]]).fetchall()), np.int64)

    def near_news(ts):
        i = np.searchsorted(news, ts)
        return any(0 <= j < len(news) and abs(news[j] - ts) <= 3600 for j in (i - 1, i))

    rows = []
    for d in (1, -1):
        m = {"t": raw["t"], "open": raw["open"], "high": raw["high"], "low": raw["low"], "close": raw["close"]} if d == 1 else \
            {"t": raw["t"], "open": -raw["open"], "high": -raw["low"], "low": -raw["high"], "close": -raw["close"]}
        t, hi, lo = m["t"], m["high"], m["low"]
        for tf, sec in TFS.items():
            b = resample(m, sec)
            at = atr14(b)
            lastH, _, _ = structure(b)
            win = 100 * sec
            for i in range(51, len(b["t"])):
                gap = b["low"][i] - b["high"][i - 2]
                if not (gap > 0 and gap >= 0.3 * at[i]):
                    continue
                ct = b["t"][i] + sec
                top, bot, liq = b["low"][i], b["high"][i - 2], b["low"][i - 6:i - 1].min()
                sl = liq - buf
                stp = top - sl
                z0 = np.searchsorted(t, ct)
                zend = np.searchsorted(t, ct + win)
                if z0 >= len(t) - 2:
                    continue
                rlo, rhi = b["low"][i - 50:i + 1].min(), b["high"][i - 50:i + 1].max()
                row = dict(sym=sym, dir=d, tf=tf, created=ct, stop_pips=stp / pip, liq_atr=(top - liq) / at[i],
                           bos=bool(max(b["close"][i - 1], b["close"][i]) > lastH[i - 2]),
                           fib_pos=(top - rlo) / (rhi - rlo) if rhi > rlo else np.nan)
                k = first(lo[z0:zend] <= top)
                row["touched"] = k >= 0
                if k >= 0:
                    k += z0
                    br = first(lo[k:zend] <= sl)
                    fill = first(lo[k:zend] <= bot)
                    row.update(hours_to_touch=(t[k] - ct) / 3600, filled=fill >= 0, hours_to_fill=(t[k + fill] - ct) / 3600 if fill >= 0 else np.nan,
                               broke=br >= 0, touch_session=session(t[k]), news_touch=near_news(t[k]),
                               R=sim(m, k, top, sl, top + 3 * stp, win=win) - cost_px / stp, cost_R=cost_px / stp)
                    if br >= 0:
                        ab = k + br
                        back = first(hi[ab:np.searchsorted(t, t[ab] + 5 * 86400)] >= top)
                        row["back_5d"] = back >= 0
                rows.append(row)
    return rows


def tstat(x):
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else np.nan


def main():
    f = OUT / "fxcm_zone_events.pkl"  # our own local cache
    if not f.exists():
        with ProcessPoolExecutor(4) as ex:
            rows = [r for part in ex.map(run_sym, PAIRS) for r in part]
        pd.DataFrame(rows).to_pickle(f)
    ev = pd.read_pickle(f)
    ev["period"] = np.where(ev.created < SPLIT, "2012-21 (untouched)", "2021-26")
    tch = ev[ev.touched.astype(bool)].copy()
    tch["full_win"] = tch.R > 2.5
    print(f"Round 31: FXCM {len(PAIRS)} pairs, 2012-01 .. 2026-10, {len(ev)} zones\n")
    print("A) YOUR FIRST TABLE ON 25 PAIRS x 14 YEARS (zone edge, stop under the liquidity, 1:3, real FXCM spread)")
    print(f"{'':22s}" + "".join(f"{tf + ' ' + p[:7]:>18s}" for tf in TFS for p in ("2012-21", "2021-26")))
    lines = {"zones": [], "price came back": [], "fully filled": [], "hit 1:3 (held)": [], "took the liquidity": [],
             "back above within 5d": [], "median stop pips": [], "R per trade": [], "total R": [], "luck band +-": [], "t": []}
    for tf in TFS:
        for per in ("2012-21 (untouched)", "2021-26"):
            g = ev[(ev.tf == tf) & (ev.period == per)]
            x = tch[(tch.tf == tf) & (tch.period == per)]
            lines["zones"].append(f"{len(g)}")
            lines["price came back"].append(f"{100 * g.touched.astype(bool).mean():.0f}%")
            lines["fully filled"].append(f"{100 * x.filled.astype(bool).mean():.0f}%")
            lines["hit 1:3 (held)"].append(f"{100 * x.full_win.mean():.0f}%")
            lines["took the liquidity"].append(f"{100 * x.broke.astype(bool).mean():.0f}%")
            br = x[x.broke.astype(bool)]
            lines["back above within 5d"].append(f"{100 * br.back_5d.astype(bool).mean():.0f}%")
            lines["median stop pips"].append(f"{x.stop_pips.median():.0f}")
            lines["R per trade"].append(f"{x.R.mean():+.3f}")
            lines["total R"].append(f"{x.R.sum():+.0f}")
            lines["luck band +-"].append(f"{2 * x.R.std() * np.sqrt(len(x)):.0f}")
            lines["t"].append(f"{tstat(x.R):+.1f}")
    for k, v in lines.items():
        print(f"{k:22s}" + "".join(f"{c:>18s}" for c in v))

    print("\nB) PRE-REGISTERED (judged on 2012-01 .. 2021-09 only)")
    hyp = {"H1  1D zone in discount": (tch.tf == "D1") & (tch.fib_pos < 0.5),
           "H2  1H BOS + far liq + discount + 07-17 UTC": (tch.tf == "H1") & tch.bos.astype(bool) & (tch.liq_atr > 2) & (tch.fib_pos < 0.5)
           & tch.touch_session.isin(["London 7-12", "NY 12-17"])}
    for lab, mk in hyp.items():
        for per in ("2012-21 (untouched)", "2021-26"):
            r = tch[mk & (tch.period == per)].R
            verdict = ("PASS" if r.sum() > 0 and tstat(r) >= 2 else "FAIL") if per.startswith("2012") else "(info)"
            print(f"   {lab:44s} {per:20s} n {len(r):5d}  hit 1:3 {100 * (r > 2.5).mean():4.1f}%  {r.mean():+.3f} R/trade  total {r.sum():+7.1f}R  t {tstat(r):+.1f}  {verdict}")
    print("\nC) SAME FILTERS AS ROUND 28 TABLE B, ALL TF, 2012-21 / 2021-26 (R per trade)")
    f_ = {"base": pd.Series(True, index=tch.index), "BOS": tch.bos.astype(bool), "liquidity far (> 2 ATR)": tch.liq_atr > 2,
          "discount": tch.fib_pos < 0.5, "premium": tch.fib_pos >= 0.5, "touch 07-17 UTC": tch.touch_session.isin(["London 7-12", "NY 12-17"]),
          "no news +-1h": ~tch.news_touch.astype(bool)}
    for lab, mk in f_.items():
        print(f"   {lab:24s} " + "  ".join(f"{tf} {tch[mk & (tch.tf == tf) & (tch.period == '2012-21 (untouched)')].R.mean():+.3f}/"
                                          f"{tch[mk & (tch.tf == tf) & (tch.period == '2021-26')].R.mean():+.3f}" for tf in TFS))
    print("\nD) 1D DISCOUNT BY YEAR (total R) -- does it hold every year or only some?")
    x = tch[(tch.tf == "D1") & (tch.fib_pos < 0.5)]
    yr = pd.to_datetime(x.created, unit="s").dt.year
    print("   " + ", ".join(f"{y}: {g.R.sum():+.0f} (n {len(g)})" for y, g in x.groupby(yr)))


if __name__ == "__main__":
    main()
