"""Phase 9: candidate generation. Every setup-timeframe FVG becomes a candidate carrying the full
causal context known at its creation (close of candle 3). The experiment ladder is then a set of
filters over this one pool, so every test sees identical detection logic.

Stack H (8 years, FOREXSB M15 base): bias D1, location H4, setup H1, confirmation M15, context W1.
Stack M (exact spec stack, MT5 M5, ~8-16 months): bias H4, location H1, setup M15, confirmation M5.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import data as D
from . import features as F


@dataclass(frozen=True)
class Stack:
    name: str
    base: str
    setup: str
    loc: str
    bias: str
    ctx: str
    k_base: int = 2
    k_setup: int = 2
    k_loc: int = 3
    k_bias: int = 3
    fvg_window: int = 24  # setup bars an FVG stays tradable
    confirm_window: int = 24  # base bars to find confirmation after first touch
    max_hold: int = 288  # base bars
    sweep_lookback: int = 6  # setup bars before candle 1 searched for a liquidity sweep


STACKS = {
    "H": Stack("H", base="M15", setup="H1", loc="H4", bias="D1", ctx="W1"),
    "M": Stack("M", base="M5", setup="M15", loc="H1", bias="H4", ctx="D1"),
}

# Initial, unoptimised score weights (spec section 22). Configurable; never fitted.
WEIGHTS = {
    "htf": 2, "ctx_ok": 1, "structure": 2, "sweep": 2, "sweep_ext": 1, "disp": 1, "size": 1,
    "sd": 1, "loc_fvg": 1, "pd": 1, "wyckoff": 1, "session": 1,
}


@dataclass
class Context:
    symbol: str
    stack: Stack
    base: D.Bars
    setup: D.Bars
    base_atr: np.ndarray
    base_st: F.Structure
    base_sweep_lo: np.ndarray
    base_sweep_hi: np.ndarray
    base_fvg_dir: np.ndarray  # +1/-1 if a base FVG completes at this bar
    cands: pd.DataFrame
    rejects: dict = field(default_factory=dict)
    fvg_db: dict = field(default_factory=dict)


def _overlap(lo: float, hi: float, zlo: np.ndarray, zhi: np.ndarray) -> np.ndarray:
    return (zlo <= hi) & (zhi >= lo)


def build(symbol: str, stack: Stack, *, k_setup: int | None = None, with_fvg_db: bool = False, base_bars: D.Bars | None = None) -> Context:
    base = base_bars if base_bars is not None else D.load(symbol, stack.base)[0]
    tfs = {tf: (base if tf == stack.base else D.resample(base, tf)) for tf in {stack.setup, stack.loc, stack.bias, stack.ctx, "D1", "W1"}}
    setup, loc, bias, ctx = tfs[stack.setup], tfs[stack.loc], tfs[stack.bias], tfs[stack.ctx]
    a_set, a_loc, a_bias, a_base = F.atr(setup), F.atr(loc), F.atr(bias), F.atr(base)
    st_set = F.structure(setup, k_setup or stack.k_setup)
    st_loc = F.structure(loc, stack.k_loc)
    st_bias = F.structure(bias, stack.k_bias)
    st_ctx = F.structure(ctx, stack.k_bias)
    st_base = F.structure(base, stack.k_base)
    reg = F.regime(bias, a_bias, st_bias)

    fv = F.fvgs(setup, a_set)
    fv = fv[fv.j >= 50].reset_index(drop=True)  # warm-up

    # liquidity on the setup timeframe (levels from setup/loc swings, PD/PW, Asia)
    sh, sl_ = F.pivot_levels(setup, st_set, 1)
    lh, ll = F.pivot_levels(loc, st_loc, 2)
    dh, dl = F.period_levels(tfs["D1"], 3)
    wh, wl = F.period_levels(tfs["W1"], 3)
    ah, al = F.asia_levels(base, 2)
    liq = F.liquidity(setup, sh + lh + dh + wh + ah, sl_ + ll + dl + wl + al, set(fv.j.tolist()), a_set)

    # confirmation timeframe: structure, sweeps of its own swings, its FVGs
    bh, bl = F.pivot_levels(base, st_base, 1)
    bliq = F.liquidity(base, bh, bl, set(), a_base, max_age=200)
    bf = F.fvgs(base, a_base)
    base_fvg_dir = np.zeros(len(base), np.int8)
    base_fvg_dir[bf.j.to_numpy()] = bf.dir.to_numpy()

    sd = F.sd_zones(loc, a_loc)
    loc_fv = F.fvg_lifecycle(loc, F.fvgs(loc, a_loc), horizon=400)
    inv_bars = loc_fv["invalid"].fillna(1e9).to_numpy()
    full_bars = loc_fv["full"].fillna(1e9).to_numpy()
    end_bar = np.minimum(loc_fv.j.to_numpy() + np.minimum(inv_bars, full_bars), len(loc) - 1).astype(np.int64)
    loc_fv["t_dead"] = np.where(np.minimum(inv_bars, full_bars) < 1e9, loc.known_at[end_bar], np.iinfo(np.int64).max)

    t_known = fv.t_known.to_numpy()
    j = fv.j.to_numpy()
    d = fv.dir.to_numpy()
    li = D.last_closed(loc, t_known)
    bi = D.last_closed(bias, t_known)
    ci = D.last_closed(ctx, t_known)
    n830, nfp = D.us_data_window(t_known)
    disp_news, _ = D.us_data_window(setup.t[j - 1], 15, 60)  # displacement candle printed on the 08:30 release
    sess = D.session_of(setup.t[j - 1])

    rec: dict[str, list] = {k: [] for k in (
        "sweep", "sweep_tier", "sweep_px", "origin", "struct_stop", "brk", "s_trend_prev", "sd", "sd_fresh",
        "sd_distal", "loc_fvg", "pd", "liq1", "liq2", "liq3", "room_r_proxy",
    )}
    for r in range(len(fv)):
        jj, dd = int(j[r]), int(d[r])
        a0 = max(0, jj - 2 - stack.sweep_lookback)
        window = slice(a0, jj + 1)
        sw = (liq.sweep_lo if dd == 1 else liq.sweep_hi)[window]
        tier = int(sw.max()) if len(sw) else 0
        rec["sweep"].append(tier > 0)
        rec["sweep_tier"].append(tier)
        swpx = (liq.sweep_lo_px if dd == 1 else liq.sweep_hi_px)[window]
        origin = float(setup.l[a0 : jj].min()) if dd == 1 else float(setup.h[a0 : jj].max())
        rec["sweep_px"].append(float(np.nanmin(swpx)) if dd == 1 and tier else (float(np.nanmax(swpx)) if tier else np.nan))
        rec["origin"].append(origin)
        bottom, top = fv.bottom[r], fv.top[r]
        if dd == 1:
            s = st_set.sl[jj]
            rec["struct_stop"].append(s if s < bottom else origin)
        else:
            s = st_set.sh[jj]
            rec["struct_stop"].append(s if s > top else origin)
        b = st_set.brk[jj - 2 : jj + 1]
        same = b[np.sign(b) == dd]
        rec["brk"].append(int(np.abs(same).max()) if len(same) else 0)
        rec["s_trend_prev"].append(int(st_set.trend[jj - 3]))
        # supply/demand on the location timeframe, state at t_known
        t = t_known[r]
        zl, zh = (origin, top) if dd == 1 else (bottom, origin)
        tol = 0.25 * a_loc[max(li[r], 0)]
        z = sd[(sd.dir.to_numpy() == dd) & (sd.t_known.to_numpy() <= t) & (sd.violated_t.to_numpy() > t)]
        lo_e = np.minimum(z.distal, z.proximal).to_numpy() - tol
        hi_e = np.maximum(z.distal, z.proximal).to_numpy() + tol
        hit = _overlap(zl, zh, lo_e, hi_e)
        rec["sd"].append(bool(hit.any()))
        rec["sd_fresh"].append(bool((hit & (z.first_touch_t.to_numpy() >= setup.t[a0])).any()))
        rec["sd_distal"].append(float(z.distal.to_numpy()[hit].min() if dd == 1 else z.distal.to_numpy()[hit].max()) if hit.any() else np.nan)
        lf = loc_fv[(loc_fv.dir.to_numpy() == dd) & (loc_fv.t_known.to_numpy() <= t) & (loc_fv.t_dead.to_numpy() > t)]
        rec["loc_fvg"].append(bool(_overlap(zl, zh, lf.bottom.to_numpy(), lf.top.to_numpy()).any()))
        # premium / discount in the location timeframe's external dealing range
        if li[r] >= 0:
            hi_, lo_ = st_loc.sh[li[r]], st_loc.sl[li[r]]
            px = setup.c[jj]
            rec["pd"].append((px - lo_) / (hi_ - lo_) if hi_ > lo_ else np.nan)
        else:
            rec["pd"].append(np.nan)
        levels = liq.above.get(jj, []) if dd == 1 else liq.below.get(jj, [])
        levels = list(levels) + [np.nan] * 3
        rec["liq1"].append(levels[0])
        rec["liq2"].append(levels[1])
        rec["liq3"].append(levels[2])
        ref = top if dd == 1 else bottom
        risk_proxy = abs(ref - origin) or np.nan
        rec["room_r_proxy"].append(abs(levels[0] - ref) / risk_proxy if not np.isnan(levels[0]) else np.nan)

    c = fv.assign(**rec)
    c["symbol"] = symbol
    c["t_open1"] = setup.t[j - 2]
    c["bias_trend"] = np.where(bi >= 0, st_bias.trend[np.maximum(bi, 0)], 0)
    c["ctx_trend"] = np.where(ci >= 0, st_ctx.trend[np.maximum(ci, 0)], 0)
    c["loc_trend"] = np.where(li >= 0, st_loc.trend[np.maximum(li, 0)], 0)
    for col in ("phase", "event", "regime", "vol", "wy_bias"):
        c[col] = np.where(bi >= 0, reg[col].to_numpy()[np.maximum(bi, 0)], None)
    c["session"] = sess
    c["news_830"] = n830
    c["nfp_day"] = nfp
    c["news_disp"] = disp_news
    c["atr_setup"] = c["atr"]

    # derived booleans used by the experiment ladder
    c["htf_ok"] = c.bias_trend == c.dir
    c["ctx_ok"] = c.ctx_trend != -c.dir
    c["struct_ok"] = c.brk > 0
    c["disp_ok"] = c.disp_atr >= 1.0
    c["wy_ok"] = c.wy_bias.astype(float) * c.dir >= 0
    c["wy_aligned"] = c.wy_bias.astype(float) * c.dir > 0
    c["pd_ok"] = np.where(c.dir == 1, c.pd < 0.5, c.pd > 0.5)
    c["loc_ok"] = c.sd | c.loc_fvg | c.pd_ok
    c["sess_ok"] = c.session.isin(["LONDON", "OVERLAP", "NEWYORK"])
    c["mode"] = np.where(c.brk == 2, "REVERSAL", np.where((c.brk == 1) | (c.s_trend_prev == c.dir), "CONTINUATION", "NONE"))
    w = WEIGHTS
    c["score"] = (
        w["htf"] * c.htf_ok + w["ctx_ok"] * c.ctx_ok + w["structure"] * c.struct_ok + w["sweep"] * c.sweep
        + w["sweep_ext"] * (c.sweep_tier >= 2) + w["disp"] * c.disp_ok + w["size"] * (c.size_atr >= 0.3)
        + w["sd"] * c.sd + w["loc_fvg"] * c.loc_fvg + w["pd"] * c.pd_ok + w["wyckoff"] * c.wy_aligned + w["session"] * c.sess_ok
    ).astype(int)

    ctx_obj = Context(symbol, stack, base, setup, a_base, st_base, bliq.sweep_lo, bliq.sweep_hi, base_fvg_dir, c)
    if with_fvg_db:
        ctx_obj.fvg_db = fvg_research(symbol, tfs, base, stack)
    return ctx_obj


def fvg_research(symbol: str, tfs: dict, base: D.Bars, stack: Stack) -> dict:
    """Phase 5/48: lifecycle of every FVG on each timeframe with its context (research database)."""
    out = {}
    d1 = tfs["D1"]
    st_d1 = F.structure(d1, 3)
    for tf in ("D1", "H4", "H1", stack.base if stack.base != "M5" else "M15"):
        b = tfs.get(tf) or D.resample(base, tf)
        a = F.atr(b)
        st = F.structure(b, 2)
        reg = F.regime(b, a, st)
        f = F.fvg_lifecycle(b, F.fvgs(b, a))
        f = f[f.j >= 50]
        di = D.last_closed(d1, f.t_known.to_numpy())
        f = f.assign(
            symbol=symbol, tf=tf,
            session=D.session_of(b.t[f.j.to_numpy() - 1]),
            regime=reg.regime.to_numpy()[f.j.to_numpy()],
            phase=reg.phase.to_numpy()[f.j.to_numpy()],
            htf_aligned=np.where(di >= 0, st_d1.trend[np.maximum(di, 0)], 0) == f.dir.to_numpy(),
            tf_trend_aligned=st.trend[f.j.to_numpy() - 3] == f.dir.to_numpy(),
            year=pd.to_datetime(f.t_known, unit="s").dt.year,
        )
        out[tf] = f
    return out
