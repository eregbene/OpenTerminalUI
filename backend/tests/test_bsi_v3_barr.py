from backend.mt5_strategies.families import bsi_v3_barr as barr


def test_barr_detects_synthetic_top_causally():
    barr._self_check()


def test_barr_live_form_matches_scan_and_ignores_future_bars():
    closes = [1.0 + 0.0003 * k + 0.002 * abs((k % 8) - 4) / 4 for k in range(40)]
    for step, count in ((0.003, 15), (-0.004, 8), (0.002, 3), (-0.003, 20)):
        for _ in range(count):
            closes.append(closes[-1] + step)
    rows = [{"high": c + 0.0004, "low": c - 0.0004, "close": c} for c in closes]
    first = barr.scan_barr(rows)[0]
    # Same signal when only the bars up to the break exist (no look-ahead).
    assert barr.detect_barr(rows, first.signal_index) == first
    assert barr.detect_barr(rows, first.signal_index - 1) is None
