"""Parity: replaying stored M5 bars through the live engine's own path (live.windows + plan) must trigger every
trade the backtest took over the same week, at the same bar.  python -m pytest bensim/test_live.py -q"""
from __future__ import annotations

import numpy as np

from . import db, guard
from .backtest import load_m5
from .live import PARAMS, lots_for, windows
from .strategy import plan

WEEK = ("2026-10-05", "2026-10-10")


def _backtest_entries() -> set[tuple[str, int]]:
    with db.connect() as c:
        rid = c.execute("""select id from backtest_runs where start_date=%s and end_date=%s and params->'confirm' = '["poi","killzone"]'::jsonb
                           and params->>'zone_mode'='fib' order by id desc limit 1""", WEEK).fetchone()
        assert rid, "run the backtest for the week first"
        return {(s, int(t)) for s, t in c.execute("select symbol, extract(epoch from t_entry)::bigint from backtest_trades where run_id=%s", rid)}


def test_live_path_reproduces_backtest_week() -> None:
    want = _backtest_entries()
    got = set()
    for sym in sorted({s for s, _ in want}):
        m5 = load_m5(sym, "2026-09-20", WEEK[1])
        t0 = int(np.datetime64(WEEK[0], "s").astype(np.int64))
        for k in np.flatnonzero(m5["t"] >= t0):
            part = {key: v[:k + 1] for key, v in m5.items()}
            pl = plan(*windows(part), sym, PARAMS)
            if pl and pl.get("status") == "TRIGGERED":
                got.add((sym, int(m5["t"][k]) + 300))
    assert want <= got, f"backtest trades the live path missed: {sorted(want - got)}"


def test_lot_sizing_matches_risk() -> None:
    eurusd = {"trade_tick_size": 1e-5, "trade_tick_value": 1.0, "volume_step": 0.01}
    assert lots_for(eurusd, 1.1000, 1.0990, 500) == 5.0  # 10 pips x $10/pip/lot -> $100 per lot
    gold = {"trade_tick_size": 0.01, "trade_tick_value": 1.0, "volume_step": 0.01}
    assert lots_for(gold, 4100.0, 4083.3, 500) == 0.29  # $16.7 x 100 oz = $1,670 per lot
    guard._selfcheck()


def test_bennett_live_path_reproduces_backtest() -> None:
    """The Bennett live path (bennett_windows: last 3500 M5 bars + the long H1 context) must trigger every trade the
    backtester took with bennett_strategy.LIVE, at the same bar."""
    from . import bennett_strategy as bstrat
    from .backtest import Exec, resample, run_symbol
    from .live import bennett_windows

    a, b = "2026-08-17", "2026-10-10"
    want, got = set(), set()
    for sym in bstrat.LIVE_SYMBOLS:
        bt = run_symbol((sym, a, b, bstrat.LIVE, Exec(), "bennett"))
        want |= {(sym, int(t)) for t in (bt["t_entry"] if len(bt) else [])}
        m5 = load_m5(sym, "2026-05-01", b)
        h1 = resample(m5, 3600)
        t0 = int(np.datetime64(a, "s").astype(np.int64))
        for k in np.flatnonzero(m5["t"] >= t0):
            part = {key: v[max(0, k + 1 - 3500):k + 1] for key, v in m5.items()}
            m15, h1w, m5w, htf = bennett_windows(part, h1)
            pl = bstrat.plan(m15, h1w, m5w, sym, bstrat.LIVE, htf=htf)
            if pl and pl.get("status") == "TRIGGERED":
                got.add((sym, int(m5["t"][k]) + 300))
    assert want, "no backtest trades in the window -- widen it"
    assert want <= got, f"backtest trades the live path missed: {sorted(want - got)}"
