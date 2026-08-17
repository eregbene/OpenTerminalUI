"""Lorentzian Classification, Role A: standalone candidate generation -- no existing strategy or
fingerprint involved. Independently implements jdehorty's actual native methodology (each
historical bar labeled by simple forward price direction, k=8 nearest-neighbor MAJORITY VOTE
over a bounded recent lookback -- his own public "maxBarsBack" convention, here capped at 750
rather than his default 2000 as a deliberate compute/fidelity tradeoff for this one-off research
validation, stated rather than hidden) to generate LONG/SHORT signals purely from price-derived
oscillator similarity, with no SMC/strategy logic involved at all.

Training labels are cheap (O(1) per bar: is close[i+N] > close[i]) -- jdehorty's own real
methodology, not an R-multiple label. The EXPENSIVE ATR-based forward-walk (same stop=1.2xATR/
target=2.5xATR convention as scratch_squeeze_momentum_validation.py's Role A, for a fair
cross-concept comparison) only runs at actual trigger bars (strong majority agreement), not
every bar -- keeps this tractable at the same order of cost as the squeeze standalone test.

Evaluated at a stride (every 3rd bar) rather than literally every M15 bar -- a real live indicator
would evaluate every bar; this is a stated compute/fidelity tradeoff for a research validation,
not a claim about what a live deployment would do.

Nothing here writes to any table.
"""
from __future__ import annotations

import bisect
from decimal import Decimal

from backend.brokers.mt5.orm import MT5CanonicalCandleORM
from backend.market_structure.bar_utils import average_true_range, normalize_bars
from backend.market_structure.oscillators import lorentzian_feature_series
from backend.shared.db import SessionLocal

SYMBOL = "EURUSD"
PROVIDER = "FOREXSB"
K = 8
MAJORITY_THRESHOLD = 6  # of 8 -- "strong" agreement, not a bare 5/8 majority
POOL_CAP_BARS = 750     # jdehorty's own default is 2000; capped here for tractable compute
STRIDE = 3               # evaluate every 3rd bar (stated compute/fidelity tradeoff)
LABEL_HORIZON_BARS = 4   # jdehorty's own default forward-label horizon
_STOP_ATR_MULT = Decimal("1.2")
_TARGET_ATR_MULT = Decimal("2.5")
_MAX_LOOKFORWARD_BARS = 800


