"""Round 3, step 1: research panel = scratchpad disc_data.pkl (25 FXCM pairs H1 2012-2026-10, real spreads, net
forward outcomes, shape/place/regime features) + the features below.  CAUSAL: bar close t; a value at t uses only bars
closed at or before t.  Reused (not re-written): indicators.structure / fvgs / equal_levels / day_levels / atr_n,
fxcm_history.load.  Only their causal fields are used (formed / confirmed / level; 'mitigated'/'taken' only as
"still open at bar i" = index > i, which is decided by bars <= i).
  STRUCT  LuxAlgo structure len 10 (internal) and 50 (swing): trend, BOS/CHoCH event on this bar, bars since event
  LIQ     sweep (wick beyond, close back inside) / close-beyond of: previous day H/L, previous week H/L, today's Asian
          range (00-06 UTC, valid after 06), today's London range (07-12 UTC, valid after 12), equal highs/lows (EQH/EQL,
          pivots 3, tol 0.1 ATR200, still untaken); failed breakout = close beyond PDH/PDL within the last 3 bars and
          now back inside
  FVG     H1 gap formed this bar (dir, size/ATR24, displacement = middle-bar range/ATR24); first retest of the latest
          still-open gap of each side (bar trades into the zone for the first time)
  VOL     ATR6/ATR48, Bollinger width (20, 2sd) percentile over 500 bars, vol_ratio change over 6 bars, close beyond
          Bollinger band
  MTF     sign(close - EMA200 H1) ~ H4 EMA50, sign(close - EMA1200 H1) ~ D1 EMA50
  TIME    DST-aware sessions (Tokyo 09-18 local, London 08-17 local, New York 08-17 local), London/NY overlap, first
          London hour, first NY hour, rollover hour (17:00 New York), week open (first 3 bars), Monday/Friday,
          month-end / quarter-end last business day
  CCY     8-currency strength = mean signed ret_24 / ret_120 (bp) over the currency's pairs; rank 1 (strongest) .. 8;
          pair rank gap (quote rank - base rank: +7 = strongest vs weakest), top_bottom flag (+1 long / -1 short)
  OUT     long_48 / short_48 joined from mi2_data.pkl (same cost model)
    PYTHONPATH=D:/Devops/trading/OpenTerminalUI python r3_build.py
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bensim import indicators as ind
from bensim.fxcm_history import load

SP = "D:/ClaudeTemp/claude/14620cf7b9ae7a95/c018d285-be3f-456e-a306-37977c08ec0a/scratchpad/"
OUT = "D:/Devops/trading/OpenTerminalUI/reports/round3/r3_panel.pkl"
CCY = ["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY"]


def _sweep_close(h, lo, c, lvl_hi, lvl_lo):
    """+1 bullish sweep of a low (wick below, close above), -1 bearish sweep of a high; brk: close beyond (+1 above hi)."""
    sw = np.where((lo < lvl_lo) & (c > lvl_lo), 1, 0) + np.where((h > lvl_hi) & (c < lvl_hi), -1, 0)
    brk = np.where(c > lvl_hi, 1, 0) + np.where(c < lvl_lo, -1, 0)
    return sw.astype(np.int8), brk.astype(np.int8)


def pair_features(sym: str) -> pd.DataFrame:
    m = load(sym, "H1")
    half = m["spr"] / 2
    b = {"t": m["t"].astype(np.int64), "open": m["open"] + half, "high": m["high"] + half, "low": m["low"] + half, "close": m["close"] + half}
    h, lo, c, t = b["high"], b["low"], b["close"], b["t"]
    n = len(c)
    a200 = ind.atr_n(b, 200)
    rng = h - lo
    atr24 = pd.Series(rng).rolling(24, min_periods=12).mean().to_numpy()
    f = {"t": pd.to_datetime(t + 3600, unit="s"), "symbol": sym}
    for L in (10, 50):
        s = ind.structure(b, L, a200)
        f[f"st_tr{L}"] = s["trend"].astype(np.int8)
        f[f"st_bos{L}"] = np.where(s["kind"] == 0, s["event"], 0).astype(np.int8)
        f[f"st_choch{L}"] = np.where(s["kind"] == 1, s["event"], 0).astype(np.int8)
        last = pd.Series(np.where(s["event"] != 0, np.arange(n), np.nan)).ffill().to_numpy()
        f[f"st_age{L}"] = np.arange(n) - last
    # liquidity levels
    dl = ind.day_levels(b)
    f["sw_pd"], f["brk_pd"] = _sweep_close(h, lo, c, dl["pdh"], dl["pdl"])
    f["sw_asia"], f["brk_asia"] = _sweep_close(h, lo, c, dl["asia_hi"], dl["asia_lo"])
    wk = (t // 86400 + 3) // 7  # Monday-UTC weeks
    W = pd.DataFrame({"wk": wk, "h": h, "l": lo}).groupby("wk").agg(h=("h", "max"), l=("l", "min"))
    pwh = W.h.shift(1).reindex(wk).to_numpy()
    pwl = W.l.shift(1).reindex(wk).to_numpy()
    f["sw_pw"], f["brk_pw"] = _sweep_close(h, lo, c, pwh, pwl)
    day, hr = t // 86400, (t % 86400) // 3600
    lon = (hr >= 7) & (hr < 12)
    D = pd.DataFrame({"d": day[lon], "h": h[lon], "l": lo[lon]}).groupby("d").agg(h=("h", "max"), l=("l", "min"))
    lh = np.where(hr >= 12, D.h.reindex(day).to_numpy(), np.nan)
    ll = np.where(hr >= 12, D.l.reindex(day).to_numpy(), np.nan)
    f["sw_lon"], f["brk_lon"] = _sweep_close(h, lo, c, lh, ll)
    # failed breakout of the previous day range: a close beyond in the last 3 bars, now back inside
    bp = pd.Series(f["brk_pd"].astype(float))
    up3 = bp.shift(1).rolling(3).max().to_numpy() > 0
    dn3 = bp.shift(1).rolling(3).min().to_numpy() < 0
    f["fb_pd"] = (np.where(up3 & (c < dl["pdh"]), -1, 0) + np.where(dn3 & (c > dl["pdl"]), 1, 0)).astype(np.int8)
    # equal highs / lows still untaken -> sweep of them
    eqsw = np.zeros(n, np.int8)
    for e in ind.equal_levels(b, a200):
        j = e["taken"]  # first bar beyond the level (causal: decided at that bar)
        if j < n and e["confirmed"] < j:
            if e["dir"] == 1 and c[j] < e["level"]:
                eqsw[j] = -1  # buy-side taken, closed back below -> bearish sweep
            if e["dir"] == -1 and c[j] > e["level"]:
                eqsw[j] = 1
    f["sw_eq"] = eqsw
    # H1 FVGs (plain 3-candle gaps)
    fnew, fsize, fdisp, frt = np.zeros(n, np.int8), np.zeros(n, np.float32), np.zeros(n, np.float32), np.zeros(n, np.int8)
    latest = {1: None, -1: None}
    gaps = {g["formed"]: g for g in ind.fvgs(b, auto=False)}
    # ponytail: tracks only the latest open gap per side; nested / older gaps ignored
    for i in range(n):
        for d in (1, -1):
            g = latest[d]
            if g is not None and not g["touched"] and g["mitigated"] > i and i > g["formed"]:
                if (d == 1 and lo[i] <= g["top"]) or (d == -1 and h[i] >= g["bottom"]):
                    frt[i] = d
                    g["touched"] = True
        g = gaps.get(i)
        if g is not None:
            fnew[i] = g["dir"]
            fsize[i] = (g["top"] - g["bottom"]) / atr24[i] if atr24[i] > 0 else np.nan
            fdisp[i] = rng[i - 1] / atr24[i] if atr24[i] > 0 else np.nan
            latest[g["dir"]] = {**g, "touched": False}
    f.update(fvg_new=fnew, fvg_size=fsize, fvg_disp=fdisp, fvg_rt=frt)
    # volatility
    cs = pd.Series(c)
    f["atr6_48"] = (pd.Series(rng).rolling(6).mean() / pd.Series(rng).rolling(48).mean()).to_numpy()
    ma, sd = cs.rolling(20).mean(), cs.rolling(20).std()
    bbw = (4 * sd / ma)
    f["bbw_pct"] = bbw.rolling(500, min_periods=200).rank(pct=True).to_numpy()
    f["bb_out"] = (np.where(c > ma + 2 * sd, 1, 0) + np.where(c < ma - 2 * sd, -1, 0)).astype(np.int8)
    f["mtf_h4"] = np.sign(c - cs.ewm(span=200, adjust=False).mean().to_numpy()).astype(np.int8)
    f["mtf_d1"] = np.sign(c - cs.ewm(span=1200, adjust=False).mean().to_numpy()).astype(np.int8)
    return pd.DataFrame(f)


def time_features(t: pd.Series) -> pd.DataFrame:
    u = pd.DatetimeIndex(t.unique()).sort_values()
    tz = u.tz_localize("UTC")
    lon, ny, tk = tz.tz_convert("Europe/London"), tz.tz_convert("America/New_York"), tz.tz_convert("Asia/Tokyo")
    # t is the bar CLOSE: the bar covers [t-1h, t); session flags describe that bar (its open hour, local)
    lo_h, ny_h, tk_h = (lon - pd.Timedelta(hours=1)).hour, (ny - pd.Timedelta(hours=1)).hour, (tk - pd.Timedelta(hours=1)).hour
    month_last = pd.Series(u).groupby([u.year, u.month]).transform(lambda s: s.dt.normalize().max()).to_numpy()
    T = pd.DataFrame({"t": u,
                      "s_tokyo": ((tk_h >= 9) & (tk_h < 18)).astype(np.int8),
                      "s_london": ((lo_h >= 8) & (lo_h < 17)).astype(np.int8),
                      "s_ny": ((ny_h >= 8) & (ny_h < 17)).astype(np.int8),
                      "lon_open": (lo_h == 8).astype(np.int8), "ny_open": (ny_h == 8).astype(np.int8),
                      "rollover": (ny_h == 17).astype(np.int8)})
    T["s_overlap"] = (T.s_london & T.s_ny).astype(np.int8)
    T["month_end"] = (pd.Series(u).dt.normalize().to_numpy() == month_last).astype(np.int8)
    T["quarter_end"] = (T.month_end & np.isin(u.month, [3, 6, 9, 12])).astype(np.int8)
    T["week_open"] = ((u.dayofweek == 0) & (u.hour < 3) | (u.dayofweek == 6)).astype(np.int8)
    return T


def currency(df: pd.DataFrame) -> pd.DataFrame:
    out = {}
    for k in ("24", "120"):
        parts = []
        for cc in CCY:
            sgn = np.where(df.symbol.str[:3] == cc, 1.0, np.where(df.symbol.str[3:] == cc, -1.0, np.nan))
            parts.append(pd.Series(df[f"ret_{k}"].to_numpy() * sgn).groupby(df.t.to_numpy()).mean().rename(cc))
        S = pd.concat(parts, axis=1)
        R = S.rank(axis=1, ascending=False)  # 1 = strongest
        base = R.reindex(df.t.to_numpy()).to_numpy()
        bi = {cc: i for i, cc in enumerate(CCY)}
        rows = np.arange(len(df))
        rb = base[rows, df.symbol.str[:3].map(bi).to_numpy()]
        rq = base[rows, df.symbol.str[3:].map(bi).to_numpy()]
        out[f"rk_base_{k}"], out[f"rk_quote_{k}"] = rb.astype(np.float32), rq.astype(np.float32)
        out[f"rk_gap_{k}"] = (rq - rb).astype(np.float32)
        out[f"tb_{k}"] = (np.where((rb == 1) & (rq == 8), 1, 0) + np.where((rb == 8) & (rq == 1), -1, 0)).astype(np.int8)
    return pd.DataFrame(out, index=df.index)


def main() -> None:
    df = pd.read_pickle(SP + "disc_data.pkl")  # our own file (scratchpad disc_build.py)
    pf = pd.concat([pair_features(s) for s in sorted(df.symbol.unique())], ignore_index=True)
    df = df.merge(pf, on=["t", "symbol"], how="left")
    df = df.merge(time_features(df.t), on="t", how="left")
    df = pd.concat([df, currency(df)], axis=1)
    m2 = pd.read_pickle(SP + "mi2_data.pkl")[["t", "symbol", "long_48", "short_48"]]
    df = df.merge(m2, on=["t", "symbol"], how="left")
    for c_ in df.columns:
        if df[c_].dtype == np.float64:
            df[c_] = df[c_].astype(np.float32)
    df.to_pickle(OUT)
    print(df.shape, "rows by year", df.groupby(df.t.dt.year).size().to_dict())
    print("non-null share", df[["st_tr10", "sw_pd", "fvg_rt", "bbw_pct", "rk_gap_24", "long_48"]].notna().mean().round(3).to_dict())
    print("event rates", {k: round(float((df[k] != 0).mean()), 4) for k in ("st_bos10", "st_choch10", "sw_pd", "sw_pw", "sw_asia", "sw_lon",
                                                                            "sw_eq", "fb_pd", "fvg_new", "fvg_rt", "tb_24")})


if __name__ == "__main__":
    main()
