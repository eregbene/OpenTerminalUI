import numpy as np

from .timemachine import blocks, friday_close, replay


def _pool(drift: float, seed: int = 0) -> dict:
    """26 synthetic weeks of M5 bars (Mon 1970-01-05 onward), random walk with the given drift per bar."""
    rng = np.random.default_rng(seed)
    t = np.arange(4 * 86400, 4 * 86400 + 26 * 7 * 86400, 300, dtype=np.int64)
    c = 1.1 * np.exp(np.cumsum(drift + 0.0003 * rng.standard_normal(len(t))))
    o = np.r_[1.1, c[:-1]]
    return blocks({"t": t, "open": o, "high": np.maximum(o, c) * 1.0001, "low": np.minimum(o, c) * 0.9999, "close": c})


def test_no_drift_is_a_coin_flip():
    pool = _pool(0.0)
    t0 = 4 * 86400 + 27 * 7 * 86400 + 8 * 3600  # a Monday 08:00, after the pool
    r = replay(pool, "EURUSD", 1, 1.1, 1.095, 1.105, 1.1, t0, friday_close(t0), n=4000)
    sides = r["target"] + r["stop"]
    assert sides > 0.5 and abs(r["target"] / sides - 0.5) < 0.05, r


def test_pool_drift_is_removed():
    pool = _pool(0.0002)  # strong uptrend in the pool must NOT make the long target likelier
    t0 = 4 * 86400 + 27 * 7 * 86400 + 8 * 3600
    r = replay(pool, "EURUSD", 1, 1.1, 1.095, 1.105, 1.1, t0, friday_close(t0), n=4000)
    sides = r["target"] + r["stop"]
    assert abs(r["target"] / sides - 0.5) < 0.05, r


def test_friday_close():
    mon = 4 * 86400 + 8 * 3600  # Monday 1970-01-05 08:00 UTC
    assert friday_close(mon) == 8 * 86400 + 20 * 3600


def test_repo_mode_keeps_drift():
    from .timemachine import bars_by_slot
    rng = np.random.default_rng(1)
    t = np.arange(4 * 86400, 4 * 86400 + 60 * 86400, 300, dtype=np.int64)
    c = 1.1 * np.exp(np.cumsum(0.0002 + 0.0003 * rng.standard_normal(len(t))))
    o = np.r_[1.1, c[:-1]]
    pool = bars_by_slot({"t": t, "open": o, "high": np.maximum(o, c) * 1.0001, "low": np.minimum(o, c) * 0.9999,
                         "close": c})
    t0 = int(t[-1]) + 8 * 3600
    r = replay(pool, "EURUSD", 1, 1.1, 1.095, 1.105, 1.1, t0, friday_close(t0), n=2000, unit=300)
    assert r["target"] > 0.9, r  # their method keeps the uptrend -> the long target wins
