"""python -m pytest bensim/test_backtest.py -q"""
import numpy as np

from bensim.backtest import Exec, _simulate
from bensim.strategy import Params

EX = Exec(raw_spread_pips={"EURUSD": 1.0}, slippage_pips=0.0, commission_per_lot_rt=0.0, rollover_mult=1.0)


def bars(rows):
    a = np.array(rows, dtype=float)
    return {"t": np.arange(len(a), dtype=np.int64) * 300 + 3600, "open": a[:, 0], "high": a[:, 1], "low": a[:, 2], "close": a[:, 3]}


def order(side, price, sl, tp):
    return {"side": side, "price": price, "sl": sl, "tp": tp, "rr": 2.0, "plan": {"signal_time": 0}}


def test_long_target_and_stop_first():
    m = bars([[1.1000, 1.1001, 1.0999, 1.1000], [1.1000, 1.1025, 1.0995, 1.1020]])
    t = _simulate("EURUSD", m, 0, order("BUY", 1.1000, 1.0990, 1.1020), Params(), EX, 0.0001)
    assert t["exit_reason"] == "target" and abs(t["r"] - 2.0) < 1e-9
    m = bars([[1.1000, 1.1001, 1.0999, 1.1000], [1.1000, 1.1025, 1.0985, 1.1020]])  # both hit -> stop
    t = _simulate("EURUSD", m, 0, order("BUY", 1.1000, 1.0990, 1.1020), Params(), EX, 0.0001)
    assert t["exit_reason"] == "stop" and abs(t["r"] + 1.0) < 1e-9


def test_short_exits_on_ask_and_gap():
    # bid high 1.1009 + 1 pip spread = ask 1.1010 reaches the stop
    m = bars([[1.1000, 1.1001, 1.0999, 1.1000], [1.1000, 1.1009, 1.0995, 1.1000]])
    t = _simulate("EURUSD", m, 0, order("SELL", 1.1000, 1.1010, 1.0980), Params(), EX, 0.0001)
    assert t["exit_reason"] == "stop" and abs(t["exit"] - 1.1010) < 1e-9
    m = bars([[1.1000, 1.1001, 1.0999, 1.1000], [1.1030, 1.1035, 1.1025, 1.1030]])  # gap over the stop: fill at ask open
    t = _simulate("EURUSD", m, 0, order("SELL", 1.1000, 1.1010, 1.0980), Params(), EX, 0.0001)
    assert abs(t["exit"] - 1.1031) < 1e-9 and t["r"] < -3
