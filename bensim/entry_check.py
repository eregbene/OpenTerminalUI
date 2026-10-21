"""Three "manager gets in" rules on top of fib618 + Bennett, 3-month backtest. Rules fixed BEFORE the run (2026-10-21):

  RE-ENTRY  after a STOP: once, within 24h, if price comes back to the original entry -> limit there, same risk distance,
            same target.
  LATE      a signal skipped because the pair was busy (same book): when that trade closes, take the newest skipped
            signal <= 24h old at market, if its stop/target is untouched and price is within 0.25R of its entry.
  ADD-ON    an open trade reaches +1R and H4, H1, M15 all agree -> add the same size at market, stop = original entry,
            same target, closed with the original trade at the latest.

All with the backtest's spread, slippage and commission; 1 unit = R of the base trade size ($500 at 0.5%).
    python -m bensim.entry_check
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import backtest as bt
from . import bennett_strategy as bstrat
from .backtest import CONTRACT, Exec, _spread, _usd_per_unit_move, load_m5, run_symbol
from .exit_check import _tf_trend
from .live import LIVE_SYMBOLS
from .strategy import pip
from .weekly_check import LIVE as FIB_LIVE

START, END = "2026-07-21", "2026-10-21"
DAY, HOLD = 288, 600  # M5 bars: 24h, max hold (= 200 M15 bars, as the strategies)


def sim(sym, m5, k, d, entry, sl, tp, end, ex) -> float:
    """Filled at `entry` at the close of bar k; plain SL/TP (SL first), else closed at bar `end`. Net R."""
    entry += d * ex.slippage_pips * pip(sym)
    risk = (entry - sl) * d
    xi, px = end, None
    for j in range(k + 1, end + 1):
        s = _spread(sym, int(m5["t"][j]), ex)
        if (m5["low"][j] <= sl) if d == 1 else (m5["high"][j] + s >= sl):
            px = sl - d * ex.slippage_pips * pip(sym)
            break
        if (m5["high"][j] >= tp) if d == 1 else (m5["low"][j] + s <= tp):
            px = tp
            break
    if px is None:
        px = m5["close"][xi] + (0 if d == 1 else _spread(sym, int(m5["t"][xi]), ex))
    comm = ex.commission_per_lot_rt / (_usd_per_unit_move(sym, entry) * CONTRACT.get(sym, 1e5))
    return ((px - entry) * d - comm) / risk


def _all_signals(args):
    """run_symbol with the busy-pair block removed: every signal, simulated as if it were alone."""
    orig = bt._simulate

    def free(*a, **kw):
        tr = orig(*a, **kw)
        return tr | {"exit_index": a[2]}
    bt._simulate = free
    try:
        return run_symbol(args)
    finally:
        bt._simulate = orig


def check(args) -> list[dict]:
    sym, base, signals = args
    ex = Exec()
    m5 = load_m5(sym, (pd.Timestamp(START) - pd.Timedelta(days=90)).isoformat(), END)
    h4, h1, m15 = (_tf_trend(m5, s) for s in (14400, 3600, 900))
    at = lambda t: int(np.searchsorted(m5["t"], t - 300))  # noqa: E731 -- bar index whose close is t
    tr_at = lambda tf, now: int(tf[1][max(0, np.searchsorted(tf[0], now, side="right") - 1)])  # noqa: E731
    out, n = [], len(m5["t"]) - 1
    base = base.sort_values("t_entry").reset_index(drop=True)
    for i, tr in base.iterrows():
        d, k, xi = int(tr.dir), at(tr.t_entry), at(tr.t_exit)
        risk = (tr.entry - tr.sl) * d
        nxt = at(base.t_entry[i + 1]) if i + 1 < len(base) else n  # next base trade: the pair must be free again
        # RE-ENTRY
        if tr.exit_reason == "stop":
            for j in range(xi + 1, min(xi + DAY, nxt)):
                s = _spread(sym, int(m5["t"][j]), ex)
                if (m5["high"][j] >= tr.entry) if d == 1 else (m5["low"][j] + s <= tr.entry):
                    out.append({"rule": "RE-ENTRY", "book": tr.book, "r": sim(sym, m5, j, d, tr.entry, tr.entry - d * risk, tr.tp, min(j + HOLD, nxt, n), ex)})
                    break
        # ADD-ON
        for j in range(k + 1, xi):
            now = int(m5["t"][j]) + 300
            px = m5["close"][j] + (_spread(sym, now, ex) if d == 1 else 0)  # buy at ask, sell at bid
            if (px - tr.entry) * d >= risk and tr_at(h4, now) == tr_at(h1, now) == tr_at(m15, now) == d:
                if (tr.tp - px) * d > 0:
                    r_add = sim(sym, m5, j, d, px, tr.entry, tr.tp, xi, ex) * abs(px - tr.entry) / risk  # same lots -> base R
                    out.append({"rule": "ADD-ON", "book": tr.book, "r": r_add})
                break
        # LATE
        sk = signals[(signals.book == tr.book) & (signals.t_entry > tr.t_entry) & (signals.t_entry < tr.t_exit)
                     & (signals.t_entry >= tr.t_exit - 86400)]
        if len(sk) and xi < nxt:
            s_ = sk.iloc[-1]
            sd, ks = int(s_.dir), at(s_.t_entry)
            seg = slice(ks + 1, xi + 1)
            hit = (m5["low"][seg].min() <= s_.sl or m5["high"][seg].max() >= s_.tp) if sd == 1 else \
                  (m5["high"][seg].max() >= s_.sl or m5["low"][seg].min() <= s_.tp)
            now = int(m5["t"][xi]) + 300
            px = m5["close"][xi] + (_spread(sym, now, ex) if sd == 1 else 0)
            if not hit and (px - s_.entry) * sd >= -0.25 * abs(s_.entry - s_.sl) and (s_.tp - px) * sd > 0:
                out.append({"rule": "LATE", "book": tr.book, "r": sim(sym, m5, xi, sd, px, s_.sl, s_.tp, min(xi + HOLD, nxt, n), ex)})
    return [o | {"symbol": sym} for o in out]


def main() -> None:
    jobs = [(s, START, END, FIB_LIVE, Exec(), "imbalance") for s in LIVE_SYMBOLS] + \
           [(s, START, END, bstrat.LIVE, Exec(), "bennett") for s in bstrat.LIVE_SYMBOLS]
    name = lambda j: "fib618" if j[5] == "imbalance" else "Bennett"  # noqa: E731
    with ProcessPoolExecutor(4) as pool:
        base = list(pool.map(run_symbol, jobs))
        sigs = list(pool.map(_all_signals, jobs))
    per: dict[str, list] = {}
    for j, b, s in zip(jobs, base, sigs):
        if not b.empty:
            per.setdefault(j[0], [[], []])
            per[j[0]][0].append(b.assign(book=name(j)))
            per[j[0]][1].append(s.assign(book=name(j)))
    with ProcessPoolExecutor(4) as pool:
        rows = [r for part in pool.map(check, [(sym, pd.concat(b), pd.concat(s)) for sym, (b, s) in per.items()]) for r in part]
    allb = pd.concat([x for v in per.values() for x in v[0]])
    print(f"{START} .. {END}: base {len(allb)} trades avg {allb.r.mean():+.3f}R total {allb.r.sum():+.1f}R (${allb.r.sum() * 500:+,.0f})")
    df = pd.DataFrame(rows)
    for rule in ("RE-ENTRY", "LATE", "ADD-ON"):
        r = df.r[df.rule == rule] if len(df) else pd.Series(dtype=float)
        if r.empty:
            print(f"  {rule:9} no trades")
            continue
        t = r.mean() / r.std(ddof=1) * np.sqrt(len(r)) if len(r) > 1 and r.std() > 0 else 0.0
        print(f"  {rule:9} extra trades {len(r):3}  win {(r > 0).mean():4.0%}  avg {r.mean():+.3f}R  total {r.sum():+6.1f}R "
              f"(${r.sum() * 500:+,.0f})  t {t:+.1f}  by symbol {df[df.rule == rule].groupby('symbol').r.sum().round(1).to_dict()}")


if __name__ == "__main__":
    main()