def load():
    print(f"Loading {PROVIDER} M15 canonical candles for {SYMBOL}...", flush=True)
    with SessionLocal() as db:
        rows = (
            db.query(MT5CanonicalCandleORM)
            .filter(
                MT5CanonicalCandleORM.broker_symbol == SYMBOL, MT5CanonicalCandleORM.provider == PROVIDER,
                MT5CanonicalCandleORM.timeframe == "M15", MT5CanonicalCandleORM.quality != "INVALID",
                MT5CanonicalCandleORM.timestamp_utc.isnot(None),
            )
            .order_by(MT5CanonicalCandleORM.timestamp_utc.asc())
            .all()
        )
    print(f"  {len(rows)} bars loaded", flush=True)
    dict_rows = [{"time": r.timestamp_utc, "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.tick_volume} for r in rows]
    bars = normalize_bars(dict_rows, symbol=SYMBOL, timeframe="M15")
    print("Computing Lorentzian feature series...", flush=True)
    features = lorentzian_feature_series(bars, normalize_window=100)
    print("Computing ATR(14) series...", flush=True)
    atrs = average_true_range(bars, 14)
    return bars, features, atrs


def _lorentzian_distance(a, b):
    import math
    return sum(math.log1p(abs(x - y)) for x, y in zip(a, b))


def _walk_forward(bars, start_idx, direction, entry, stop, target, risk):
    long = direction == "LONG"
    end_idx = min(len(bars), start_idx + _MAX_LOOKFORWARD_BARS)
    for j in range(start_idx, end_idx):
        b = bars[j]
        sl_touched = (b.low <= stop) if long else (b.high >= stop)
        tp_touched = (b.high >= target) if long else (b.low <= target)
        if sl_touched or tp_touched:
            if sl_touched:
                return -1.0, True
            return float(abs(target - entry) / risk), True
    if end_idx > start_idx and end_idx - start_idx >= _MAX_LOOKFORWARD_BARS:
        last_close = bars[end_idx - 1].close
        mtm_r = float((last_close - entry) / risk) * (1.0 if long else -1.0)
        return mtm_r, True
    return None, False


def main():
    bars, features, atrs = load()
    n = len(bars)

    # Cheap O(1)-per-bar forward-direction labels (jdehorty's own native training target).
    closes = [float(b.close) for b in bars]
    labels: list[int | None] = [None] * n  # +1 = up, -1 = down, None = unavailable
    for i in range(n - LABEL_HORIZON_BARS):
        labels[i] = 1 if closes[i + LABEL_HORIZON_BARS] > closes[i] else -1

    print(f"Walking {n} bars, stride={STRIDE}, pool_cap={POOL_CAP_BARS}, k={K}, majority>={MAJORITY_THRESHOLD}/8...", flush=True)
    # No-lookahead pool management (same fix as market_structure/pivot_confidence.py's
    # pivot_confidence_series -- caught via manual review of an implausibly strong first result:
    # appending a bar's own (feature, label) pair to the pool and querying that SAME pool at that
    # SAME bar is a real leak. label_i = sign(close[i+4] - close[i]) needs bars i..i+4 to exist --
    # it is NOT knowable "as of bar i". A bar only becomes safe pool evidence once its own label
    # horizon has genuinely elapsed relative to the CURRENT query bar (j + LABEL_HORIZON_BARS <=
    # i), never merely because it was appended in an earlier loop iteration.
    pending: list[tuple[int, tuple, int]] = []  # (bar_index, feature_vector, label)
    pool_features: list[tuple] = []  # ring buffer of (feature_vector, label), all label-safe
    trades = []
    first_feature_idx = next((i for i, f in enumerate(features) if f is not None), None)
    if first_feature_idx is None:
        print("No features computed at all -- aborting"); return

    i = first_feature_idx
    while i < n - LABEL_HORIZON_BARS - 1:
        still_pending = []
        for born_idx, feat_j, label_j in pending:
            if born_idx + LABEL_HORIZON_BARS <= i:
                pool_features.append((feat_j, label_j))
                if len(pool_features) > POOL_CAP_BARS:
                    pool_features.pop(0)
            else:
                still_pending.append((born_idx, feat_j, label_j))
        pending = still_pending

        feat_i = features[i]
        label_i = labels[i]
        if feat_i is not None and label_i is not None:
            pending.append((i, feat_i, label_i))

        if i % STRIDE == 0 and len(pool_features) >= K and atrs[i] is not None and atrs[i] > 0 and features[i] is not None:
            query = features[i]
            scored = sorted(pool_features, key=lambda t: _lorentzian_distance(query, t[0]))[:K]
            up_votes = sum(1 for _, lab in scored if lab == 1)
            down_votes = K - up_votes
            direction = None
            if up_votes >= MAJORITY_THRESHOLD:
                direction = "LONG"
            elif down_votes >= MAJORITY_THRESHOLD:
                direction = "SHORT"
            if direction is not None and i + 1 < n:
                entry_bar = bars[i + 1]
                entry = entry_bar.open
                atr_d = Decimal(str(atrs[i]))
                if direction == "LONG":
                    stop, target = entry - atr_d * _STOP_ATR_MULT, entry + atr_d * _TARGET_ATR_MULT
                else:
                    stop, target = entry + atr_d * _STOP_ATR_MULT, entry - atr_d * _TARGET_ATR_MULT
                risk = abs(entry - stop)
                if risk > 0:
                    outcome_r, resolved = _walk_forward(bars, i + 1, direction, entry, stop, target, risk)
                    if resolved:
                        trades.append({"entry_time": entry_bar.open_time, "direction": direction, "outcome_r": outcome_r, "up_votes": up_votes})
        if i % 5000 == 0:
            print(f"  progress: bar {i}/{n} ({round(100*i/n,1)}%), {len(trades)} trigger events so far", flush=True)
        i += 1

    print(f"Total standalone trigger events with a resolved outcome: {len(trades)}", flush=True)
    if not trades:
        return
    trades.sort(key=lambda t: t["entry_time"])

    def _stats(rs):
        nn = len(rs)
        if nn == 0:
            return {"n": 0, "expectancy_r": None, "win_rate": None, "profit_factor": None}
        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]
        gw, gl = sum(wins), abs(sum(losses))
        pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else None)
        return {"n": nn, "expectancy_r": round(sum(rs) / nn, 4), "win_rate": round(len(wins) / nn, 4), "profit_factor": round(pf, 3) if pf not in (None, float("inf")) else pf}

    all_rs = [t["outcome_r"] for t in trades]
    cut = int(len(trades) * 0.6)
    train_rs = [t["outcome_r"] for t in trades[:cut]]
    oos_rs = [t["outcome_r"] for t in trades[cut:]]
    print(f"  all:     {_stats(all_rs)}")
    print(f"  train60: {_stats(train_rs)}")
    print(f"  oos40:   {_stats(oos_rs)}")
    long_rs = [t["outcome_r"] for t in trades if t["direction"] == "LONG"]
    short_rs = [t["outcome_r"] for t in trades if t["direction"] == "SHORT"]
    print(f"  LONG only:  {_stats(long_rs)}")
    print(f"  SHORT only: {_stats(short_rs)}")
    # Stronger-conviction subset (8/8 or 0/8 unanimous vote) vs the bare 6/8 threshold
    unanimous = [t["outcome_r"] for t in trades if t["up_votes"] in (0, K)]
    print(f"  unanimous (8/8) subset: {_stats(unanimous)}")


if __name__ == "__main__":
    main()
