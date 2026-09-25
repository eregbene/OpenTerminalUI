"""Bump-and-Run Reversal (BARR) trendline detector -- research candidate for V3.

Source: Thomas Bulkowski, thepatternsite.com (barrt.html / barrb.html). Rank 1 of 39 (bottom)
and 3 of 36 (top) for daily stocks. Rules, adapted to FX bars because trendline *degrees* depend
on chart scaling:

- Lead-in: a trendline through two swing points (lows for a top, highs for a bottom); Bulkowski
  30-45 degrees, "at least a month". Here: >= MIN_LEAD_BARS bars, sloping with the trend.
- Bump: price accelerates away (45-60 degrees) and the bump height above the line is >= 2x the
  lead-in height. Here: bump slope >= BUMP_SLOPE_RATIO x lead-in slope and height >= 2x.
- Signal: first close through the lead-in trendline. Target: pattern start A (measure rule).
- Throwback/pullback to the broken line happens 61-64% of the time -> optional retest entry.

Pure functions over plain OHLC rows so the same code serves replay and (later) live runtime.
Not a Faiz course strategy: keep it out of live V3 until replay evidence clears costs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

MIN_LEAD_BARS = 30
BUMP_HEIGHT_RATIO = 2.0
BUMP_SLOPE_RATIO = 1.5
SWING_WINDOW = 3
MAX_PATTERN_BARS = 400
MIN_RR = 1.5


@dataclass(frozen=True)
class BarrSignal:
    direction: str  # SHORT for a BARR top, LONG for a BARR bottom
    signal_index: int  # bar whose close broke the lead-in line
    entry: float
    stop: float
    target: float
    line_at_signal: float
    slope: float  # lead-in line price change per bar
    anchor_index: int  # point A (first lead-in swing)
    bump_index: int

    @property
    def rr(self) -> float:
        risk = abs(self.entry - self.stop)
        return abs(self.target - self.entry) / risk if risk else 0.0

    def line_at(self, index: int) -> float:
        return self.line_at_signal + self.slope * (index - self.signal_index)


def _f(row: Any, key: str) -> float:
    return float(row[key] if isinstance(row, dict) else getattr(row, key))


def _swings(rows: Sequence[Any], key: str, highs: bool) -> list[int]:
    """Fractal swing indices. Swing s is only known once bar s + SWING_WINDOW has closed."""
    w = SWING_WINDOW
    vals = [_f(r, key) for r in rows]
    out = []
    for i in range(w, len(vals) - w):
        window = vals[i - w : i + w + 1]
        if vals[i] == (max(window) if highs else min(window)):
            out.append(i)
    return out


def scan_barr(rows: Sequence[Any]) -> list[BarrSignal]:
    """All BARR signals in `rows`, each at the first close through its lead-in line (causal)."""
    found: dict[tuple[str, int], BarrSignal] = {}
    for direction in ("SHORT", "LONG"):
        for signal in _scan_side(rows, direction):
            found.setdefault((direction, signal.signal_index), signal)
    return sorted(found.values(), key=lambda s: s.signal_index)


def detect_barr(rows: Sequence[Any], index: int) -> BarrSignal | None:
    """Live form: the signal whose break bar is `index`, reading only rows[: index + 1]."""
    for signal in scan_barr(rows[: index + 1]):
        if signal.signal_index == index:
            return signal
    return None


def _scan_side(rows: Sequence[Any], direction: str) -> list[BarrSignal]:
    top = direction == "SHORT"
    # Top: rising line through swing lows, bump in highs. Bottom: mirror.
    line_key, bump_key = ("low", "high") if top else ("high", "low")
    sign = 1.0 if top else -1.0  # +1: pattern sits above the line
    n = len(rows)
    closes = [_f(r, "close") for r in rows]
    line_vals = [_f(r, line_key) for r in rows]
    bump_vals = [_f(r, bump_key) for r in rows]
    anchors = _swings(rows, line_key, highs=not top)
    run_swings = _swings(rows, bump_key, highs=top)
    out: list[BarrSignal] = []
    for a_pos, a in enumerate(anchors):
        for b in anchors[a_pos + 1 :]:
            if b - a > MAX_PATTERN_BARS:
                break
            if b - a < MIN_LEAD_BARS // 2:
                continue
            slope = (line_vals[b] - line_vals[a]) / (b - a)
            if sign * slope <= 0:  # lead-in must trend with the pattern
                continue

            def line(k: int) -> float:
                return line_vals[a] + slope * (k - a)

            # Walk forward to the first close through the line; the line must be known (b
            # confirmed) before that bar can count as a signal.
            i = next((k for k in range(a, min(n, a + MAX_PATTERN_BARS)) if sign * (closes[k] - line(k)) < 0), None)
            if i is None or i < b + SWING_WINDOW or i <= b + 1:
                continue
            lead_height = max(sign * (bump_vals[k] - line(k)) for k in range(a, b + 1))
            if lead_height <= 0:
                continue
            p = max(range(b + 1, i), key=lambda k: sign * (bump_vals[k] - line(k)))
            bump_height = sign * (bump_vals[p] - line(p))
            if p - a < MIN_LEAD_BARS or bump_height < BUMP_HEIGHT_RATIO * lead_height:
                continue
            if sign * (bump_vals[p] - line_vals[b]) / (p - b) < BUMP_SLOPE_RATIO * abs(slope):
                continue
            # Stop beyond the last confirmed swing of the "run" leg after the bump, else the peak.
            run = [s for s in run_swings if p < s and s + SWING_WINDOW <= i]
            stop = bump_vals[run[-1] if run else p]
            signal = BarrSignal(direction, i, closes[i], stop, line_vals[a], line(i), slope, a, p)
            if signal.rr >= MIN_RR and sign * (closes[i] - signal.target) > 0 and sign * (stop - closes[i]) > 0:
                out.append(signal)
    return out


def _self_check() -> None:
    # Synthetic BARR top: zigzag lead-in rising slowly, steep bump, lower-high bounce, then a
    # close back through the lead-in line.
    closes = []
    for k in range(40):
        closes.append(1.0 + 0.0003 * k + 0.002 * abs((k % 8) - 4) / 4)
    for k in range(15):
        closes.append(closes[-1] + 0.003)
    for k in range(8):
        closes.append(closes[-1] - 0.004)
    for k in range(3):
        closes.append(closes[-1] + 0.002)
    for k in range(20):
        closes.append(closes[-1] - 0.003)
    rows = [{"high": c + 0.0004, "low": c - 0.0004, "close": c} for c in closes]
    found = [s for s in (detect_barr(rows, k) for k in range(len(rows))) if s]
    assert found and found[0].direction == "SHORT", found
    first = found[0]
    assert first.target < first.entry < first.stop and first.rr >= MIN_RR
    # Causal: the signal bar is the first close below the line.
    assert rows[first.signal_index - 1]["close"] >= first.line_at(first.signal_index - 1)


if __name__ == "__main__":
    _self_check()
    print("ok")
