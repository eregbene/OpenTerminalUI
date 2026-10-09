"""Independent FVG / imbalance audit (research only; nothing here touches live trading).

Question: after an FVG forms and price later revisits it, does price react in the FVG's direction
more than it would from a random point in the same window?

Every FVG on M15/H1/H4/D1 is kept (no filters). Its path is read on the M15 base (FOREXSB, UTC)
whatever its timeframe, in units of the FVG timeframe's ATR at candle 1 ("R" = 1 ATR, so no
strategy stop/target is assumed). Bearish FVGs are mirrored (price -> -price) so one bullish code
path serves both directions.

Fill rules, used everywhere:
  * limit entries (A-D) fill on the first base bar trading to the level (at the open if it gaps
    past). On that bar only the adverse side counts (its high may precede the fill).
  * market entries (E confirmation, K control) fill at a known price; later bars count both sides.
  * a bar reaching both -1R and +kR scores 0.5 (order unknown) and is flagged `amb`.
  * K control = same FVG, same direction, same R, entry at the open of a random base bar inside the
    FVG's own follow-up window. This is the "random level" benchmark: expect ~50/33/25% at 1/2/3R.

python -m bensim_research.fvg_audit [--workers 3]
"""
from __future__ import annotations

import argparse
import time
import zlib
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import data as D
from . import features as F
from .backtest import COST_PIPS
from .run import ART, _low_priority

OUT = ART / "fvg_audit"
TFS = ("M15", "H1", "H4", "D1")
HTF = {"M15": "H4", "H1": "H4", "H4": "D1", "D1": "W1"}
TOUCH_H, REACT_H, CONF_H = 200, 50, 2  # own-TF bars: lifecycle window, reaction window, confirmation window
RET_N = (5, 10, 20, 50)
LEVELS = {"A": 0.0, "B": 0.25, "C": 0.5, "D": 0.75}  # entry depth into the gap
KS = (1, 2, 3)
NA = 10**9


def excursion(t: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, k0: int, entry: float, R: float, P: int, limit: bool) -> dict:
    """Long from `entry` starting at base bar k0, mirrored frame. +kR before -1R within REACT_H."""
    n = len(t)
    end = max(k0 + 1, min(n, int(np.searchsorted(t, t[k0] + REACT_H * P))))
    hh, ll = h[k0:end], l[k0:end]
    if limit:
        hh = np.r_[-np.inf, hh[1:]]
    stop = ll <= entry - R
    si = int(np.argmax(stop)) if stop.any() else NA
    out: dict = {}
    for k in KS:
        tg = hh >= entry + k * R
        ti = int(np.argmax(tg)) if tg.any() else NA
        out[f"w{k}"] = np.nan if si == ti == NA else (0.5 if si == ti else float(ti < si))
        if k == 1:
            out["amb"] = float(si == ti != NA)
    out["mfe"] = max(0.0, float(hh.max()) - entry) / R
    out["mae"] = max(0.0, entry - float(ll.min())) / R
    for m in RET_N:
        i = int(np.searchsorted(t, t[k0] + m * P))
        out[f"r{m}"] = (c[i - 1] - entry) / R if i < n else np.nan
    return out


def lifecycle(L: np.ndarray, C: np.ndarray, T: float, B: float) -> dict:
    """Mirrored (bullish) gap [B, T] followed by base lows/closes L, C. Indexes are offsets into L."""
    size = T - B
    touch = L <= T
    out = {"touch_i": NA, "q25_i": NA, "q50_i": NA, "q75_i": NA, "full_i": NA, "beyond_i": NA, "max_pen": 0.0}
    if not touch.any():
        return out
    pen = (T - np.minimum.accumulate(L)) / size
    out["touch_i"] = int(np.argmax(touch))
    for q, key in ((0.25, "q25_i"), (0.5, "q50_i"), (0.75, "q75_i"), (1.0, "full_i")):
        m = pen >= q - 1e-9
        out[key] = int(np.argmax(m)) if m.any() else NA
    out["max_pen"] = float(pen[-1])
    b = C < B
    out["beyond_i"] = int(np.argmax(b)) if b.any() else NA
    return out


