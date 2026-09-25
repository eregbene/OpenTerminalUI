from datetime import datetime, timezone

import scripts.bsi_v3_august_validation as v3


def _op():
    t = datetime(2026, 8, 3, 12, tzinfo=timezone.utc)
    return v3.Opportunity("s", "EURUSD", "LONG", t, 1.1000, 1.0990, 1.1020, ["M15"], "th", "op1", [])


def _bar(high, low):
    return v3.Bar(datetime(2026, 8, 3, 12, 15, tzinfo=timezone.utc), 1.1, high, low, 1.1, 0)


def test_realistic_fills_charge_costs_and_same_bar_is_loss(monkeypatch):
    monkeypatch.setattr(v3, "REALISTIC_FILLS", False)
    assert v3.simulate_outcome(_op(), [_bar(1.1025, 1.0985)]).managed_r == 0.0
    assert v3.simulate_outcome(_op(), [_bar(1.1025, 1.0995)]).baseline_r == 2.0

    monkeypatch.setattr(v3, "REALISTIC_FILLS", True)
    both = v3.simulate_outcome(_op(), [_bar(1.1025, 1.0985)])
    assert both.status == "sl_same_bar" and both.managed_r == -1.09  # 0.9 pip cost on a 10 pip stop
    assert v3.simulate_outcome(_op(), [_bar(1.1025, 1.0995)]).baseline_r == 1.91


def test_clean_source_resamples_h1_from_m5(monkeypatch):
    monkeypatch.setenv("BSI_V3_CANDLE_PROVIDER", "MT5")
    store = object.__new__(v3.CandleStore)
    t0 = datetime(2026, 8, 3, 12, tzinfo=timezone.utc).timestamp()
    m5 = [v3.Bar(datetime.fromtimestamp(t0 + i * 300, timezone.utc), 1 + i, 10 + i, -i, 2 + i, 1) for i in range(13)]
    a, b = m5[0].time, m5[-1].time
    store._bar_cache = {("EURUSD", "M5", a, b): m5}
    h1 = store.candles("EURUSD", "H1", a, b)
    assert [(x.open, x.high, x.low, x.close, x.volume) for x in h1] == [(1, 21, -11, 13, 12), (13, 22, -12, 14, 1)]


def test_bias_ignores_bars_not_closed_yet():
    t0 = datetime(2026, 8, 3, tzinfo=timezone.utc).timestamp()
    rising = [v3.Bar(datetime.fromtimestamp(t0 + i * 3600, timezone.utc), i, i + 1, i - 1, i, 0) for i in range(8)]
    falling = [v3.Bar(datetime.fromtimestamp(t0 + (8 + i) * 3600, timezone.utc), -i, -i + 1, -i - 1, -i, 0) for i in range(8)]
    at = rising[-1].time + (rising[1].time - rising[0].time)  # the moment the last rising bar closes
    assert v3._bias_at({"H1": rising}, ("H1",), at) == "LONG"
    assert v3._bias_at({"H1": rising + falling}, ("H1",), at) == "LONG"  # future bars must not leak in
