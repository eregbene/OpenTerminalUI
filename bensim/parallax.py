"""ParallaxFX "10 Minutes/Day" strategy (r/Forex, June 2020, parts I-VIII) and the two user tests of it, on our M5 data.

    python -m bensim.parallax                       # D1 + H4, all 7 symbols, 2021-10 .. now
    python -m bensim.parallax --tf D1 --csv data/parallax_d1.csv

Rules as the author wrote them (our reading, fixed BEFORE the first run -- do not tune on the results):
  bars       NY-close aligned (17:00 New York = the FX day; H4 = 17/21/01/05/09/13 NY), so D1 = the broker daily candle.
  location   long: the indecision or the setup bar trades at/below the LOWER Bollinger band (20 SMA, 2 sd) and
             Stochastic(14,3,3) %K > %D at the setup bar with %K <= 20 in the last 3 bars ("oversold and crossing up").
             Short = mirror at the upper band (%K >= 80). Middle-band trades are optional in the posts -> left out.
  pattern    indecision bar (body <= 50% of its range, close not in the outer 10% of it) then the setup bar:
             outside or engulfing, bullish, HIGHER HIGH and HIGHER CLOSE than the indecision bar (short mirror).
  fib        drawn over the setup candle (long: low -> high). Closed below its 38.2% retrace = no trade; closed below
             23.6% = only the 38.2% order; else two half-size limit orders at the 23.6% and 38.2% retracements.
  stop       beyond the setup candle's 78.6% retrace (the 310-trade tester's choice for both legs).
  targets    extensions -61.8 / -100 / -161.8 (= high + 0.618 / 1.0 / 1.618 x candle range), each tested separately.
  orders     live from the setup bar's close; cancelled when price reaches the target first ("price has run to a
             fibonacci extension level without filling me") or after EXPIRE bars; trades closed after MAX_HOLD bars.
Variants: author (above, strict) | faithful (the Reddit tester's reading) | ideal (faithful + the author's Part VIII checklist, see setups()) | stop_entry (u/ForexBorex H1 test: buy-stop at the setup candle's high, stop at its low,
cancelled if the low breaks first) | pattern_only (author orders, no band/Stoch condition: does the location add
anything?) | any_candle (placebo: author orders on random candles, colour = direction).
Execution: M5 BID bars; buys fill/exit at ask = bid + spread, sells at bid; spread = backtest._spread (raw spread,
x4 at the 20:55-22:05 UTC rollover); a gap fills at the open; a bar touching stop and target = stop; on the fill bar
only the stop counts. Commission $7/lot round trip, charged in R. R is per leg (each leg's own entry-stop risk), as
the tester counted it; "setup R" = the legs at half risk each.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .backtest import CONTRACT, Exec, _usd_per_unit_move, load_m5
from .config import SYMBOLS
from .strategy import pip

TF_HOURS = {"D1": 24, "H12": 12, "H8": 8, "H6": 6, "H4": 4}
TARGETS = {"-61.8": 0.618, "-100": 1.0, "-161.8": 1.618}
EXPIRE, MAX_HOLD = 5, 30  # setup-timeframe bars
COMMISSION_LOT = 7.0


def ny_bars(m5: dict[str, np.ndarray], hours: int) -> pd.DataFrame:
    """M5 -> bars closing on New York 17:00 boundaries (DST aware). t_close = end of the last M5 bar inside."""
    local = pd.to_datetime(m5["t"], unit="s", utc=True).tz_convert("America/New_York").tz_localize(None).asi8 // 10**9
    key = (local + 7 * 3600) // (hours * 3600)  # 17:00 NY starts a new D1 bar
    df = pd.DataFrame({"k": key, "o": m5["open"], "h": m5["high"], "l": m5["low"], "c": m5["close"], "t": m5["t"]})
    g = df.groupby("k", sort=True)
    return pd.DataFrame({"open": g.o.first(), "high": g.h.max(), "low": g.l.min(), "close": g.c.last(),
                         "t_open": g.t.first(), "t_close": g.t.last() + 300}).reset_index(drop=True)


def indicators(b: pd.DataFrame) -> pd.DataFrame:
    c = b.close
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std(ddof=0)  # population sd, as MT4/TradingView Bollinger Bands
    ll, hh = b.low.rolling(14).min(), b.high.rolling(14).max()
    k = (100 * (c - ll) / (hh - ll).replace(0, np.nan)).rolling(3).mean()  # slow %K (14,3)
    return b.assign(ubb=mid + 2 * sd, lbb=mid - 2 * sd, k=k, d=k.rolling(3).mean())


def setups(b: pd.DataFrame, location: bool = True, body: float = 0.5, oversold: float = 20, faithful: bool = False,
           ideal: bool = False) -> pd.DataFrame:
    """Rows = setup bars (closed), with dir. location=False drops the band/Stoch condition (pattern only).
    body / oversold: our reading of "small body" and "oversold" (robustness checks only, never tuned).
    faithful=True: the reading the 145/310-trade Reddit tester's own trade list shows (2026-10-14 check): the setup
    bar only needs a higher high + higher close (no engulfing/outside requirement, as the author wrote), the
    indecision bar may close up to 5% from its extremes, and middle-band trades count (low through the 20 SMA,
    close back above it; Stoch not required -- "Often when taking trades off the MBB, Stochs WON'T go overbought").
    ideal=True: faithful + the author's own Part VIII checklist / comment answers, fixed before testing:
      * bands "sufficiently widened": BB width (ubb-lbb)/mid >= its median of the last 100 bars ("No I will not" trade
        the MBB when the bands are narrow; "Each BB can act as support/resistance")
      * middle-band trades need Stoch %K crossed in the trade direction (his "strong guideline")
      * "open sky": no confirmed Bill Williams fractal high (long) / low (short) of the last 120 bars between the setup
        candle's end and its -100 extension ("not buying directly into overhead resistance"; he suggested fractals)."""
    faithful = faithful or ideal
    o, h, lo, c = b.open, b.high, b.low, b.close
    rng = (h - lo).replace(0, np.nan)
    pos = (c - lo) / rng
    edge = 0.05 if faithful else 0.1
    indecision = ((c - o).abs() <= body * rng) & pos.between(edge, 1 - edge)
    ind = indecision.shift(1, fill_value=False)
    h1, l1, c1 = h.shift(1), lo.shift(1), c.shift(1)
    top, bot = np.maximum(o, c), np.minimum(o, c)
    outside_or_engulf = ((h > h1) & (lo < l1)) | ((top >= top.shift(1)) & (bot <= bot.shift(1)))
    shape = True if faithful else outside_or_engulf
    long_pat = ind & shape & (c > o) & (h > h1) & (c > c1)
    short_pat = ind & shape & (c < o) & (lo < l1) & (c < c1)
    if location:
        k_min, k_max = b.k.rolling(3).min(), b.k.rolling(3).max()
        outer_l = ((lo <= b.lbb) | (l1 <= b.lbb.shift(1))) & (b.k > b.d) & (k_min <= oversold)
        outer_s = ((h >= b.ubb) | (h1 >= b.ubb.shift(1))) & (b.k < b.d) & (k_max >= 100 - oversold)
        if faithful:
            mid = (b.ubb + b.lbb) / 2
            stoch_l = (b.k > b.d) if ideal else True
            stoch_s = (b.k < b.d) if ideal else True
            outer_l |= ((lo <= mid) | (l1 <= mid.shift(1))) & (c > mid) & stoch_l
            outer_s |= ((h >= mid) | (h1 >= mid.shift(1))) & (c < mid) & stoch_s
        long_pat &= outer_l
        short_pat &= outer_s
        if ideal:
            width = (b.ubb - b.lbb) / ((b.ubb + b.lbb) / 2)
            wide = width >= width.rolling(100, min_periods=50).median()
            long_pat &= wide & _open_sky(b, 1)
            short_pat &= wide & _open_sky(b, -1)
    s = b[long_pat | short_pat].copy()
    s["dir"] = np.where(long_pat[long_pat | short_pat], 1, -1)
    return s