def touch_starts(L: np.ndarray, T: float) -> np.ndarray:
    """Offsets of distinct touches: a bar reaching the gap after a bar that stayed fully outside it."""
    inside = L <= T
    return np.flatnonzero(inside & np.r_[True, ~inside[:-1]])


def _sd_flags(sd: pd.DataFrame, tk: np.ndarray, top: np.ndarray, bot: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    flags = []
    for zd in (1, -1):
        z = sd[sd.dir.to_numpy() == zd]
        zk, zv = z.t_known.to_numpy(), z.violated_t.to_numpy()
        zlo, zhi = np.minimum(z.distal, z.proximal).to_numpy(), np.maximum(z.distal, z.proximal).to_numpy()
        f = np.zeros(len(tk), bool)
        for r in range(len(tk)):
            live = (zk <= tk[r]) & (zv > tk[r])
            f[r] = bool((live & (zlo <= top[r]) & (zhi >= bot[r])).any())
        flags.append(f)
    return flags[0], flags[1]


def _eq_near(b: D.Bars, piv: np.ndarray, px: np.ndarray, j: np.ndarray, a: np.ndarray, top: np.ndarray, bot: np.ndarray, k: int = 2) -> np.ndarray:
    """FVG within 1 ATR of an equal-high/low pair among the last 10 confirmed pivots."""
    known = piv + k
    out = np.zeros(len(j), bool)
    for r, jj in enumerate(j):
        e = int(np.searchsorted(known, jj, side="right"))
        p = px[piv[max(0, e - 10) : e]]
        if len(p) < 2:
            continue
        diff = np.abs(p[:, None] - p[None, :]) <= 0.1 * a[jj]
        np.fill_diagonal(diff, False)
        lv = p[diff.any(1)]
        out[r] = bool(len(lv) and (np.maximum(0, np.maximum(lv - top[r], bot[r] - lv)) <= a[jj]).any())
    return out


def chained(j: np.ndarray, d: np.ndarray) -> np.ndarray:
    """True when the previous candle also completed a same-direction FVG (one multi-candle displacement run)."""
    seen = set(zip(j.tolist(), d.tolist()))
    return np.array([(jj - 1, dd) in seen for jj, dd in zip(j.tolist(), d.tolist())], dtype=bool)


def features(symbol: str, tf: str, tfs: dict, base: D.Bars) -> pd.DataFrame:
    """Every FVG on `tf` with its causal context at creation (close of candle 3)."""
    b = tfs[tf]
    a = F.atr(b)
    st = F.structure(b, 2)
    fv = F.fvgs(b, a)
    fv = fv[fv.j >= 50].reset_index(drop=True)
    j, d, tk = fv.j.to_numpy(), fv.dir.to_numpy(), fv.t_known.to_numpy()
    top, bot, ar = fv.top.to_numpy(), fv.bottom.to_numpy(), fv.atr.to_numpy()

    hb = tfs[HTF[tf]]
    sth = F.structure(hb, 3)
    erh = F.efficiency_ratio(hb.c, 20)
    hi = D.last_closed(hb, tk)
    hix = np.maximum(hi, 0)
    with np.errstate(invalid="ignore"):
        neutral = (hi < 0) | ~(erh[hix] >= 0.2)
    htf_state = np.where(neutral, 0, sth.trend[hix])
    rng = sth.sh[hix] - sth.sl[hix]
    with np.errstate(invalid="ignore", divide="ignore"):
        pdv = np.where((hi >= 0) & (rng > 0), (b.c[j] - sth.sl[hix]) / rng, np.nan)

    # liquidity: own swings (1), HTF swings (2), Asia range (2), previous day/week (3); equal levels +1
    sh, sl = F.pivot_levels(b, st, 1)
    hh, hl = F.pivot_levels(hb, sth, 2)
    dh, dl = F.period_levels(tfs["D1"], 3)
    wh, wl = F.period_levels(tfs["W1"], 3)
    ah, al = F.asia_levels(base, 2)
    liq = F.liquidity(b, sh + hh + dh + wh + ah, sl + hl + dl + wl + al, set(j.tolist()), a)
    sw_lo = pd.Series(liq.sweep_lo).rolling(9, min_periods=1).max().to_numpy()  # candle 1 - 6 .. candle 3
    sw_hi = pd.Series(liq.sweep_hi).rolling(9, min_periods=1).max().to_numpy()
    up = pd.Series(np.clip(st.brk, 0, None)).rolling(3, min_periods=1).max().to_numpy()
    dn = pd.Series(np.clip(-st.brk, 0, None)).rolling(3, min_periods=1).max().to_numpy()

    def first(snap: dict, i: int) -> float:
        v = snap.get(i, [])
        return float(v[0]) if len(v) else np.nan

    tgt = np.array([first(liq.above if dd == 1 else liq.below, jj) for jj, dd in zip(j, d)])
    behind = np.array([first(liq.below if dd == 1 else liq.above, jj) for jj, dd in zip(j, d)])

    d1, w1 = tfs["D1"], tfs["W1"]
    di, wi = np.maximum(D.last_closed(d1, tk), 0), np.maximum(D.last_closed(w1, tk), 0)

    def near(lv: np.ndarray) -> np.ndarray:
        return np.maximum(0, np.maximum(lv - top, bot - lv)) <= ar

    in_demand, in_supply = _sd_flags(F.sd_zones(hb, F.atr(hb)), tk, top, bot)
    hh20 = pd.Series(b.h).rolling(20).max().to_numpy()[j]
    ll20 = pd.Series(b.l).rolling(20).min().to_numpy()[j]
    vmed = pd.Series(b.v).rolling(20).median().shift(1).to_numpy()

    disp = fv.disp_atr.to_numpy()
    br = fv.body_ratio.to_numpy()
    return fv.assign(
        symbol=symbol, tf=tf,
        range_atr=(b.h[j - 1] - b.l[j - 1]) / ar,
        disp_class=np.where((disp >= 1.0) & (br >= 0.6), "strong", np.where(disp < 0.5, "weak", "normal")),
        vol_ratio=np.nan if symbol == "XAUUSD" else b.v[j - 1] / np.maximum(vmed[j - 1], 1),
        session=D.session_of(b.t[j - 1]),
        htf_state=htf_state,
        sweep_tier=np.where(d == 1, sw_lo[j], sw_hi[j]).astype(int),
        struct=np.where(d == 1, up[j], dn[j]).astype(int),
        dist_hi20=(hh20 - b.c[j]) / ar, dist_lo20=(b.c[j] - ll20) / ar,
        dist_target_liq=np.abs(tgt - b.c[j]) / ar, dist_behind_liq=np.abs(b.c[j] - behind) / ar,
        in_demand=in_demand, in_supply=in_supply,
        near_pd=near(d1.h[di]) | near(d1.l[di]), near_pw=near(w1.h[wi]) | near(w1.l[wi]),
        near_eq=_eq_near(b, st.ph, b.h, j, a, top, bot) | _eq_near(b, st.pl, b.l, j, a, top, bot),
        pd=pdv,
        chained=chained(j, d),
        cost_atr=COST_PIPS[symbol] * D.pip(symbol) / ar,
        year=pd.to_datetime(tk, unit="s").year,
    )


def outcomes(fv: pd.DataFrame, b: D.Bars, base: D.Bars, rng: np.random.Generator) -> pd.DataFrame:
    """Lifecycle + entry-model reactions for every FVG (forward-looking labels, research only)."""
    P = D.TF_SECONDS[b.tf]
    t = base.t
    mirror = {1: (base.o, base.h, base.l, base.c), -1: (-base.o, -base.l, -base.h, -base.c)}
    own = {1: b.l, -1: -b.h}
    rows = []
    for j, d, tk, top, bot, R in fv[["j", "dir", "t_known", "top", "bottom", "atr"]].itertuples(index=False):
        O, H, L, C = mirror[d]
        T, B = (top, bot) if d == 1 else (-bot, -top)
        size = T - B
        s = int(np.searchsorted(t, tk))
        e_own = j + 1 + TOUCH_H  # same TOUCH_H own-TF bars as the distinct-touch count (weekends excluded)
        e = int(np.searchsorted(t, b.t[e_own])) if e_own < len(b) else len(t)
        row: dict = {}
        if e - s < 2:
            rows.append(row)
            continue
        lc = lifecycle(L[s:e], C[s:e], T, B)
        for key in ("touch", "q25", "q50", "q75", "full", "beyond"):
            i = lc[f"{key}_i"]
            row[key] = i != NA
            row[f"{key}_bars"] = int(np.searchsorted(b.t, t[s + i], side="right")) - 1 - j if i != NA else np.nan  # own-TF bars after candle 3
        row["max_pen"] = lc["max_pen"]
        ojb = own[d][j + 1 : j + 1 + TOUCH_H]
        starts = touch_starts(ojb, T)
        row["n_touches"] = len(starts)

        def put(prefix: str, res: dict) -> None:
            for k, v in res.items():
                row[f"{prefix}_{k}"] = v

        for name, q in LEVELS.items():
            m = L[s:e] <= T - q * size
            if not m.any():
                continue
            k = s + int(np.argmax(m))
            entry = min(O[k], T - q * size)
            row[f"{name}_fill"] = True
            put(name, excursion(t, H, L, C, k, entry, R, P, limit=True))
        if lc["touch_i"] != NA:  # E: close back above the gap within CONF_H own bars of the touch
            k_t = s + lc["touch_i"]
            stop_at = int(np.searchsorted(t, t[k_t] + CONF_H * P))
            for k in range(k_t, min(stop_at, len(t) - 1)):
                if C[k] < B:
                    break
                if C[k] > T:
                    row["E_fill"] = True
                    put("E", excursion(t, H, L, C, k + 1, C[k], R, P, limit=False))
                    break
        for n_t, oi in ((2, 1), (3, 2)):  # second/third distinct touch (own TF), model A
            if len(starts) > oi:
                k0 = int(np.searchsorted(t, b.t[j + 1 + starts[oi]]))
                seg = L[k0 : int(np.searchsorted(t, b.t[j + 1 + starts[oi]] + P))]
                if len(seg) and (seg <= T).any():
                    k = k0 + int(np.argmax(seg <= T))
                    row[f"T{n_t}_prior_pen"] = float((T - L[s:k].min()) / size)
                    put(f"T{n_t}", excursion(t, H, L, C, k, min(O[k], T), R, P, limit=True))
        k = int(rng.integers(s, e - 1))  # K: random-time control in the same window
        put("K", excursion(t, H, L, C, k, O[k], R, P, limit=False))
        rows.append(row)
    return pd.concat([fv.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def run_symbol(symbol: str) -> pd.DataFrame:
    t0 = time.time()
    base = D.load(symbol, "M15")[0]
    tfs = {tf: base if tf == "M15" else D.resample(base, tf) for tf in ("M15", "H1", "H4", "D1", "W1")}
    rng = np.random.default_rng(zlib.crc32(symbol.encode()))
    out = []
    for tf in TFS:
        fv = features(symbol, tf, tfs, base)
        out.append(outcomes(fv, tfs[tf], base, rng))
        print(f"{symbol} {tf}: {len(fv)} FVGs ({time.time() - t0:.0f}s)", flush=True)
    return pd.concat(out, ignore_index=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--symbols", default=",".join(D.SYMBOLS))
    args = ap.parse_args()
    _low_priority()
    OUT.mkdir(parents=True, exist_ok=True)
    syms = args.symbols.split(",")
    with ProcessPoolExecutor(max_workers=args.workers, initializer=_low_priority) as ex:
        df = pd.concat(ex.map(run_symbol, syms), ignore_index=True)
    floats = df.select_dtypes("float64").columns
    df[floats] = df[floats].astype("float32")
    df.to_parquet(OUT / "fvgs.parquet") if _has_parquet() else df.to_csv(OUT / "fvgs.csv.gz", index=False)
    print("saved", len(df), "FVGs to", OUT)


def _has_parquet() -> bool:
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    main()
