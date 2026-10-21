"""Maurice Kenny supply & demand -> 0DTE / nearest-expiry ATM options. Rules: docs/maurice_kenny_options.md.

    python -m optsim.mkenny                      # all tickers, 2021-08 .. now (5y) + last 3y, report
    python -m optsim.mkenny --day QQQ 2026-10-20 # print one day's zones, candles, trades (to eyeball vs charts)

Underlying first (stock-level R: is the setup itself any good?), then the option on top (what he actually trades).
All rule choices are fixed in the doc BEFORE any run -- no tuning on these results.
"""
from __future__ import annotations

import math
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1] / "data" / "options"
TICKERS = ["SPY", "QQQ", "TSLA", "NVDA", "AAPL", "AMZN", "MSFT", "AMD", "NFLX"]
ET = "America/New_York"


@dataclass(frozen=True)
class P:
    zone_atr: float = 0.30      # zone width = 0.3 x ATR14(5m)  (10 cents on 2020 SPY)
    push_atr: float = 1.0       # zone needs a push away >= 1 ATR within 3 bars
    start: str = "10:30"        # 1 h after the open
    last_entry: str = "15:55"
    power_hour: str = "15:00"   # from here: enter on the confirmation candle (3rd) -- Dec 7 2020
    flat: str = "15:58"
    max_trades: int = 2
    touch_tol: float = 0.10     # x width: "1-2 cents short still counts"
    trend: str = "day"           # day = no trades against a trending day (VWAP slope over 60 min + price side);
                                # vwap = strict: calls only above VWAP / puts only below; off = no filter
    trend_atr: float = 0.5      # 'trending' = VWAP moved > 0.5 x ATR(5m) over the last 60 minutes
    stop_edge: str = "far"      # far = wiggle room (default), near = conservative
    flips: bool = True          # broken zone flips side and can be traded once (his "hard" days Dec 9 / Dec 15)
    budget: float = 1000.0      # $ premium per trade
    fee: float = 0.65           # $ per contract per side


@dataclass
class Zone:
    kind: int          # +1 demand (calls), -1 supply (puts)
    top: float
    bot: float
    t: int             # available from (UTC s)
    prior_day: bool = False
    flipped: bool = False
    used: bool = False
    dead: bool = False


# ---------------------------------------------------------------- data
def load(t: str) -> pd.DataFrame:
    df = pd.read_csv(ROOT / "m1" / f"{t}.csv.gz")
    ts = pd.to_datetime(df.t, unit="s", utc=True).dt.tz_convert(ET)
    df["et"] = ts
    df["d"] = ts.dt.date
    hm = ts.dt.hour * 60 + ts.dt.minute
    df = df[(ts.dt.dayofweek < 5) & (hm >= 570) & (hm < 960)].reset_index(drop=True)  # 09:30-16:00 ET
    df["hm"] = df.et.dt.hour * 60 + df.et.dt.minute
    return df


def iv_series(t: str) -> pd.Series:
    f = ROOT / "iv" / f"{t}.csv.gz"
    if not f.exists():
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    s = pd.read_csv(f, parse_dates=["date"]).set_index("date").iv.sort_index()
    return s[s > 0]


def hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


# ---------------------------------------------------------------- option model
def bs(S: float, K: float, T: float, vol: float, d: int) -> float:
    if T <= 0:
        return max(0.0, (S - K) * d)
    sq = vol * math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * vol * vol * T) / sq
    N = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))  # noqa: E731
    return S * N(d1) - K * N(d1 - sq) if d == 1 else K * N(sq - d1) - S * N(-d1)


def strike_step(t: str, px: float) -> float:
    if t in ("SPY", "QQQ"):
        return 1.0
    return 1.0 if px < 150 else 2.5 if px < 500 else 5.0


def expiry_days(t: str, day: pd.Timestamp) -> int:
    """Trading days from `day` to the nearest listed expiry (0 = same day)."""
    wd = day.weekday()
    if t in ("SPY", "QQQ"):
        daily = pd.Timestamp("2022-11-14") if t == "SPY" else pd.Timestamp("2023-01-03")
        if day >= daily:
            return 0
        return 0 if wd in (0, 2, 4) else 1  # Mon / Wed / Fri expiries before daily listings
    return 4 - wd  # Friday weeklies