def _open_sky(b: pd.DataFrame, d: int, lookback: int = 120) -> pd.Series:
    """True where no confirmed fractal (5-bar swing) high (d=1) / low (d=-1) of the last `lookback` bars sits between
    the bar's end (high for longs) and its -100 extension. A fractal at bar j is known from bar j+2 on."""
    x = (b.high if d == 1 else -b.low).to_numpy()
    n = len(x)
    frac = np.full(n, np.nan)
    for j in range(2, n - 2):
        if x[j] > max(x[j - 2], x[j - 1], x[j + 1], x[j + 2]):
            frac[j] = x[j]
    rng = (b.high - b.low).to_numpy()
    ok = np.ones(n, bool)
    for i in range(n):
        lv = frac[max(0, i - lookback):max(0, i - 1)]  # confirmed by bar i (j <= i-2)
        lv = lv[~np.isnan(lv)]
        ok[i] = not ((lv > x[i]) & (lv <= x[i] + rng[i])).any()
    return pd.Series(ok, index=b.index)


def legs_for(s: pd.Series, variant: str) -> list[tuple[float, float, str]]:
    """(entry, stop, kind) per order for one setup bar; [] = no trade."""
    d, hi, lo, c = int(s.dir), s.high, s.low, s.close
    rng = hi - lo
    if rng <= 0:
        return []
    ext0, base = (hi, lo) if d == 1 else (lo, hi)  # candle end in trade direction / its start
    if variant == "stop_entry":
        return [(ext0, base, "stop")]
    lvl = lambda f: ext0 - d * f * rng  # noqa: E731  retracement level of the setup candle
    if (c - lvl(0.382)) * d < 0:
        return []
    fibs = (0.236, 0.382) if (c - lvl(0.236)) * d >= 0 else (0.382,)
    return [(lvl(f), lvl(0.786), "limit") for f in fibs]


