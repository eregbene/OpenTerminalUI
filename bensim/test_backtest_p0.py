"""Audit P0 options: default Exec unchanged; friday_close_utc never holds past Friday 20:00 UTC; swap charged per night.

    python -m pytest -q bensim/test_backtest_p0.py
"""
import numpy as np

from bensim.backtest import Exec, exec_profile, friday_close_ts, run_symbol
from bensim.weekly_check import LIVE

ARGS = ("GBPUSD", "2026-08-03", "2026-11-03", LIVE)


def test_friday_close_and_swap():
    base = run_symbol((*ARGS, Exec(), "imbalance"))
    obs = run_symbol((*ARGS, exec_profile("observed_account"), "imbalance"))
    assert (base.swap_r == 0).all() and (base.nights == 0).all()  # default: no swap, no new behaviour
    assert len(obs) > 0
    for t_in, t_out in zip(obs.t_entry, obs.t_exit):
        assert t_out <= friday_close_ts(int(t_in), 20)  # never held past the entry week's Friday 20:00 UTC
        assert not ((t_in // 86400 + 3) % 7 == 4 and t_in % 86400 >= 20 * 3600)  # no entry after the Friday close
    held = obs[obs.nights > 0]
    rate = exec_profile("observed_account").swap_usd_per_lot_night
    for x in held.itertuples():
        if rate.get(f"GBPUSD|{'long' if x.dir == 1 else 'short'}") is not None:
            assert x.swap_r != 0
    assert np.allclose(obs.r, obs.r_gross - obs.commission_r + obs.swap_r)
