"""Phases 10-13: event-driven trade simulation on the confirmation (base) timeframe + portfolio layer.

Execution rules (documented, conservative):
  * Nothing is evaluated before the candidate's t_known (close of FVG candle 3 on the setup TF).
  * Limit entries (models A/B) fill when the bar trades through the limit by half the spread; a
    bar opening beyond the limit fills at the open. On the fill bar only the stop is checked
    (intrabar order unknown -> assume the worst).
  * Market entries (model C) fill at the confirmation bar close + slippage; exits start next bar.
  * A bar touching both stop and target counts as a stop. Gaps through the stop fill at the open.
  * Costs: round-trip spread+commission (static per-symbol estimates reused from the BSI V3
    validation) x scenario multiplier, plus slippage on market entries and stop exits.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from . import data as D
from .strategy import Context

# spread+commission round trip (pips; XAUUSD pips of $0.10) -- scripts/bsi_v3_august_validation.COST_PIPS
COST_PIPS = {"EURUSD": 0.9, "GBPUSD": 1.2, "USDJPY": 1.0, "AUDUSD": 1.0, "USDCAD": 1.1, "USDCHF": 1.1, "XAUUSD": 3.0}
SCENARIOS = {  # spread multiplier, slippage pips per market fill / stop exit
    "optimistic": (0.75, 0.0),
    "base": (1.0, 0.2),
    "conservative": (1.5, 0.5),
}
XAU_SLIP = {"optimistic": 0.0, "base": 1.0, "conservative": 3.0}  # pips of $0.10


@dataclass(frozen=True)
class Cfg:
    entry: str = "C"  # A first touch, B 50%/CE, C confirmed
    stop: str = "auto"  # A sweep/origin, B structural swing, C FVG+ATR, D supply/demand, CONF (model C), auto
    tp: str = "LIQ"  # R fixed, LIQ nearest liquidity, PARTIAL, ADAPT
    rr: float = 2.0  # fixed-R target
    min_rr: float = 1.5  # minimum reward:risk for liquidity targets
    be: bool = False  # stop to break-even at +1R
    trail: str = "none"  # none, atr, struct
    scenario: str = "base"
    confirm_sweep: bool = True  # model C requires a confirmation-TF sweep


def _costs(symbol: str, scenario: str) -> tuple[float, float]:
    mult, slip = SCENARIOS[scenario]
    p = D.pip(symbol)
    slip_pips = XAU_SLIP[scenario] if symbol == "XAUUSD" else slip
    return COST_PIPS[symbol] * mult * p, slip_pips * p


def simulate(ctx: Context, cands: pd.DataFrame, cfg: Cfg) -> tuple[pd.DataFrame, dict]:
    """One trade attempt per candidate. Returns (trades, no-trade reason counts)."""
    b = ctx.base
    st = ctx.stack
    n = len(b)
    rt_cost, slip = _costs(ctx.symbol, cfg.scenario)
    hs = rt_cost / 2
    set_sec = D.TF_SECONDS[st.setup]
    trades: list[dict] = []
    reasons: dict[str, int] = {}

    def reject(why: str) -> None:
        reasons[why] = reasons.get(why, 0) + 1

    for c in cands.itertuples(index=False):
        d = int(c.dir)
        top, bot = float(c.top), float(c.bottom)
        a_set = float(c.atr_setup)
        s = int(np.searchsorted(b.t, c.t_known))
        e = min(n, int(np.searchsorted(b.t, c.t_known + st.fvg_window * set_sec)))
        if s >= e:
            reject("no data after setup")
            continue
        near = top if d == 1 else bot
        # ---- first touch of the FVG (needed by every model)
        lows, highs = b.l[s:e], b.h[s:e]
        touch = (lows <= near) if d == 1 else (highs >= near)
        if not touch.any():
            reject("FVG never retraced")
            continue
        t0 = s + int(np.argmax(touch))
        conf_lo = np.nan
        if cfg.entry in ("A", "B"):
            level = near if cfg.entry == "A" else (top + bot) / 2
            fill = (lows <= level - hs) if d == 1 else (highs >= level + hs)
            if not fill.any():
                reject("limit not filled")
                continue
            eb = s + int(np.argmax(fill))
            entry = min(b.o[eb], level) if d == 1 else max(b.o[eb], level)
            market = False
        else:
            eb, entry, conf_lo = _confirm(ctx, t0, d, bot if d == 1 else top, a_set, cfg)
            if eb < 0:
                reject(entry)
                continue
            market = True
        # ---- stop
        stop_kind = cfg.stop if cfg.stop != "auto" else ("CONF" if cfg.entry == "C" else "A")
        buf = 0.1 * a_set
        if stop_kind == "A":
            ref = c.sweep_px if not np.isnan(c.sweep_px) else c.origin
        elif stop_kind == "B":
            ref = c.struct_stop
        elif stop_kind == "C":
            ref = bot if d == 1 else top
            buf = 0.25 * a_set
        elif stop_kind == "D":
            ref = c.sd_distal
        else:  # CONF
            ref = conf_lo
            buf = 0.1 * float(ctx.base_atr[eb])
        if ref is None or np.isnan(ref):
            reject(f"no reference for stop {stop_kind}")
            continue
        stop = ref - buf if d == 1 else ref + buf
        risk = (entry - stop) * d
        if risk <= max(3 * rt_cost, 0.05 * a_set):
            reject("stop too tight / beyond entry")
            continue
        if not market and ((b.o[eb] <= stop) if d == 1 else (b.o[eb] >= stop)):
            reject("gapped through stop on fill")
            continue
        # ---- targets
        levels = [x for x in (c.liq1, c.liq2, c.liq3) if not np.isnan(x) and (x - entry) * d > 0]
        tp1, tp2, part = np.nan, np.nan, 0.0
        be, trail = cfg.be, cfg.trail
        if cfg.tp == "R":
            tp1 = entry + d * cfg.rr * risk
        elif cfg.tp == "LIQ":
            if not levels:
                reject("no liquidity target")
                continue
            tp1 = levels[0]
        elif cfg.tp == "PARTIAL":
            if not levels:
                reject("no liquidity target")
                continue
            tp1 = levels[0]
            tp2 = levels[1] if len(levels) > 1 else entry + 2 * (tp1 - entry)
            part, be = 0.5, True
        elif cfg.tp == "ADAPT":
            strong = (c.regime == ("TREND_UP" if d == 1 else "TREND_DOWN")) and c.disp_atr >= 1.5
            if strong:
                tp1 = levels[1] if len(levels) > 1 else entry + d * 3 * risk
                trail = "struct"
            elif not levels:
                reject("no liquidity target")
                continue
            else:
                tp1 = levels[0]
                be = be or c.regime == "RANGE"
        rr = (tp1 - entry) * d / risk
        if cfg.tp != "R" and rr < (1.0 if cfg.tp == "PARTIAL" else cfg.min_rr):
            reject("poor RR to liquidity")
            continue
        res = _manage(ctx, eb, d, entry, stop, tp1, tp2, part, be, trail, market, risk)
        gross = res["gross_r"]
        cost_r = (rt_cost + slip * (int(market) + int(res["stopped"]))) / risk
        trades.append({
            "symbol": ctx.symbol, "dir": d, "fvg_j": int(c.j), "t_entry": int(b.t[eb] + (D.TF_SECONDS[st.base] if market else 0)),
            "t_exit": int(res["t_exit"]), "entry": entry, "stop": stop, "target": tp1, "exit": res["exit"],
            "risk_px": risk, "planned_rr": rr, "r_gross": gross, "r": gross - cost_r, "cost_r": cost_r,
            "mfe_r": res["mfe"], "mae_r": res["mae"], "exit_reason": res["why"], "hold_bars": res["bars"],
            "entry_model": cfg.entry, "stop_model": stop_kind, "tp_model": cfg.tp,
            "score": int(c.score), "mode": c.mode, "session": c.session, "regime": c.regime, "vol": c.vol, "phase": c.phase,
            "bias_trend": int(c.bias_trend), "loc_trend": int(c.loc_trend), "sweep_tier": int(c.sweep_tier),
            "sd": bool(c.sd), "loc_fvg": bool(c.loc_fvg), "pd": float(c.pd), "size_atr": float(c.size_atr),
            "disp_atr": float(c.disp_atr), "fvg_top": top, "fvg_bottom": bot, "news_830": bool(c.news_830),
            "news_disp": bool(c.news_disp), "spread_px": rt_cost, "slip_px": slip,
            "entry_reason": f"{c.mode} FVG {st.setup} score={int(c.score)} sweep_tier={int(c.sweep_tier)}",
        })
    return pd.DataFrame(trades), reasons


def _confirm(ctx: Context, t0: int, d: int, far: float, a_set: float, cfg: Cfg):
    """Model C on the base TF after first touch t0: sweep -> structure break -> displacement -> FVG.
    Returns (entry_bar, entry_price, sweep_extreme) or (-1, reason, nan)."""
    b = ctx.base
    n = len(b)
    end = min(n, t0 + ctx.stack.confirm_window)
    swept = not cfg.confirm_sweep
    brk_at = -1
    ext = b.l[t0] if d == 1 else b.h[t0]
    for i in range(t0, end):
        ext = min(ext, b.l[i]) if d == 1 else max(ext, b.h[i])
        if (b.c[i] < far - 0.5 * a_set) if d == 1 else (b.c[i] > far + 0.5 * a_set):
            return -1, "FVG invalidated before confirmation", np.nan
        if (ctx.base_sweep_lo[i] if d == 1 else ctx.base_sweep_hi[i]) > 0:
            swept = True
        if brk_at < 0 and swept and np.sign(ctx.base_st.brk[i]) == d and i > t0:
            body = abs(b.c[i] - b.o[i]) / max(ctx.base_atr[i - 1], 1e-12)
            if body >= 0.8:
                brk_at = i
        if brk_at >= 0 and i <= brk_at + 2 and ctx.base_fvg_dir[i] == d:
            return i, float(b.c[i]), float(ext)
        if brk_at >= 0 and i > brk_at + 2:
            brk_at = -1  # displacement left no FVG; wait for another break
    return -1, ("no confirmation sweep" if not swept else "no confirmation MSS+displacement+FVG"), np.nan


def _manage(ctx: Context, eb: int, d: int, entry: float, stop: float, tp1: float, tp2: float, part: float,
            be: bool, trail: str, market: bool, risk: float) -> dict:
    b = ctx.base
    n = len(b)
    last = min(n - 1, eb + ctx.stack.max_hold)
    start = eb + 1 if market else eb
    h, l, o, c = b.h, b.l, b.o, b.c
    mfe = mae = 0.0
    realized = 0.0
    size = 1.0
    cur_stop = stop
    tp = tp1
    took_partial = False
    for i in range(start, last + 1):
        fav = (h[i] - entry) * d if d == 1 else (entry - l[i])
        adv = (entry - l[i]) if d == 1 else (h[i] - entry)
        mfe, mae = max(mfe, fav / risk), max(mae, adv / risk)
        hit_stop = (l[i] <= cur_stop) if d == 1 else (h[i] >= cur_stop)
        if hit_stop:
            px = min(o[i], cur_stop) if d == 1 else max(o[i], cur_stop)
            realized += size * (px - entry) * d / risk
            why = "stop" if cur_stop * d < entry * d else ("breakeven" if abs(cur_stop - entry) < 1e-12 else "trailing stop")
            return {"gross_r": realized, "exit": px, "t_exit": b.t[i] + D.TF_SECONDS[b.tf], "why": why,
                    "stopped": True, "mfe": mfe, "mae": mae, "bars": i - eb}
        fill_bar = i == eb and not market
        if not fill_bar and not np.isnan(tp) and ((h[i] >= tp) if d == 1 else (l[i] <= tp)):
            if part and not took_partial:
                realized += part * (tp - entry) * d / risk
                size -= part
                took_partial = True
                tp = tp2
                cur_stop = entry if be else cur_stop
            else:
                realized += size * (tp - entry) * d / risk
                return {"gross_r": realized, "exit": tp, "t_exit": b.t[i] + D.TF_SECONDS[b.tf], "why": "target",
                        "stopped": False, "mfe": mfe, "mae": mae, "bars": i - eb}
        if fill_bar:
            continue
        # stop management after the bar closes (known for the next bar)
        if be and mfe >= 1.0 and (cur_stop - entry) * d < 0:
            cur_stop = entry
        if trail != "none" and mfe >= 1.0:
            if trail == "atr":
                cand = c[i] - d * 4.0 * ctx.base_atr[i]  # ~2 setup-TF ATRs (ATR scales ~sqrt(time))
            else:
                cand = ctx.base_st.sl[i] if d == 1 else ctx.base_st.sh[i]
            if not np.isnan(cand) and (cand - cur_stop) * d > 0 and (c[i] - cand) * d > 0:
                cur_stop = cand
    realized += size * (c[last] - entry) * d / risk
    return {"gross_r": realized, "exit": c[last], "t_exit": b.t[last] + D.TF_SECONDS[b.tf], "why": "time",
            "stopped": False, "mfe": mfe, "mae": mae, "bars": last - eb}


# ---------------------------------------------------------------- portfolio / risk (phases 28-31)

CCY = {s: (s[:3], s[3:]) for s in D.SYMBOLS}


@dataclass(frozen=True)
class Risk:
    risk_pct: float = 0.5
    max_open_risk: float = 2.0
    max_ccy_exposure: float = 1.0  # % risk per currency, same direction
    max_daily_loss: float = 2.0
    max_weekly_loss: float = 4.0
    adaptive: bool = False  # NORMAL/CAUTION/REDUCED/HALTED from drawdown + volatility regime


def portfolio(trades: pd.DataFrame, risk: Risk = Risk()) -> pd.DataFrame:
    """Chronological portfolio pass: one position per symbol, open-risk / currency caps, loss limits,
    optional bounded adaptive risk. Returns accepted trades with risk_pct, pnl_pct, state."""
    if trades.empty:
        return trades.assign(risk_pct=[], pnl_pct=[], state=[])
    t = trades.sort_values(["t_entry", "symbol"]).reset_index(drop=True)
    open_pos: list[tuple[int, str, int, float]] = []  # (t_exit, symbol, dir, risk)
    equity = peak = 0.0
    pending: list[tuple[int, float]] = []  # (t_exit, pnl)
    day_pnl: dict[int, float] = {}
    week_pnl: dict[int, float] = {}
    halted_until = -1
    out = []
    for r in t.itertuples(index=False):
        now = r.t_entry
        still = []
        for p in sorted(pending):
            if p[0] <= now:  # only realised P&L counts toward loss limits / drawdown state
                equity += p[1]
                peak = max(peak, equity)
                xd, xw = p[0] // 86400, (p[0] + 4 * 86400 + 7200) // 604800
                day_pnl[xd] = day_pnl.get(xd, 0.0) + p[1]
                week_pnl[xw] = week_pnl.get(xw, 0.0) + p[1]
            else:
                still.append(p)
        pending = still
        open_pos = [p for p in open_pos if p[0] > now]
        day, week = now // 86400, (now + 4 * 86400 + 7200) // 604800
        state, rp = "NORMAL", risk.risk_pct
        if risk.adaptive:
            dd = peak - equity
            if now < halted_until:
                state = "HALTED"
            elif halted_until > 0 and dd >= 10:  # halt served: new baseline, resume at REDUCED
                peak, halted_until = equity, 0
                state, rp = "REDUCED", risk.risk_pct * 0.5
            elif dd >= 10:
                state, halted_until = "HALTED", now + 7 * 86400
            elif dd >= 6:
                state, rp = "REDUCED", risk.risk_pct * 0.5
            elif dd >= 3 or r.vol == "HIGH":
                state, rp = "CAUTION", risk.risk_pct * 0.75
        if state == "HALTED":
            continue
        if day_pnl.get(day, 0.0) <= -risk.max_daily_loss or week_pnl.get(week, 0.0) <= -risk.max_weekly_loss:
            continue
        if any(p[1] == r.symbol for p in open_pos):
            continue
        if sum(p[3] for p in open_pos) + rp > risk.max_open_risk + 1e-9:
            continue
        base, quote = CCY[r.symbol]
        expo: dict[str, float] = {}
        for _, sym, dd_, rk in open_pos:
            b_, q_ = CCY[sym]
            expo[b_] = expo.get(b_, 0) + dd_ * rk
            expo[q_] = expo.get(q_, 0) - dd_ * rk
        if abs(expo.get(base, 0) + r.dir * rp) > risk.max_ccy_exposure + 1e-9 or abs(expo.get(quote, 0) - r.dir * rp) > risk.max_ccy_exposure + 1e-9:
            continue
        pnl = r.r * rp
        open_pos.append((r.t_exit, r.symbol, r.dir, rp))
        pending.append((r.t_exit, pnl))
        out.append({**r._asdict(), "risk_pct": rp, "pnl_pct": pnl, "state": state})
    return pd.DataFrame(out)


def variant(cfg: Cfg, **kw) -> Cfg:
    return replace(cfg, **kw)