@dataclass
class Path:
    """One symbol's M5 bid bars + per-bar spread (price) and commission (price distance per unit, round trip)."""
    t: np.ndarray
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    spr: np.ndarray
    comm: np.ndarray

    @classmethod
    def load(cls, sym: str, start: str, end: str | None, rollover_stress: float = 0.0) -> "Path":
        """rollover_stress > 0: ALSO multiply the spread by this much from 16:55 to 18:05 New York time (DST aware) --
        the D1 setups close at 17:00 NY, when real spreads blow out, winter and summer alike."""
        m = load_m5(sym, start, end)
        ex = Exec()
        mins = (m["t"] % 86400) // 60
        mult = np.where((mins >= 20 * 60 + 55) & (mins <= 22 * 60 + 5), ex.rollover_mult, 1.0)
        if rollover_stress:
            ny = pd.to_datetime(m["t"], unit="s", utc=True).tz_convert("America/New_York")
            ny_min = ny.hour.to_numpy() * 60 + ny.minute.to_numpy()
            mult = np.where((ny_min >= 16 * 60 + 55) & (ny_min <= 18 * 60 + 5), np.maximum(mult, rollover_stress), mult)
        spr = ex.raw_spread_pips.get(sym, 0.5) * pip(sym) * mult + ex.slippage_pips * pip(sym)
        usd = np.array([_usd_per_unit_move(sym, p) for p in m["close"]])
        comm = COMMISSION_LOT / (CONTRACT.get(sym, 100_000.0) * usd)
        return cls(m["t"], m["open"], m["high"], m["low"], m["close"], spr, comm)


