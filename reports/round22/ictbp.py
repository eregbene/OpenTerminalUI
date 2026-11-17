"""Round 22 (2026-11-09): "ictbp" = the ICT blueprint from the 4 videos in docs/ict_videos_2026_11_09.md, 3-month test.

FROZEN before the first run (spec in the doc). FIX (after a manual chart check, before any tuning): the swept LTF low must be
inside the HTF POI -- the first run let LTF setups fire after price had left the OB, in premium (GBPUSD 08-07). Long (short = mirror), HTF/LTF = H4/M15 and D/H1:
 1 HTF market-structure break: an HTF CLOSE above the last unbroken 3-candle swing high.
 2 Range = the leg origin (lowest low between that swing high and the break) -> the highest high since (fixed when price
   first returns to the POI). POI = the order block = the run of down candles ending at / just before the origin.
   Variant FVG: POI = the lowest bullish 3-candle FVG of the leg (Jesse: HTF FVG = key level).
 3 The POI top must be in discount (<= 50% of the range). Variant OTE: the touch must reach >= 70.5% depth.
 4 After price trades into the POI (range low intact), on the LTF: a 3-candle swing low forms -> a later LTF low sweeps
   it -> an LTF CLOSE above the swing high that preceded that swing low = breaker / MSS -> enter at that close.
 5 Stop = sweep low - 1 pip; target = HTF range high (ERL); skip if < 2R. Up to 2 attempts per setup.
   Setup dies on an HTF close below the range low or after 30 HTF bars. Variant BE: half off at 2R, stop to entry.
 6 Creamer: max 2 losses per day across all pairs. Exits on M5: stop first on ties, Friday 20:00 UTC close, 10-day max.
Costs: round-trip cost_pips (bensim.bennett_strategy) deducted in R. Window 2026-08-07 .. 2026-11-06 (IC M5, DB).
    python reports/round22/ictbp.py
"""
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
from bensim.backtest import load_m5, resample  # noqa: E402
from bensim.strategy import pip  # noqa: E402

SYMS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD", "XAUUSD"]
COST = {"EURUSD": 0.9, "GBPUSD": 1.2, "USDJPY": 1.0, "AUDUSD": 1.0, "USDCAD": 1.1, "USDCHF": 1.1, "XAUUSD": 3.0}
T0, T1 = pd.Timestamp("2026-08-07", tz="UTC").timestamp(), pd.Timestamp("2026-11-06 21:00", tz="UTC").timestamp()
PAIRS = {"H4/M15": (4 * 3600, 900, "2026-05-01"), "D/H1": (86400, 3600, "2026-01-01")}


def swings(b, i_last):
    """Confirmed 3-candle swing highs/lows up to bar i_last (needs i+1 closed)."""
    h, lo = b["high"], b["low"]
    sh = [i for i in range(1, i_last) if h[i] > h[i - 1] and h[i] > h[i + 1]]
    sl = [i for i in range(1, i_last) if lo[i] < lo[i - 1] and lo[i] < lo[i + 1]]
    return sh, sl


def htf_setups(H, d, variant):
    """All HTF setups: (break_bar, origin_price, poi_top, poi_bot, swing_idx)."""
    o, h, lo, c = H["open"], H["high"], H["low"], H["close"]
    if d == -1:  # mirror: flip prices
        o, h, lo, c = -o, -lo, -h, -c
    out, used = [], set()
    n = len(c)
    for j in range(3, n):
        sh = [i for i in range(max(1, j - 60), j - 1) if h[i] > h[i - 1] and h[i] > h[i + 1]]
        if not sh:
            continue
        s = sh[-1]
        if s in used or c[j] <= h[s] or h[s + 1:j].max(initial=-np.inf) >= h[s] and c[s + 1:j].max(initial=-np.inf) > h[s]:
            continue
        used.add(s)
        L = s + int(np.argmin(lo[s:j + 1]))
        if L >= j:
            continue
        if variant == "fvg":
            gaps = [(lo[k + 1], h[k - 1]) for k in range(L + 1, j) if lo[k + 1] > h[k - 1]]
            if not gaps:
                continue
            top, bot = min(gaps, key=lambda g: g[1])  # lowest gap of the leg
        else:
            k = L if c[L] < o[L] else max([x for x in range(max(0, L - 5), L) if c[x] < o[x]], default=-1)
            if k < 0:
                continue
            a = k
            while a - 1 >= 0 and c[a - 1] < o[a - 1] and k - a < 4:
                a -= 1
            top, bot = h[a:k + 1].max(), lo[a:k + 1].min()
        out.append((j, lo[L], top, bot))
    return out


