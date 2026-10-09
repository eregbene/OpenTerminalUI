"""donchian_imbalance (live "combination B"): triggers once, on the first M5 touch of the planned zone."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from backend.mt5_strategies.families import donchian_imbalance as DI

T0 = 1_700_002_800  # hour-aligned UTC
EPS = 1e-9


def _row(t: int, o: float, h: float, l: float, c: float) -> dict:
    return {"time": datetime.fromtimestamp(t, timezone.utc).isoformat(), "open": o, "high": h, "low": l, "close": c}


def _m15() -> list[dict]:
    rows = []
    for i in range(139):  # quiet range around 1.1000
        c = 1.1000 + (0.0002 if i % 2 else -0.0002)
        o = 1.1000 - (c - 1.1000)
        rows.append(_row(T0 + i * 900, o, max(o, c) + 0.0001, min(o, c) - 0.0001, c))
    rows.append(_row(T0 + 139 * 900, 1.0992, 1.1003, 1.0990, 1.1002))  # B: base candle of the run
    rows.append(_row(T0 + 140 * 900, 1.1002, 1.1021, 1.1001, 1.1020))
    rows.append(_row(T0 + 141 * 900, 1.1020, 1.1041, 1.1018, 1.1040))  # first gap: h[139] 1.1003 < l[141] 1.1018
    rows.append(_row(T0 + 142 * 900, 1.1040, 1.1056, 1.1038, 1.1055))
    for i in range(143, 150):  # drift back down, staying above the zone (1.1003)
        c = 1.1055 - (i - 142) * 0.0005
        rows.append(_row(T0 + i * 900, c + 0.0005, c + 0.0006, c - 0.0001, c))
    return rows


def _h1() -> list[dict]:
    rows = []
    for k in range(100):  # blocks of 5: rise to a swing high, dip, next block breaks it -> trend +1
        c = 1.0900 + 0.0010 * (k // 5) + (0.0, 0.0004, 0.0008, 0.0002, 0.0001)[k % 5]
        rows.append(_row(T0 - 70 * 3600 + k * 3600, c - 0.0001, c + 0.0003, c - 0.0003, c))
    return rows


def _m5(lows: list[float]) -> list[dict]:
    start = T0 + 150 * 900  # M5 bars of the forming M15 bar 150
    base = [_row(T0 + 150 * 900 - (100 - j) * 300, 1.1030, 1.1032, 1.1028, 1.1030) for j in range(100 - len(lows))]
    return base + [_row(start + j * 300, 1.1010, 1.1012, lo, 1.1006) for j, lo in enumerate(lows)]


def _ctx(lows: list[float], price: float = 1.1006) -> SimpleNamespace:
    return SimpleNamespace(symbol="EURUSD", broker_symbol="EURUSD", generated_at=datetime.now(timezone.utc), regime="neutral",
                           m15_rows=_m15(), h1_rows=_h1(), m5_rows=_m5(lows), bid=price, ask=price)


def test_precondition_h1_trend_is_up():
    assert DI._trend(DI._arrays(_h1()))[-30:].max() == 1


def test_triggers_on_first_m5_touch_with_planned_stop_and_target():
    sig = DI.evaluate_donchian_imbalance(_ctx([1.1008, 1.1007, 1.1002]))
    assert sig.valid and sig.direction == "LONG", sig.rejection_reason
    assert abs(sig.evidence["planned_entry"] - 1.1003) < EPS
    assert sig.stop_loss < 1.0990 and sig.take_profit > 1.1056
    assert sig.proposed_entry == 1.1006


def test_no_retrigger_when_an_earlier_m5_bar_already_touched():
    sig = DI.evaluate_donchian_imbalance(_ctx([1.1002, 1.1007, 1.1001]))
    assert not sig.valid


def test_no_trigger_before_touch_or_through_the_stop():
    assert not DI.evaluate_donchian_imbalance(_ctx([1.1008, 1.1007, 1.1005])).valid  # not reached yet
    assert not DI.evaluate_donchian_imbalance(_ctx([1.1008, 1.0980])).valid  # first touch also went through the stop