def simulate(p: Path, t0: int, d: int, entry: float, stop: float, target: float, kind: str,
             expire_sec: int, hold_sec: int, cancel: float | None = None) -> tuple[float, int, int] | None:
    """Fill then exit on M5. Returns (R net of commission, fill time, exit time) or None (no fill).
    cancel: price that kills an unfilled limit (default: the target)."""
    cancel = target if cancel is None else cancel
    a = int(np.searchsorted(p.t, t0))
    b = int(np.searchsorted(p.t, t0 + expire_sec))
    if a >= b:
        return None
    o, h, lo, spr = p.o[a:b], p.h[a:b], p.lo[a:b], p.spr[a:b]
    ask_h, ask_l = h + spr, lo + spr
    if kind == "limit":  # buy limit fills when ASK trades down to entry, sell limit when BID trades up to it
        hit = (ask_l <= entry) if d == 1 else (h >= entry)
        gone = (h >= cancel) if d == 1 else (ask_l <= cancel)  # ran away (to the target / cancel level) first
    else:  # buy stop when ASK trades up to entry, sell stop when BID trades down to it
        hit = (ask_h >= entry) if d == 1 else (lo <= entry)
        gone = (lo <= stop) if d == 1 else (ask_h >= stop)  # setup broken before the breakout
    jf = int(np.argmax(hit)) if hit.any() else None
    if jf is None or (gone.any() and int(np.argmax(gone)) <= jf):
        return None
    of = (o[jf] + spr[jf]) if d == 1 else o[jf]  # the fill bar's open on the side we trade (ask for buys)
    # an open already beyond the order fills at the open: better for a limit, worse for a stop
    fill = (min if (d == 1) == (kind == "limit") else max)(entry, of)
    risk = (fill - stop) * d
    if risk <= 0:
        return None
    k = a + jf
    e = int(np.searchsorted(p.t, p.t[k] + hold_sec))
    o2, h2, l2, s2 = p.o[k:e], p.h[k:e], p.lo[k:e], p.spr[k:e]
    if d == 1:  # long exits on the bid
        st, tp, ost = l2 <= stop, h2 >= target, o2
    else:  # short exits on the ask
        st, tp, ost = h2 + s2 >= stop, l2 + s2 <= target, o2 + s2
    tp[0] = False  # on the fill bar only the stop counts (order inside the bar unknown)
    js = int(np.argmax(st)) if st.any() else None
    jt = int(np.argmax(tp)) if tp.any() else None
    if js is not None and (jt is None or js <= jt):
        px, j = (min(stop, ost[js]) if d == 1 else max(stop, ost[js])) if js > 0 else stop, js
    elif jt is not None:
        px, j = (max(target, ost[jt]) if d == 1 else min(target, ost[jt])), jt
    else:
        j = len(o2) - 1
        px = p.c[k + j] if d == 1 else p.c[k + j] + s2[j]
    r = (px - fill) * d / risk - p.comm[k] / risk
    return float(r), int(p.t[k]), int(p.t[k + j]) + 300


def fxcm_path(sym: str, start: str, end: str | None) -> Path:
    """FXCM H1 bid bars with their REAL ask-bid spread per bar (rollover included); no extra commission: that spread
    is a retail spread, wider than raw + commission, so this is the harsher cost model."""
    from .fxcm_history import load
    m = load(sym)
    lo_t = pd.Timestamp(start, tz="UTC").timestamp()
    hi_t = pd.Timestamp(end, tz="UTC").timestamp() if end else np.inf
    k = (m["t"] >= lo_t) & (m["t"] < hi_t)
    return Path(m["t"][k], m["open"][k], m["high"][k], m["low"][k], m["close"][k], m["spr"][k] + 0.1 * pip(sym), np.zeros(k.sum()))