def option_trade(t: str, d: int, S0: float, S1: float, min0: int, min1: int, day, vol: float, spread: float, p: P):
    """Buy ATM at the ask at S0 (minute-of-day ET min0), sell at the bid at S1 (min1). Returns (pnl $, return)."""
    step = strike_step(t, S0)
    K = round(S0 / step) * step
    dte = expiry_days(t, pd.Timestamp(day))
    yr = 252 * 390
    T0 = (dte * 390 + (960 - min0)) / yr
    T1 = (dte * 390 + (960 - min1)) / yr
    m0, m1 = bs(S0, K, T0, vol, d), bs(S1, K, T1, vol, d)
    ask = max(0.01, m0 * (1 + spread / 2))
    bid = max(0.0, m1 * (1 - spread / 2))
    n = max(1, int(p.budget // (ask * 100)))
    cost = ask * 100 * n + p.fee * n
    pnl = bid * 100 * n - p.fee * n - cost
    return pnl, pnl / cost, ask


# ---------------------------------------------------------------- zones (5-minute chart)
def five_min(day: pd.DataFrame) -> pd.DataFrame:
    g = day.groupby(day.hm // 5)
    return pd.DataFrame({"t": g.t.first() + 300, "open": g.open.first(), "high": g.high.max(), "low": g.low.min(),
                         "close": g.close.last()}).reset_index(drop=True)  # t = bar close (UTC s)


def atr(m5: pd.DataFrame, n: int = 14) -> np.ndarray:
    pc = m5.close.shift(1).fillna(m5.open)
    tr = np.maximum(m5.high - m5.low, np.maximum((m5.high - pc).abs(), (m5.low - pc).abs()))
    return tr.rolling(n, min_periods=3).mean().to_numpy()


def day_zones(m5: pd.DataFrame, a: np.ndarray, p: P) -> list[Zone]:
    """Swing high/low (2 bars each side) followed by a push away >= push_atr x ATR within 3 bars."""
    h, lo, out = m5.high.to_numpy(), m5.low.to_numpy(), []
    for j in range(2, len(m5) - 3):
        if not np.isfinite(a[j]):
            continue
        w = p.zone_atr * a[j]
        if h[j] > h[j - 2:j].max() and h[j] >= h[j + 1:j + 3].max() and lo[j + 1:j + 4].min() <= h[j] - p.push_atr * a[j]:
            out.append(Zone(-1, h[j], h[j] - w, int(m5.t[j + 3])))
        if lo[j] < lo[j - 2:j].min() and lo[j] <= lo[j + 1:j + 3].min() and h[j + 1:j + 4].max() >= lo[j] + p.push_atr * a[j]:
            out.append(Zone(1, lo[j] + w, lo[j], int(m5.t[j + 3])))
    return out


# ---------------------------------------------------------------- one day
def run_day(t: str, day: pd.DataFrame, prev: pd.DataFrame | None, a_prev: float, p: P) -> list[dict]:
    m5 = five_min(day)
    a = atr(m5)
    if np.isfinite(a_prev):
        a = np.where(np.isfinite(a), a, a_prev)
    zones = day_zones(m5, a, p)
    w0 = p.zone_atr * (a_prev if np.isfinite(a_prev) else np.nanmean(a))
    if prev is not None and np.isfinite(w0):  # multi-day zones: prior day's high / low
        t0 = int(day.t.iloc[0])
        zones += [Zone(-1, prev.high.max(), prev.high.max() - w0, t0, True), Zone(1, prev.low.min() + w0, prev.low.min(), t0, True)]
    o, h, lo, c, tm, mins = (day[k].to_numpy() for k in ("open", "high", "low", "close", "t", "hm"))
    vol = np.maximum(day.tick_volume.to_numpy(float), 1.0)
    vwap = np.cumsum((h + lo + c) / 3 * vol) / np.cumsum(vol)
    trades, state, z_act, R, C = [], 0, None, None, None
    pos = None  # dict while in a trade
    t_start, t_last, t_flat, t_ph = hm(p.start), hm(p.last_entry), hm(p.flat), hm(p.power_hour)
    t_draw = int(tm[0]) + (hm(p.start) - int(mins[0])) * 60  # zones existing when he opens the platform (UTC s)
    for k in range(1, len(day)):
        now = int(tm[k]) + 60
        live = [z for z in zones if z.t <= now and not z.dead and not z.used]
        # zone broken: a close beyond its far edge kills it (flip trades = hard, not in the base rules)
        for z in live:
            if (z.kind == -1 and c[k] > z.top) or (z.kind == 1 and c[k] < z.bot):
                z.dead = True
                if p.flips and not z.flipped and (z.prior_day or z.t <= t_draw):  # only zones he drew at 10:30 (majors) flip
                    zones.append(Zone(-z.kind, z.top, z.bot, now, z.prior_day, flipped=True))
        if pos:
            d, z = pos["d"], pos["z"]
            stop = (z.top if p.stop_edge == "far" else z.bot) if d == -1 else (z.bot if p.stop_edge == "far" else z.top)
            against = (c[k] > o[k]) if d == -1 else (c[k] < o[k])
            pos["against"] = pos["against"] + 1 if against else 0
            why = None
            if (c[k] - stop) * d < 0:
                why = "stop"
            elif pos["tp2"] is not None and ((lo[k] <= pos["tp2"]) if d == -1 else (h[k] >= pos["tp2"])):
                why, pos["tp"] = "tp2", pos["tp2"]
            elif pos["tp1_hit"] and against:
                why = "tp1 stall"  # reached TP1 and it can't go past: sell (Ch 17 L7)
            elif pos["tp"] is not None and pos["tp2"] is None and ((lo[k] <= pos["tp"]) if d == -1 else (h[k] >= pos["tp"])):
                why = "target"
            elif pos["against"] >= 2:
                why = "2 against"
            elif mins[k] >= t_flat:
                why = "eod"
            if not why and pos["tp"] is not None and ((lo[k] <= pos["tp"]) if d == -1 else (h[k] >= pos["tp"])):
                pos["tp1_hit"] = True
            if why:
                px = pos["tp"] if why in ("target", "tp2") else c[k]
                trades.append({**{k_: v for k_, v in pos.items() if k_ not in ("z", "against", "tp1_hit")}, "exit": px,
                               "t_exit": now, "min_exit": int(mins[k]) + 1, "why": why})
                pos = None
            continue
        if mins[k] < t_start or mins[k] > t_last or len(trades) >= p.max_trades:
            continue
        # major zones = the day's extremes among live zones (+ prior-day levels)
        sup = [z for z in live if z.kind == -1 and not z.prior_day and not z.flipped and z.bot > c[k - 1] - 3 * (z.top - z.bot)]
        dem = [z for z in live if z.kind == 1 and not z.prior_day and not z.flipped and z.top < c[k - 1] + 3 * (z.top - z.bot)]
        majors = ([max(sup, key=lambda z: z.top)] if sup else []) + ([min(dem, key=lambda z: z.bot)] if dem else [])             + [z for z in live if z.prior_day or z.flipped]  # the day's extremes + previous day's high/low + flipped zones
        # 4-candle state machine on the active zone
        if state == 0:
            for z in majors:
                tol = p.touch_tol * (z.top - z.bot)
                touched = (h[k - 1] >= z.bot - tol) if z.kind == -1 else (lo[k - 1] <= z.top + tol)
                rev = (c[k] < o[k]) if z.kind == -1 else (c[k] > o[k])
                if touched and rev:  # k-1 = zone candle, k = reversal candle
                    z_act, R, state = z, k, 1
                    break
            continue
        z = z_act
        if z.dead:
            state = 0
            continue
        d = z.kind
        if state == 1:
            same = (c[k] < o[k]) if d == -1 else (c[k] > o[k])
            beyond = (c[k] < c[R]) if d == -1 else (c[k] > c[R])
            outside = (h[k] <= z.bot) if d == -1 else (lo[k] >= z.top)  # closed AND wicked outside the zone
            if same and beyond and outside:
                C, state = k, 2
                if mins[k] >= t_ph:  # power hour: enter on the fast confirmation candle itself (at its close)
                    state = 3
            elif same:
                R = k  # side-by-side reversal candles
            else:
                state = 0  # opposite colour cancels: wait for a new reversal
            continue
        if state in (2, 3):  # entry candle: enter at its open if it opens beyond the confirmation close's side
            ph, state = state == 3, 0
            if not ph and (o[k] - c[C]) * d < -0.5 * (z.top - z.bot):
                continue  # gapped back: not "forming beyond"
            entry = c[C] if ph else o[k]
            stop = (z.top if p.stop_edge == "far" else z.bot) if d == -1 else (z.bot if p.stop_edge == "far" else z.top)
            risk = (entry - stop) * d
            if risk <= 0:
                continue
            if p.trend == "vwap" and (entry - vwap[k - 1]) * d < 0:
                continue
            if p.trend == "day" and k > 60:
                slope = vwap[k - 1] - vwap[k - 61]
                a_now = a[min(len(a) - 1, (int(mins[k]) - 570) // 5)]
                if abs(slope) > p.trend_atr * a_now and slope * d < 0 and (c[k - 1] - vwap[k - 1]) * d < 0:
                    continue  # don't fight a trending day ("why would you buy a call if it's trending down")
            # TP1 = nearest minor zone or VWAP in the trade direction
            cands = [zz.top for zz in zones if zz.kind == 1 and zz.t <= now and zz is not z and zz.top < entry] if d == -1 else \
                    [zz.bot for zz in zones if zz.kind == -1 and zz.t <= now and zz is not z and zz.bot > entry]
            if (vwap[k - 1] - entry) * d > 0:
                cands.append(vwap[k - 1])
            cands = sorted(cands, reverse=(d == -1))  # nearest first in the trade direction
            tp, tp2 = (cands[0] if cands else None), (cands[1] if len(cands) > 1 else None)
            z.used = True
            pos = {"ticker": t, "day": str(day.d.iloc[0]), "d": d, "z": z, "entry": entry, "risk": risk, "tp": tp,
                   "t_entry": int(tm[k]), "min_entry": int(mins[k]), "prior_day_zone": z.prior_day, "flip": z.flipped,
                   "tp2": tp2, "tp1_hit": False, "against": 0}
    return trades


def run_ticker(args) -> pd.DataFrame:
    t, start, p = args
    df = load(t)
    df = df[df.d >= pd.Timestamp(start).date() - pd.Timedelta(days=5)]
    days = [g for _, g in df.groupby("d")]
    out, a_prev = [], np.nan
    for i, day in enumerate(days):
        if len(day) < 300:
            continue
        if str(day.d.iloc[0]) >= start:
            out += run_day(t, day, days[i - 1] if i else None, a_prev, p)
        aa = atr(five_min(day))
        a_prev = aa[-1] if np.isfinite(aa[-1]) else a_prev
    tr = pd.DataFrame(out)
    if tr.empty:
        return tr
    tr["r"] = (tr.exit - tr.entry) * tr.d / tr.risk
    ivs, spr = iv_series(t), spreads().get(t, 0.04)
    hv = pd.Series(np.log(df.groupby("d").close.last()).diff().rolling(20).std().to_numpy() * np.sqrt(252),
                   index=pd.to_datetime(sorted(df.d.unique())))
    res = []
    for r in tr.itertuples():
        dts = pd.Timestamp(r.day)
        h_ = hv[:dts].dropna()
        v = ivs[:dts].iloc[-1] if len(ivs[:dts]) else (h_.iloc[-1] if len(h_) else 0.35)  # IV, else realized vol
        res.append(option_trade(t, r.d, r.entry, r.exit, r.min_entry, r.min_exit, r.day, float(v), spr, p))
    tr["opt_pnl"], tr["opt_ret"], tr["ask"] = zip(*res)
    tr["iv_src"] = "iv" if len(ivs) else "hv"
    return tr


def spreads() -> dict[str, float]:
    """Median (ask-bid)/mid of real ATM near-expiry quotes per ticker (DoltHub chain sample); default 4%."""
    out = {}
    for f in (ROOT / "chain").glob("*.csv.gz"):
        q = pd.read_csv(f)
        q = q[(q.bid > 0) & (q.ask > q.bid) & ((q.bid + q.ask) / 2 > 0.3)]
        if len(q):
            out[f.name.split(".")[0]] = float(((q.ask - q.bid) / ((q.ask + q.bid) / 2)).median())
    return out


def report(tr: pd.DataFrame, label: str) -> None:
    print(f"\n{label}: {len(tr)} trades")
    rows = []
    for t, g in list(tr.groupby("ticker")) + [("ALL", tr)]:
        r, o = g.r, g.opt_ret
        tst = r.mean() / r.std() * np.sqrt(len(r)) if len(r) > 2 and r.std() > 0 else 0
        rows.append((t, len(g), (r > 0).mean(), r.mean(), tst, (o > 0).mean(), o.mean(), g.opt_pnl.sum()))
    print(f"  {'ticker':6} {'n':>5} {'stockWin':>8} {'stock R':>8} {'t':>5} | {'optWin':>6} {'opt ret':>8} {'$ ($1k/trade)':>14}")
    for t, n, w, r, ts, ow, orr, pnl in rows:
        print(f"  {t:6} {n:5} {w:8.0%} {r:+8.3f} {ts:+5.1f} | {ow:6.0%} {orr:+8.1%} {pnl:+14,.0f}")
    yr = tr.groupby(pd.to_datetime(tr.day).dt.year).agg(n=("r", "size"), R=("r", "mean"), opt=("opt_ret", "mean"), usd=("opt_pnl", "sum"))
    print("  by year:", {y: f"n{int(x.n)} R{x.R:+.2f} opt{x.opt:+.0%} ${x.usd:+,.0f}" for y, x in yr.iterrows()})
    print("  exits:", tr.why.value_counts().to_dict(), " prior-day zones:", int(tr.prior_day_zone.sum()))


def main() -> None:
    if sys.argv[1:2] == ["--day"]:
        t, d = sys.argv[2], sys.argv[3]
        df = load(t)
        days = [g for _, g in df.groupby("d")]
        i = next(i for i, g in enumerate(days) if str(g.d.iloc[0]) == d)
        a = atr(five_min(days[i - 1]))[-1]
        m5 = five_min(days[i])
        for z in day_zones(m5, np.where(np.isfinite(atr(m5)), atr(m5), a), P()):
            print("zone", "supply" if z.kind == -1 else "demand", round(z.bot, 2), "-", round(z.top, 2), "from",
                  pd.to_datetime(z.t, unit="s", utc=True).tz_convert(ET).strftime("%H:%M"))
        for x in run_day(t, days[i], days[i - 1], a, P()):
            print({k: (round(v, 3) if isinstance(v, float) else v) for k, v in x.items()})
        return
    variants = {"base": P(), "no trend filter": P(trend="off"), "strict vwap": P(trend="vwap"), "near stop": P(stop_edge="near")}
    for name, p in variants.items():
        with ProcessPoolExecutor(4) as pool:
            parts = list(pool.map(run_ticker, [(t, "2021-08-09", p) for t in TICKERS]))
        tr = pd.concat([x for x in parts if not x.empty], ignore_index=True)
        tr.to_csv(ROOT / f"mkenny_trades_{name.replace(' ', '_')}.csv.gz", index=False)
        report(tr, f"=== {name} -- 5 years (2021-08 .. now; SPY only 2026)")
        report(tr[tr.day >= "2023-10-21"], f"=== {name} -- last 3 years")


if __name__ == "__main__":
    main()
