"""Setup database for the Bennett engine: EVERY candidate setup in a period, unfiltered, with what was known at entry
(which of his rules it passes) and what happened after (R for several stop / target choices, costs included).
Rules are then judged on the table -- the setups a rule keeps vs the ones it throws away -- instead of re-running
backtests. A candidate = a 1H (or 4H) leg, its relief reached the OTE, then a 5M CHoCH our way (the engine's own
plan() with every optional rule switched off). No lookahead in the features: each comes from plan() at that M5 close.

    python -m bensim.setups --start 2021-10-15 --end 2026-10-12 --symbols EURUSD,GBPUSD --workers 6 \\
                            --out data/setups_bennett_h1.csv [--leg-tf H4]
"""
from __future__ import annotations

import argparse
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

import numpy as np
import pandas as pd

from . import bennett_strategy as bs
from .backtest import Exec, _news_times, _simulate, _spread, _win, load_m5, resample
from .strategy import pip

# every optional rule OFF: the widest net that is still "his setup" (leg -> OTE -> 5M CHoCH our way)
BASE = dict(need_fvg=False, choch_plus=False, top_in_ote=False, after_fail="off", expire_h=1e9, min_rr=0.01,
            max_targets=20, bias="off", stop_mode="tight", liq_eq_pips=0.0, premium_entry=False, took_internal=False,
            clear_path=False, max_rr=1e9)
# each rule = BASE + that one rule on; flag = does the setup still trigger with it (the engine's exact semantics)
RULES = {"fvg": {"need_fvg": True}, "choch_plus": {"choch_plus": True}, "top_in_ote": {"top_in_ote": True},
         "fresh48": {"expire_h": 48.0}, "fresh24": {"expire_h": 24.0}, "fresh12": {"expire_h": 12.0},
         "after_fail": {"after_fail": "1h"}, "premium": {"premium_entry": True},
         "took_internal": {"took_internal": True}, "clear_path": {"clear_path": True},
         "bias_h4": {"bias": "H4"}, "bias_d1": {"bias": "D1"}, "bias_w1d1": {"bias": "W1D1"}}
R_GRID = (1.0, 2.0, 3.0, 4.0, 5.0)


def _path_r(m5: dict, k: int, d: int, entry: float, stop: float, hold: int) -> dict:
    """Gross R outcomes along the path after bar k: fixed-R targets (stop first on a same-bar tie), MFE/MAE."""
    risk = (entry - stop) * d
    hi, lo = m5["high"][k + 1:k + 1 + hold], m5["low"][k + 1:k + 1 + hold]
    fav = ((hi - entry) if d == 1 else (entry - lo)) / risk
    adv = ((entry - lo) if d == 1 else (hi - entry)) / risk
    hit_stop = np.flatnonzero(adv >= 1.0)
    s_i = int(hit_stop[0]) if len(hit_stop) else len(fav)
    out = {"mfe_r": float(fav[:s_i + 1].max()) if len(fav) else 0.0, "mae_r": float(adv.max()) if len(adv) else 0.0,
           "bars_to_stop": s_i if s_i < len(fav) else -1}
    last = float(((m5["close"][min(k + hold, len(m5["close"]) - 1)] - entry) * d) / risk)
    for g in R_GRID:
        hit = np.flatnonzero(fav >= g)
        t_i = int(hit[0]) if len(hit) else len(fav)
        out[f"r_fix{g:g}"] = g if t_i < s_i else (-1.0 if s_i < len(fav) else max(-1.0, min(g, last)))
    return out