def run(sym, pair, variant):
    hsec, lsec, start = PAIRS[pair]
    m5 = load_m5(sym, start, "2026-11-07")
    if len(m5["t"]) < 1000:
        return []
    H, Lb = resample(m5, hsec), resample(m5, lsec)
    p = pip(sym)
    trades = []
    for d in (1, -1):
        f = (lambda x: x) if d == 1 else (lambda x: -x)
        Hh, Hl, Hc = (H["high"], H["low"], H["close"]) if d == 1 else (-H["low"], -H["high"], -H["close"])
        lh, ll, lc = (Lb["high"], Lb["low"], Lb["close"]) if d == 1 else (-Lb["low"], -Lb["high"], -Lb["close"])
        mh, ml, mc = (m5["high"], m5["low"], m5["close"]) if d == 1 else (-m5["low"], -m5["high"], -m5["close"])
        for j, origin, top, bot in htf_setups(H, d, "fvg" if variant == "fvg" else "ob"):
            t_break = H["t"][j] + hsec
            if t_break < T0 - 30 * hsec or t_break > T1:
                continue
            end_j = min(len(Hc) - 1, j + 30)
            # first HTF bar after the break that trades into the POI; range high fixed there
            touch = next((k for k in range(j + 1, end_j + 1) if Hl[k] <= top), None)
            if touch is None or Hc[j + 1:touch + 1].min(initial=np.inf) < origin:
                continue
            rhigh = Hh[j:touch + 1].max()
            rng = rhigh - origin
            if rng <= 0 or top > origin + 0.5 * rng:  # POI must be in discount
                continue
            if variant == "ote" and Hl[j + 1:end_j + 1].min() > rhigh - 0.705 * rng:
                continue
            t_from, t_to = H["t"][touch], H["t"][end_j] + hsec
            a, b = np.searchsorted(Lb["t"], t_from), np.searchsorted(Lb["t"], t_to)
            attempts, k = 0, a + 2
            while k < b and attempts < 2:
                if lc[k] < origin:  # HTF range broken on a close: setup dead
                    break
                # LTF swing low confirmed before k, its preceding swing high, then a sweep, then a close above that high
                sls = [i for i in range(max(a, 1), k - 1) if ll[i] < ll[i - 1] and ll[i] < ll[i + 1]]
                fired = False
                for s1 in reversed(sls[-3:]):
                    shs = [i for i in range(max(a, 1), s1) if lh[i] > lh[i - 1] and lh[i] > lh[i + 1]]
                    if not shs:
                        continue
                    hi_lvl = lh[shs[-1]]
                    swept = ll[s1 + 1:k].min(initial=np.inf) < ll[s1]
                    if swept and ll[s1:k].min() > top:  # FIX after the manual chart check: the swept low must be INSIDE the POI
                        continue
                    if swept and lc[k] > hi_lvl and lc[k - 1] <= hi_lvl:
                        entry = lc[k]
                        stop = ll[s1:k + 1].min() - p
                        risk = entry - stop
                        if risk <= 0 or (rhigh - entry) < 2 * risk:
                            continue
                        t_in = Lb["t"][k] + lsec
                        if not (T0 <= t_in <= T1):
                            continue
                        res = exit_(m5["t"], mh, ml, mc, t_in, entry, stop, rhigh, variant == "be")
                        if res is None:
                            continue
                        r_gross, t_out, why = res
                        r = r_gross - COST[sym] * p / risk
                        trades.append(dict(sym=sym, pair=pair, variant=variant, dir=d, t_in=t_in, t_out=t_out, entry=f(entry),
                                           stop=f(stop), target=f(rhigh), risk_pips=risk / p, rr=(rhigh - entry) / risk,
                                           r=r, why=why))
                        attempts += 1
                        fired = True
                        k = max(k + 1, int(np.searchsorted(Lb["t"], t_out)))
                        break
                if not fired:
                    k += 1
    return trades


def exit_(t, h, lo, c, t_in, entry, stop, target, be):
    i0 = int(np.searchsorted(t, t_in))
    i1 = int(np.searchsorted(t, t_in + 10 * 86400))
    risk = entry - stop
    half, st = False, stop
    for i in range(i0, min(i1, len(t))):
        wd, hr = pd.Timestamp(t[i], unit="s").dayofweek, pd.Timestamp(t[i], unit="s").hour
        if wd == 4 and hr >= 20:
            x = (c[i] - entry) / risk
            return (0.5 * 2 + 0.5 * x if half else x), t[i], "friday"
        if lo[i] <= st:
            x = (st - entry) / risk
            return (0.5 * 2 + 0.5 * x if half else x), t[i], "stop" if not half else "be"
        if be and not half and h[i] >= entry + 2 * risk:
            half, st = True, entry
        if h[i] >= target:
            x = (target - entry) / risk
            return (0.5 * 2 + 0.5 * x if half else x), t[i], "target"
    return None


def day_cap(df):
    """Creamer: no new trades after 2 losses on that UTC day (all pairs), in entry order."""
    keep, losses = [], defaultdict(int)
    for r in df.sort_values("t_in").itertuples():
        day = pd.Timestamp(r.t_in, unit="s").date()
        if losses[day] >= 2:
            continue
        keep.append(r.Index)
        losses[day] += r.r < 0
    return df.loc[keep]


if __name__ == "__main__":
    rows = []
    for pair in PAIRS:
        for variant in ("base", "fvg", "ote", "be"):
            for s in SYMS:
                rows += run(s, pair, variant)
            print(f"done {pair} {variant}", flush=True)
    df = pd.DataFrame(rows)
    df.to_csv("D:/Devops/trading/OpenTerminalUI/reports/round22/ictbp_trades_fixed.csv", index=False)
    print(f"\nwindow {pd.Timestamp(T0, unit='s').date()} .. {pd.Timestamp(T1, unit='s').date()}, net of costs, 2-loss/day cap applied")
    for (pair, var), g in df.groupby(["pair", "variant"]):
        g = day_cap(g)
        t = g.r.mean() / g.r.std() * np.sqrt(len(g)) if len(g) > 1 else 0
        print(f"{pair:7} {var:5} {len(g):4} trades  win {np.mean(g.r > 0):4.0%}  {g.r.mean():+.3f}R/trade  total {g.r.sum():+6.1f}R  "
              f"t {t:+.2f}  ${g.r.sum() * 500:+8,.0f} at $500  median stop {g.risk_pips.median():.1f} pips  avg RR {g.rr.mean():.1f}")
    print("\nper pair (H4/M15 base):")
    g = day_cap(df[(df.pair == "H4/M15") & (df.variant == "base")])
    for s, x in g.groupby("sym"):
        print(f"  {s}: {len(x)} trades {x.r.sum():+.1f}R")
