"""Backtester for donchian_imbalance on broker data (`candles`, M5, UTC) -- MT5/prop-firm style.

    python -m bensim.backtest                         # all symbols, default params, 5 years
    python -m bensim.backtest --symbols EURUSD --start 2024-01-01 --news-block 30 --be-r 1.0

Exactly the live decision path: after every M5 close, bensim.strategy.plan() sees the same windows the live
engine sees (300 completed M15, 200 H1, 100 M5 bars, resampled from M5 on UTC boundaries). For speed, plan()
runs on every M15 boundary and on any M5 bar that reaches a waiting zone -- the only bars that can trigger.

Execution (MT5 bars are BID prices):
  * entry: market at the trigger bar's close -- BUY at ask (= bid + spread), SELL at bid; + slippage
  * exits on later M5 bars: long SL/TP on bid, short SL/TP on ask; gap through -> fill at the open;
    a bar reaching both SL and TP counts as SL; time exit after Params.max_hold_bars M15 bars
  * costs: spread = per-symbol raw spread x hour-of-day profile, commission $7 per lot round trip,
    slippage on market entry and stop fills
  * sizing: Params-independent risk % of equity, lots rounded down to 0.01 (min 0.01); P&L in USD
  * prop-firm check: 5% daily loss / 10% max loss on the starting balance (closed-trade basis)
Options (default off, for research): news block around high-impact events, break-even / partial at +R.
"""
from __future__ import annotations

import argparse
import ast
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import db
from .config import SYMBOLS
from . import fvg_strategy, strategy
from .strategy import pip

STRATEGIES = {"imbalance": (strategy.Params, strategy.plan, strategy.order_for),
              "htf_fvg": (fvg_strategy.FvgParams, fvg_strategy.plan, fvg_strategy.order_for)}
WARMUP_SEC = 30 * 86400  # covers the 300 M15 / 200 H1 bar windows plan() sees

CONTRACT = {"XAUUSD": 100.0}  # units per lot; FX = 100,000
CURRENCIES = {"EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"), "USDJPY": ("USD", "JPY"), "AUDUSD": ("AUD", "USD"),
              "USDCHF": ("USD", "CHF"), "USDCAD": ("USD", "CAD"), "XAUUSD": ("XAU", "USD")}


@dataclass(frozen=True)
class Exec:
    raw_spread_pips: dict[str, float] = field(default_factory=lambda: {"EURUSD": 0.1, "GBPUSD": 0.3, "USDJPY": 0.2, "AUDUSD": 0.2,
                                                                        "USDCHF": 0.3, "USDCAD": 0.3, "XAUUSD": 1.2})
    commission_per_lot_rt: float = 7.0
    slippage_pips: float = 0.1
    rollover_mult: float = 4.0  # spread multiplier 20:55-22:05 UTC (rollover), raw accounts widen there
    risk_pct: float = 0.25
    start_balance: float = 100_000.0
    news_block_min: int = 0  # 0 = off; else no entries within +/- N minutes of a high-impact event
    be_r: float = 0.0  # 0 = off; move SL to entry once price reaches +be_r R
    partial_r: float = 0.0  # 0 = off; close partial_frac at +partial_r R
    partial_frac: float = 0.5


def _spread(sym: str, t: int, ex: Exec) -> float:
    m = (t % 86400) // 60
    mult = ex.rollover_mult if 20 * 60 + 55 <= m <= 22 * 60 + 5 else 1.0
    return ex.raw_spread_pips.get(sym, 0.5) * pip(sym) * mult


def _usd_per_unit_move(sym: str, price: float) -> float:
    """USD P&L of a 1.0 price move on 1 unit."""
    return 1.0 if CURRENCIES[sym][1] == "USD" else 1.0 / price


