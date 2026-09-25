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
