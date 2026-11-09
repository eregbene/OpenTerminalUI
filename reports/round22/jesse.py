"""Round 22b (2026-11-09): Jesse Rogers' 3 ICT entry models, as the slides show them, 3-month test (same window/pairs/costs
as ictbp.py). FROZEN before the first run. Long (short = mirror), HTF H4 key level -> M15 entry:
 KEY LEVEL (2 types, reported separately):
   FVG = a bullish H4 FVG (low[k+1] > high[k-1]), live for 30 H4 bars after it forms; tapped = first M15 low <= gap top.
   ERL = a confirmed H4 3-candle swing low (last 30 H4 bars); tapped = first M15 low below it (liquidity taken).
   The setup lives 2 days (192 M15 bars) from the tap and dies on an H4 close below the gap bottom / swing low.
 ENTRY MODELS (each at most once per setup):
   iFVG = a bearish M15 FVG formed from 16 bars before the tap onwards gets an M15 CLOSE above its top -> enter at that close.
   CISD = "liquidity sweep -> engulf": an M15 swing low (from 16 bars before the tap) is swept, then an M15 close above
          the OPEN of the first candle of the last run (>= 2) of down-close candles that ends at/after the sweep -> enter.
   MSS  = "structural break, retrace into imbalance": an M15 close above the latest confirmed M15 swing high, with a
          bullish M15 FVG in the leg from its lowest low -> limit at the top of the newest such FVG, filled on M5 within
          16 M15 bars after the break (cancelled if an M15 close goes below the FVG bottom first).
 Stop = lowest low since the tap (MSS: of the leg) - 1 pip. Target = the latest confirmed H4 swing high above the entry.
 Skip if < 2R. Exits on M5 (stop first on ties, Friday 20:00 UTC close, 10-day max). Costs = ictbp COST in R.
    python reports/round22/jesse.py
"""
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI")
sys.path.insert(0, "D:/Devops/trading/OpenTerminalUI/reports/round22")
from bensim.backtest import load_m5, resample  # noqa: E402
from bensim.strategy import pip  # noqa: E402
from ictbp import COST, SYMS, T0, T1, exit_  # noqa: E402

HS, LS, LIFE, PRE = 4 * 3600, 900, 192, 16


def flip(b, d):
    if d == 1:
        return b["open"], b["high"], b["low"], b["close"]
    return -b["open"], -b["low"], -b["high"], -b["close"]


def keys(Ht, Hh, Hl, kind):
    """(known_time, top, bottom) of each key level, long space."""
    out = []
    for k in range(1, len(Hh) - 1):
        if kind == "FVG" and Hl[k + 1] > Hh[k - 1]:
            out.append((Ht[k + 1] + HS, Hl[k + 1], Hh[k - 1], k + 1))
        if kind == "ERL" and Hl[k] < Hl[k - 1] and Hl[k] < Hl[k + 1]:
            out.append((Ht[k + 1] + HS, Hl[k], Hl[k], k + 1))
    return out


def h4_target(Ht, Hh, t, entry):
    """Latest confirmed H4 swing high (confirmed before t) above the entry."""
    j = int(np.searchsorted(Ht, t - HS, side="right"))  # H4 bars fully closed by t: index < j
    for i in range(j - 2, max(0, j - 60), -1):
        if Hh[i] > Hh[i - 1] and Hh[i] > Hh[i + 1] and Hh[i] > entry:
            return Hh[i]
    return None