def scan(args: tuple) -> pd.DataFrame:
    sym, start, end, leg_tf = args
    p0 = bs.BennettParams(**{**BASE, "leg_tf": leg_tf})
    variants = {k: replace(p0, **v) for k, v in RULES.items()}
    p_wide = replace(p0, stop_mode="auto")  # min_rr ~0 -> the conservative stop always pays -> the wide stop
    p_liq = replace(p0, liq_eq_pips=1.0)
    p_275 = {"tight": replace(p0, min_rr=2.75, max_targets=2), "wide": replace(p0, min_rr=2.75, max_targets=2, stop_mode="auto")}
    ex = Exec(news_block_min=0)
    warm = (p0.htf_bars + 100) * 3600
    m5 = load_m5(sym, (pd.Timestamp(start, tz="UTC") - pd.Timedelta(seconds=warm)).isoformat(), end)
    if not len(m5["t"]):
        return pd.DataFrame()
    m15, h1 = resample(m5, 900), resample(m5, 3600)
    news = _news_times(sym)
    t0 = int(pd.Timestamp(start, tz="UTC").timestamp())
    rows, t_start = [], time.time()

    def plan_at(k: int, p: bs.BennettParams) -> dict | None:
        now = int(m5["t"][k]) + 300
        i15 = int(np.searchsorted(m15["t"] + 900, now, side="right"))
        i1 = int(np.searchsorted(h1["t"] + 3600, now, side="right"))
        pl = bs.plan(_win(m15, i15, 300), _win(h1, i1, 200), _win(m5, k + 1, p.m5_bars), sym, p, htf=_win(h1, i1, p.htf_bars))
        return pl if pl and pl["status"] == "TRIGGERED" else None

    for k in np.flatnonzero(m5["t"] >= t0):
        if k < 100:
            continue
        pl = plan_at(int(k), p0)
        if pl is None:
            continue
        now = int(m5["t"][k]) + 300
        d, c = pl["dir"], pl["checks"]
        bid = float(m5["close"][k])
        spr = _spread(sym, now, ex)
        entry = (bid + spr) if d == 1 else bid
        row = {"symbol": sym, "t": now, "dir": d, "entry": entry, "hour": pd.Timestamp(now - 300, unit="s").hour,
               "dow": pd.Timestamp(now - 300, unit="s").dayofweek, "leg": c["leg"], "fib_pos": c["fib_pos"],
               "hours_since_touch": (now - c["t_touch"]) / 3600, "hours_since_push": (now - c["t_push"]) / 3600,
               "spread_pips": spr / pip(sym), "atr_m5_pips": pl["atr"] / pip(sym), "choch_plus": bool(c["choch_plus"]),
               "news_120": bool(len(news) and np.any(np.abs(news - now) <= 120 * 60)), "hold_bars": pl["hold_bars"]}
        for name, pv in variants.items():  # which of his rules this setup passes
            row[f"ok_{name}"] = plan_at(int(k), pv) is not None
        for sname, pv in (("tight", p0), ("wide", p_wide), ("liq", p_liq)):
            pp = pl if sname == "tight" else plan_at(int(k), pv)
            if pp is None:
                continue
            stop = pp["stop"]
            row[f"{sname}_stop_pips"] = abs(entry - stop) / pip(sym)
            for kk, vv in _path_r(m5, int(k), d, entry, stop, int(pl["hold_bars"])).items():
                row[f"{sname}_{kk}"] = vv
        for sname, pv in p_275.items():  # the engine's real exit: first liquidity paying >= 2.75R, costs included
            pp = plan_at(int(k), pv)
            row[f"{sname}_liq275_ok"] = pp is not None
            if pp is None:
                continue
            order, _ = bs.order_for(pp, bid, bid + spr, pv)
            if order is None:
                row[f"{sname}_liq275_ok"] = False
                continue
            tr = _simulate(sym, m5, int(k), order, pv, ex, spr)
            row[f"{sname}_liq275_r"] = tr["r"]
            row[f"{sname}_liq275_rr"] = round(order["rr"], 2)
            row[f"{sname}_liq275_exit"] = tr["exit_reason"]
            row[f"{sname}_cost_r"] = tr["r_gross"] - tr["r"]
        rows.append(row)
    print(f"{sym}: {len(rows)} setups in {time.time() - t_start:.0f}s", flush=True)
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--symbols", default="EURUSD,GBPUSD,USDJPY,AUDUSD,USDCHF,USDCAD")
    ap.add_argument("--leg-tf", default="H1")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    syms = [s for s in a.symbols.split(",") if s]
    with ProcessPoolExecutor(a.workers) as pool:
        parts = list(pool.map(scan, [(s, a.start, a.end, a.leg_tf) for s in syms]))
    df = pd.concat([x for x in parts if len(x)], ignore_index=True).sort_values("t")
    df.to_csv(a.out, index=False)
    print(f"{len(df)} setups -> {a.out}")


if __name__ == "__main__":
    main()