def load_m5(sym: str, start: str | None, end: str | None) -> dict[str, np.ndarray]:
    q = "select extract(epoch from ts)::bigint, open, high, low, close from candles where symbol=%s and tf='M5'"
    args: list = [sym]
    if start:
        q += " and ts >= %s"
        args.append(start)
    if end:
        q += " and ts < %s"
        args.append(end)
    with db.connect() as c:
        a = np.array(c.execute(q + " order by ts", args).fetchall(), dtype=float)
    return {"t": a[:, 0].astype(np.int64), "open": a[:, 1], "high": a[:, 2], "low": a[:, 3], "close": a[:, 4]}


def resample(b: dict[str, np.ndarray], sec: int) -> dict[str, np.ndarray]:
    key = b["t"] // sec
    s = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    e = np.r_[s[1:], len(key)]
    return {"t": key[s] * sec, "open": b["open"][s], "high": np.maximum.reduceat(b["high"], s),
            "low": np.minimum.reduceat(b["low"], s), "close": b["close"][e - 1]}


def _win(b: dict[str, np.ndarray], z: int, n: int) -> dict[str, np.ndarray]:
    return {k: v[max(0, z - n):z] for k, v in b.items()}


def _news_times(sym: str) -> np.ndarray:
    cur = set(CURRENCIES[sym]) | {"USD"} if sym == "XAUUSD" else set(CURRENCIES[sym])
    with db.connect() as c:
        rows = c.execute("select extract(epoch from ts)::bigint from economic_events where impact='high' and currency = any(%s) and name not like '%%]' order by ts",
                         (list(cur),)).fetchall()
    return np.array([r[0] for r in rows], dtype=np.int64)