def run_symbol(sym: str, tfs: list[str], start: str, end: str | None, seed: int = 0, rollover_stress: float = 0.0,
               source: str = "db") -> pd.DataFrame:
    p = fxcm_path(sym, start, end) if source == "fxcm" else Path.load(sym, start, end, rollover_stress)
    m5 = {"t": p.t, "open": p.o, "high": p.h, "low": p.lo, "close": p.c}
    rows = []
    rng = np.random.default_rng(seed)
    for tf in tfs:
        sec = TF_HOURS[tf] * 3600
        b = indicators(ny_bars(m5, TF_HOURS[tf]))
        b = b[b.t_open >= p.t[0] + 30 * sec].reset_index(drop=True)  # indicator warm-up
        full = setups(b, location=True)
        cand = b[(b.close != b.open)].sample(n=min(len(b) - 1, 5 * max(len(full), 1)), random_state=int(rng.integers(1e9)))
        groups = {"author": full, "faithful": setups(b, faithful=True), "ideal": setups(b, ideal=True), "stop_entry": full, "pattern_only": setups(b, location=False),
                  "any_candle": cand.assign(dir=np.sign(cand.close - cand.open).astype(int))}
        for variant, s_df in groups.items():
            for _, s in s_df.iterrows():
                for tname, ext in TARGETS.items():
                    target = (s.high if s.dir == 1 else s.low) + s.dir * ext * (s.high - s.low)
                    # faithful: the tester deleted unfilled orders once price reached the -38.2 extension
                    cancel = (s.high if s.dir == 1 else s.low) + s.dir * 0.382 * (s.high - s.low) if variant in ("faithful", "ideal") else None
                    for leg, (entry, stop, kind) in enumerate(legs_for(s, variant)):
                        res = simulate(p, int(s.t_close), int(s.dir), entry, stop, target, kind, EXPIRE * sec, MAX_HOLD * sec, cancel)
                        if res is not None:
                            rows.append((sym, tf, variant, tname, int(s.t_close), leg, int(s.dir), *res))
    return pd.DataFrame(rows, columns=["symbol", "tf", "variant", "target", "t_setup", "leg", "dir", "r", "t_fill", "t_exit"])


def report(t: pd.DataFrame) -> None:
    t = t.assign(year=pd.to_datetime(t.t_fill, unit="s").dt.year)
    print(f"{'tf':3} {'variant':12} {'target':7} {'legs':>5} {'setups':>6} {'win%':>5} {'avgR':>7} {'PF':>5} {'totalR':>7} "
          f"{'maxDD':>6} {'t':>5}  years+  per year avg R")
    for (tf, v, tg), g in t.groupby(["tf", "variant", "target"], sort=False):
        g = g.sort_values("t_fill")
        r = g.r.to_numpy()
        eq = np.cumsum(r)
        pf = r[r > 0].sum() / max(-r[r < 0].sum(), 1e-9)
        ys = g.groupby("year").r.mean()
        print(f"{tf:3} {v:12} {tg:7} {len(r):5} {g.t_setup.nunique():6} {np.mean(r > 0) * 100:5.1f} {r.mean():+7.3f} {pf:5.2f} "
              f"{r.sum():+7.1f} {(np.maximum.accumulate(eq) - eq).max():6.1f} {r.mean() / r.std() * np.sqrt(len(r)):+5.2f}  "
              f"{(ys > 0).sum()}/{len(ys)}   " + " ".join(f"{y % 100}:{x:+.2f}" for y, x in ys.items()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", default="D1,H4")
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--start", default="2021-10-04")
    ap.add_argument("--end", default=None)
    ap.add_argument("--csv", default="data/parallax_5y.csv")
    ap.add_argument("--rollover-stress", type=float, default=0.0, help="spread x this at 16:55-18:05 New York")
    ap.add_argument("--source", choices=["db", "fxcm"], default="db", help="fxcm = data/bars/fxcm H1 (python -m bensim.fxcm_history)")
    a = ap.parse_args()
    t = pd.concat([run_symbol(s, a.tf.split(","), a.start, a.end, rollover_stress=a.rollover_stress, source=a.source) for s in a.symbols.split(",")], ignore_index=True)
    t.to_csv(a.csv, index=False)
    print(f"{len(t)} legs -> {a.csv}   (EXPIRE {EXPIRE}, MAX_HOLD {MAX_HOLD} bars; costs: spread x4 at rollover + $7/lot)")
    print("manual daily test (310 entries, 28 pairs, 2017-19): -100 target +0.72R/entry PF 2.34 win 46%; -61.8 +0.56R win 53%\n")
    report(t)


if __name__ == "__main__":
    main()