def run(sym):
    m5 = load_m5(sym, "2026-05-01", "2026-11-07")
    if len(m5["t"]) < 1000:
        return []
    H, L = resample(m5, HS), resample(m5, LS)
    p, trades = pip(sym), []
    for d in (1, -1):
        Ho, Hh, Hl, Hc = flip(H, d)
        o, h, lo, c = flip(L, d)
        mo, mh, ml, mc = flip(m5, d)
        t, Ht = L["t"], H["t"]
        for kind in ("FVG", "ERL"):
            for known, top, bot, kidx in keys(Ht, Hh, Hl, kind):
                if known < T0 - 2 * 86400 or known > T1:
                    continue
                a0 = int(np.searchsorted(t, known))
                a_end = int(np.searchsorted(t, Ht[min(kidx + 30, len(Ht) - 1)]))
                tap = next((i for i in range(a0, a_end) if (lo[i] <= top if kind == "FVG" else lo[i] < top)), None)
                if tap is None:
                    continue
                done = set()
                end = min(tap + LIFE, len(c) - 1)
                for j in range(tap + 1, end):
                    if (t[j] + LS) % HS == 0 and c[j] < bot:  # H4 close below the key level: dead
                        break
                    if len(done) == 3:
                        break
                    low_since = lo[tap:j + 1].min()
                    s0 = max(2, tap - PRE)
                    cand = []
                    # iFVG: bearish gap (h[k+1] < lo[k-1]) formed from s0, first close above its top
                    if "iFVG" not in done:
                        for k in range(s0, j - 1):
                            if h[k + 1] < lo[k - 1] and c[j] > lo[k - 1] >= c[j - 1]:
                                cand.append(("iFVG", c[j], t[j] + LS, low_since))
                                break
                    # CISD: swing low swept, then close above the open of the last down run's first candle
                    if "CISD" not in done:
                        sw = [i for i in range(s0, j - 1) if lo[i] < lo[i - 1] and lo[i] < lo[i + 1] and lo[i + 1:j].min() < lo[i]]
                        if sw:
                            sweep_bar = next(x for x in range(sw[0] + 1, j) if lo[x] < lo[sw[0]])
                            m = j - 1
                            while m > s0 and not c[m] < o[m]:
                                m -= 1
                            s = m
                            while s - 1 > s0 and c[s - 1] < o[s - 1]:
                                s -= 1
                            if m - s >= 1 and m >= sweep_bar and c[j] > o[s] >= c[j - 1]:
                                cand.append(("CISD", c[j], t[j] + LS, low_since))
                    # MSS: close above the latest confirmed swing high, FVG in the leg, limit at the gap top
                    if "MSS" not in done:
                        shs = [i for i in range(s0, j - 1) if h[i] > h[i - 1] and h[i] > h[i + 1]]
                        if shs and c[j] > h[shs[-1]] >= c[j - 1]:
                            sh = shs[-1]
                            ll = sh + int(np.argmin(lo[sh:j + 1]))
                            gaps = [k for k in range(ll + 1, j) if lo[k + 1] > h[k - 1]]
                            if gaps:
                                k = gaps[-1]
                                gtop, gbot = lo[k + 1], h[k - 1]
                                i0 = int(np.searchsorted(m5["t"], t[j] + LS))
                                i1 = int(np.searchsorted(m5["t"], t[j] + LS + PRE * LS))
                                for i in range(i0, min(i1, len(mc))):
                                    if ml[i] <= gtop:
                                        cand.append(("MSS", min(gtop, mo[i]), m5["t"][i], lo[ll:j + 1].min()))
                                        break
                                    if (m5["t"][i] + 300) % LS == 0 and mc[i] < gbot:
                                        break
                    for model, entry, t_in, lowest in cand:
                        done.add(model)
                        stop = lowest - p
                        risk = entry - stop
                        tgt = h4_target(Ht, Hh, t_in, entry)
                        if risk <= 0 or tgt is None or tgt - entry < 2 * risk or not (T0 <= t_in <= T1):
                            continue
                        res = exit_(m5["t"], mh, ml, mc, t_in, entry, stop, tgt, False)
                        if res is None:
                            continue
                        r = res[0] - COST[sym] * p / risk
                        trades.append(dict(sym=sym, kind=kind, model=model, dir=d, t_in=t_in, entry=entry * d, stop=stop * d,
                                           target=tgt * d, risk_pips=risk / p, rr=(tgt - entry) / risk, r=r, why=res[2]))
    return trades


def pipval(sym, price):
    return 1000 / price if sym == "USDJPY" else 10 / price if sym in ("USDCAD", "USDCHF") else 10.0


if __name__ == "__main__":
    rows = []
    for s in SYMS:
        rows += run(s)
        print("done", s, flush=True)
    df = pd.DataFrame(rows).drop_duplicates(["sym", "dir", "kind", "model", "t_in"])
    df.to_csv("D:/Devops/trading/OpenTerminalUI/reports/round22/jesse_trades.csv", index=False)
    df["usd1"] = df.r * df.risk_pips * [pipval(s, abs(e)) for s, e in zip(df.sym, df.entry)]
    print(f"\nwindow {pd.Timestamp(T0, unit='s').date()} .. {pd.Timestamp(T1, unit='s').date()}, H4 key level -> M15 entry, net of costs")
    for (kind, model), g in list(df.groupby(["kind", "model"])) + [(("ALL", "ALL"), df)]:
        tt = g.r.mean() / g.r.std() * np.sqrt(len(g)) if len(g) > 1 else 0
        u = g.usd1.sum()
        print(f"{kind:4} {model:5} {len(g):4} trades win {np.mean(g.r > 0):4.0%} {g.r.mean():+.3f}R/trade total {g.r.sum():+6.1f}R t {tt:+.2f} "
              f"${g.r.sum() * 500:+8,.0f} @$500 | 1/5/7 lots ${u:+8,.0f} / ${5 * u:+9,.0f} / ${7 * u:+9,.0f}  "
              f"median stop {g.risk_pips.median():.1f}p RR {g.rr.median():.1f}")
    print("\nper pair (all models):")
    for s, x in df.groupby("sym"):
        print(f"  {s}: {len(x)} trades {x.r.sum():+.1f}R")