def run_symbol(args: tuple) -> pd.DataFrame:
    sym, start, end, p, ex, name = args
    _, plan, order_for = STRATEGIES[name]
    t_load = (pd.Timestamp(start, tz="UTC") - pd.Timedelta(seconds=WARMUP_SEC)).isoformat() if start else None
    m5 = load_m5(sym, t_load, end)
    if not len(m5["t"]):
        return pd.DataFrame()
    m15, h1 = resample(m5, 900), resample(m5, 3600)
    t0 = int(pd.Timestamp(start, tz="UTC").timestamp()) if start else int(m5["t"][0])
    news = _news_times(sym) if ex.news_block_min else np.zeros(0, np.int64)
    trades, open_until, waiting, last15 = [], -1, [], -1
    for k in np.flatnonzero(m5["t"] >= t0):
        if k <= open_until or k < 100:
            continue
        now = int(m5["t"][k]) + 300
        cur15 = (int(m5["t"][k]) // 900) * 900
        new_bucket = cur15 != last15
        if not new_bucket:  # cheap pre-check: can this bar reach any waiting zone?
            lo, hi = m5["low"][k], m5["high"][k]
            if not any((lo <= e) if d == 1 else (hi >= e) for d, e, _ in waiting):
                continue
        i15 = int(np.searchsorted(m15["t"] + 900, now, side="right"))
        i1 = int(np.searchsorted(h1["t"] + 3600, now, side="right"))
        waiting = []
        pl = plan(_win(m15, i15, 300), _win(h1, i1, 200), _win(m5, k + 1, 100), sym, p, waiting=waiting)
        last15 = cur15
        bid = float(m5["close"][k])
        spr = _spread(sym, now, ex)
        order, why = order_for(pl, bid, bid + spr, p)
        if order is None:
            continue
        if len(news) and np.any(np.abs(news - now) <= ex.news_block_min * 60):
            continue
        tr = _simulate(sym, m5, k, order, p, ex, spr)
        trades.append(tr)
        open_until = tr["exit_index"]
        last15 = -1  # force a fresh plan scan after the trade
    return pd.DataFrame(trades)


def _simulate(sym: str, m5: dict[str, np.ndarray], k: int, o: dict, p, ex: Exec, spr_entry: float) -> dict:
    d = 1 if o["side"] == "BUY" else -1
    slip = ex.slippage_pips * pip(sym)
    entry = o["price"] + d * slip
    sl, tp = o["sl"], o["tp"]
    risk = (entry - sl) * d
    units_per_lot = CONTRACT.get(sym, 100_000.0)
    usd_move = _usd_per_unit_move(sym, entry)
    risk_usd_target = ex.start_balance * ex.risk_pct / 100  # equity compounding applied later in summaries
    lots = max(0.01, np.floor(risk_usd_target / (risk * usd_move * units_per_lot) * 100) / 100)
    size, realized, be_done, part_done = 1.0, 0.0, False, False
    end = min(len(m5["t"]) - 1, k + p.max_hold_bars * 3)
    reason, xi, exit_px = "time", end, None
    for j in range(k + 1, end + 1):
        s = _spread(sym, int(m5["t"][j]), ex)
        o_, h_, l_ = m5["open"][j], m5["high"][j], m5["low"][j]
        # long exits on bid, short exits on ask (= bid + spread)
        adv_hit = (l_ <= sl) if d == 1 else (h_ + s >= sl)
        fav_hit = (h_ >= tp) if d == 1 else (l_ + s <= tp)
        if adv_hit:
            gap_open = o_ if d == 1 else o_ + s
            exit_px = (min(gap_open, sl) if d == 1 else max(gap_open, sl)) - d * slip
            reason, xi = ("stop" if (sl - entry) * d < 0 else "breakeven"), j
            break
        if fav_hit:
            exit_px, reason, xi = tp, "target", j
            break
        best = h_ if d == 1 else l_ + s
        r_now = (best - entry) * d / risk
        if ex.partial_r and not part_done and r_now >= ex.partial_r:  # applied at the partial level, next bars run the rest
            realized += ex.partial_frac * ex.partial_r
            size -= ex.partial_frac
            part_done = True
        if ex.be_r and not be_done and r_now >= ex.be_r:
            sl, be_done = entry, True
    if exit_px is None:
        exit_px = m5["close"][xi] if d == 1 else m5["close"][xi] + _spread(sym, int(m5["t"][xi]), ex)
    r_gross = realized + size * (exit_px - entry) * d / risk
    commission_r = ex.commission_per_lot_rt * lots / (risk * usd_move * units_per_lot * lots)
    return {"symbol": sym, "dir": d, "t_signal": o["plan"]["signal_time"], "t_entry": int(m5["t"][k]) + 300, "t_exit": int(m5["t"][xi]) + 300,
            "entry": entry, "sl": o["sl"], "tp": tp, "exit": exit_px, "exit_reason": reason, "rr_planned": o["rr"], "lots": lots,
            "risk_pips": risk / pip(sym), "spread_pips_entry": spr_entry / pip(sym), "r_gross": r_gross, "commission_r": commission_r,
            "r": r_gross - commission_r, "exit_index": xi, **{f"chk_{k_}": v for k_, v in o["plan"].get("checks", {}).items()}}


def summarize(t: pd.DataFrame, ex: Exec) -> dict:
    if t.empty:
        return {"trades": 0}
    t = t.sort_values("t_exit")
    r = t.r.to_numpy()
    eq = ex.start_balance * (1 + np.cumsum(r) * ex.risk_pct / 100)  # fixed-fraction of starting balance
    peak = np.maximum.accumulate(np.r_[ex.start_balance, eq])[1:]
    dd_pct = float(((peak - eq) / peak).max() * 100)
    day = pd.to_datetime(t.t_exit, unit="s").dt.date
    daily_pct = (t.r * ex.risk_pct).groupby(day).sum()
    yrs = pd.to_datetime(t.t_entry, unit="s").dt.year
    wins = r[r > 0]
    return {"trades": len(r), "win_rate": round(100 * float((r > 0).mean()), 1), "avg_r": round(float(r.mean()), 3),
            "t_stat": round(float(r.mean() / r.std(ddof=1) * np.sqrt(len(r))), 2) if len(r) > 2 else None,
            "profit_factor": round(float(wins.sum() / -r[r <= 0].sum()), 2) if (r <= 0).any() else None,
            "total_r": round(float(r.sum()), 1), "return_pct": round(float(r.sum() * ex.risk_pct), 2), "max_dd_pct": round(dd_pct, 2),
            "worst_day_pct": round(float(daily_pct.min()), 2), "days_over_5pct_loss": int((daily_pct <= -5).sum()),
            "breaches_10pct_max_loss": bool(((ex.start_balance - eq) / ex.start_balance * 100 >= 10).any()),
            "trades_per_month": round(len(r) / max(1, (t.t_entry.max() - t.t_entry.min()) / 2.63e6), 1),
            "by_year": {int(y): {"n": int(len(g)), "avg_r": round(float(g.r.mean()), 3), "total_r": round(float(g.r.sum()), 1)} for y, g in t.groupby(yrs)},
            "by_symbol": {s: {"n": int(len(g)), "avg_r": round(float(g.r.mean()), 3), "total_r": round(float(g.r.sum()), 1)} for s, g in t.groupby("symbol")},
            "exits": t.exit_reason.value_counts().to_dict(), "avg_commission_r": round(float(t.commission_r.mean()), 3),
            "avg_entry_spread_pips": round(float(t.spread_pips_entry.mean()), 2)}


def save(params, ex: Exec, symbols: list[str], start: str | None, end: str | None, t: pd.DataFrame, summary: dict) -> int:
    with db.connect() as c:
        c.execute("""create table if not exists backtest_runs (id bigserial primary key, created_at timestamptz default now(),
                     symbols text, start_date text, end_date text, params jsonb, exec jsonb, summary jsonb)""")
        c.execute("""create table if not exists backtest_trades (run_id bigint references backtest_runs(id) on delete cascade,
                     symbol text, dir int, t_entry timestamptz, t_exit timestamptz, entry double precision, sl double precision,
                     tp double precision, exit double precision, exit_reason text, lots double precision, r double precision, r_gross double precision)""")
        run_id = c.execute("insert into backtest_runs (symbols, start_date, end_date, params, exec, summary) values (%s,%s,%s,%s,%s,%s) returning id",
                           (",".join(symbols), start, end, json.dumps(asdict(params)), json.dumps(asdict(ex)), json.dumps(summary, default=str))).fetchone()[0]
        with c.cursor() as cur:
            cur.executemany("insert into backtest_trades values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                            [(run_id, r.symbol, int(r.dir), datetime.fromtimestamp(int(r.t_entry), timezone.utc), datetime.fromtimestamp(int(r.t_exit), timezone.utc),
                              r.entry, r.sl, r.tp, r.exit, r.exit_reason, r.lots, r.r, r.r_gross) for r in t.itertuples()])
        c.commit()
    return run_id


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(SYMBOLS))
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--news-block", type=int, default=0)
    ap.add_argument("--be-r", type=float, default=0.0)
    ap.add_argument("--partial-r", type=float, default=0.0)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--strategy", choices=sorted(STRATEGIES), default="imbalance")
    ap.add_argument("--csv", default=None, help="also write every trade (with its SMC checks) to this CSV")
    ap.add_argument("--set", action="append", default=[], help="strategy param, e.g. --set min_body_ratio=0.6 --set 'killzones=((7,10),(12,15))'")
    a = ap.parse_args()
    p = STRATEGIES[a.strategy][0](**{k: ast.literal_eval(v) for k, v in (kv.split("=", 1) for kv in a.set)})
    ex = Exec(news_block_min=a.news_block, be_r=a.be_r, partial_r=a.partial_r)
    syms = a.symbols.split(",")
    with ProcessPoolExecutor(a.workers) as pool:
        t = pd.concat([x for x in pool.map(run_symbol, [(s, a.start, a.end, p, ex, a.strategy) for s in syms]) if not x.empty], ignore_index=True)
    s = summarize(t, ex)
    run_id = save(p, ex, syms, a.start, a.end, t, s)
    if a.csv:
        t.to_csv(a.csv, index=False)
    print(json.dumps({"run_id": run_id, **s}, indent=1, default=str))


if __name__ == "__main__":
    main()
