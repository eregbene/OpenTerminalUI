"""Hand-verified financial calculations vs Bensim functions (read-only; no broker calls). python reports/code_audit_2026/math_checks.py"""
import math
from bensim.live import lots_for, loss_per_lot
from bensim.backtest import _usd_per_unit_move, CONTRACT, Exec, exec_profile, CURRENCIES
from bensim.strategy import pip
from bensim import guard
res = []
def check(name, got, want, tol=1e-6):
    ok = abs(got - want) <= tol * max(1, abs(want)); res.append((name, got, want, ok)); print(f"{'OK ' if ok else 'BAD'} {name}: got {got} want {want}")
# MT5-style symbol specs (tick_value = USD per tick per 1.0 lot)
EURUSD = {"trade_tick_size": 1e-5, "trade_tick_value": 1.0, "volume_step": 0.01}
USDJPY = {"trade_tick_size": 0.001, "trade_tick_value": 0.001 * 100000 / 157.0, "volume_step": 0.01}
GBPJPY = {"trade_tick_size": 0.001, "trade_tick_value": 0.001 * 100000 / 157.0, "volume_step": 0.01}   # JPY profit converted at USDJPY
USDCAD = {"trade_tick_size": 1e-5, "trade_tick_value": 1e-5 * 100000 / 1.40, "volume_step": 0.01}
XAUUSD = {"trade_tick_size": 0.01, "trade_tick_value": 1.0, "volume_step": 0.01}
# 1) live sizing: lots = risk / (stop distance in ticks * tick value), floored to the step
check("EURUSD 10 pip stop $500 -> 5.00 lots", lots_for(EURUSD, 1.10000, 1.09900, 500), 5.00)
check("USDJPY 20 pip stop $500 -> floor(500/127.39)=3.92", lots_for(USDJPY, 157.00, 156.80, 500), 3.92)
check("USDJPY loss per lot 20 pips = 200 ticks*0.6369 = $127.39", loss_per_lot(USDJPY, 157.00, 156.80), 0.2 / 157.0 * 100000, 1e-9)
check("GBPJPY 30 pip stop $250 -> $191.08/lot -> 1.30 lots", lots_for(GBPJPY, 210.00, 209.70, 250), 1.30)
check("USDCAD 15 pip stop $244 -> $107.14/lot -> 2.27 lots", lots_for(USDCAD, 1.40000, 1.39850, 244), 2.27)
check("XAUUSD $16.7 stop $500 -> 0.29 lots", lots_for(XAUUSD, 4100.0, 4083.3, 500), 0.29)
check("floor guard: 4.999999999 lots stays 5.00 not 4.99", lots_for(EURUSD, 1.1, 1.0999, 50.0), 5.00)
# 2) backtest USD per unit move and contract sizes
check("EURUSD usd/unit move = 1", _usd_per_unit_move("EURUSD", 1.1), 1.0)
check("USDJPY usd/unit move = 1/157", _usd_per_unit_move("USDJPY", 157.0), 1 / 157.0)
check("XAUUSD contract 100", CONTRACT["XAUUSD"], 100.0)
try:
    _usd_per_unit_move("GBPJPY", 210.0); print("OK  crosses supported")
except KeyError as e:
    print("BAD backtest _usd_per_unit_move has no entry for crosses (KeyError", e, ") -> backtest cannot price GBPJPY/EURJPY/CADJPY")
# 3) backtest commission in R: $7/lot rt on EURUSD 10-pip stop = 0.7 pip / 10 pip = 0.07R
ex = Exec(); risk = 0.0010
check("EURUSD commission_r 10-pip stop = 0.07R", ex.commission_per_lot_rt / (_usd_per_unit_move("EURUSD", 1.1) * 100000) / risk, 0.07)
check("USDJPY commission_r 20-pip stop = 7/(100000/157)/0.20 = 0.05495R", ex.commission_per_lot_rt / (_usd_per_unit_move("USDJPY", 157.0) * 100000) / 0.20, 7 * 157.0 / 100000 / 0.20)
check("XAUUSD commission_r $10 stop = 7/100/10 = 0.007R", ex.commission_per_lot_rt / (1.0 * 100) / 10.0, 0.007)
# 4) P0 swap in R: EURUSD long -8.033 USD/lot-night, 2 nights, 15-pip stop -> -16.066 / (0.0015*100000) = -0.1071R
check("EURUSD swap 2 nights 15-pip stop = -0.1071R", 2 * -8.033 / (0.0015 * 1.0 * 100000), -16.066 / 150.0)
# 5) pip sizes
check("pip EURUSD", pip("EURUSD"), 1e-4); check("pip USDJPY", pip("USDJPY"), 0.01); check("pip XAUUSD (=$0.10)", pip("XAUUSD"), 0.1)
check("pip GBPJPY", pip("GBPJPY"), 0.01)
# 6) guard currency legs
print("legs XAUUSD sell:", guard.legs("XAUUSD", -1), "| GBPJPY buy:", guard.legs("GBPJPY", 1))
print("backtest CURRENCIES keys:", sorted(CURRENCIES)[:9], "...")
print(sum(not r[3] for r in res), "BAD of", len(res))
