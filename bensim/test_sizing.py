"""python -m pytest bensim/test_sizing.py -q  -- fixed $ risk -> lots (no broker needed)."""
from bensim.sizing import Costs, size

EURUSD = dict(symbol="EURUSD", bid=1.10000, ask=1.10008, point=1e-5, digits=5, trade_tick_value=1.0, trade_tick_size=1e-5,
              volume_min=0.01, volume_max=200.0, volume_step=0.01)
USDJPY = dict(symbol="USDJPY", bid=158.000, ask=158.009, point=1e-3, digits=3, trade_tick_value=0.001 * 100_000 / 158,
              trade_tick_size=1e-3, volume_min=0.01, volume_max=200.0, volume_step=0.01)
NO = Costs(0, 0)


def test_eurusd_exact():  # 20 pips + 0.8 spread = 20.8 pips x $10 = $208/lot -> 500/208 = 2.403 -> 2.40 (down)
    d = size(EURUSD, 1.10008, 1.09808, 500, NO)
    assert d["lots"] == 2.40 and d["pip_value"] == 10 and d["est_loss"] <= 500


def test_jpy_pip_value_and_round_down():
    d = size(USDJPY, 158.009, 157.809, 500, NO)  # 20 pips at $6.33/pip
    assert abs(d["pip_value"] - 6.3291) < 1e-3 and d["est_loss"] <= 500 and d["lots"] == 3.77  # (20 + 0.9 spread) x 6.329 = 132.28 -> 3.779 -> 3.77


def test_costs_shrink_lots_never_exceed():
    a, b = size(EURUSD, 1.10008, 1.09808, 500, NO), size(EURUSD, 1.10008, 1.09808, 500, Costs(7.0, 0.5))
    assert b["lots"] < a["lots"] and b["est_loss"] <= 500


def test_min_lot_rejected_and_max_capped():
    assert not size(EURUSD, 1.10008, 1.00008, 5, NO)["ok"]  # 1000-pip stop, $5: 0.01 lot would lose ~$100
    d = size({**EURUSD, "volume_max": 1.0}, 1.10008, 1.09808, 500, NO)
    assert d["lots"] == 1.0 and d["reason"] == "capped at volume_max"


def test_same_risk_different_lots():
    e, j = size(EURUSD, 1.10008, 1.09808, 500, NO), size(USDJPY, 158.009, 157.809, 500, NO)
    assert e["lots"] != j["lots"] and abs(e["est_loss"] - j["est_loss"]) < 15
