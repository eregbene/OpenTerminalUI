"""Phase 1-2: candle loading, cleaning, resampling and multi-timeframe synchronisation.

Bars are numpy structured columns keyed by bar OPEN time (UTC, epoch seconds). A bar of
timeframe P opened at t is only knowable once t + P has passed; `known_at` encodes that and every
cross-timeframe lookup goes through `last_closed()` so a 4H bar is never visible before it closes.

Sources (mt5_canonical_candles):
  FOREXSB M15  -- UTC, 2018-08 .. 2026-08, all 7 instruments. The long-history base.
  MT5 MetaQuotes-Demo M5 -- server clock (Europe/Athens), converted per bar to UTC with the repo's
                 own MT5_SERVER_TZ. Only ~8-16 months; used for the exact 4H/1H/15M/5M stack.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from backend.historical_intelligence.quality import MT5_SERVER_TZ

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "bars" / "bensim"
PG_URL = os.getenv("BENSIM_DATABASE_URL", "postgresql://openterminalui:openterminalui@localhost:5432/openterminalui")

SYMBOLS = ["EURUSD", "XAUUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCHF", "USDCAD"]
END_TS = int(datetime(2026, 8, 14, tzinfo=timezone.utc).timestamp())
TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H2": 7200, "H4": 14400, "D1": 86400, "W1": 604800}


@dataclass
class Bars:
    tf: str
    t: np.ndarray  # open time, int64 epoch seconds UTC
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    v: np.ndarray

    @property
    def known_at(self) -> np.ndarray:
        return self.t + TF_SECONDS[self.tf]

    def __len__(self) -> int:
        return len(self.t)

    def slice(self, a: int, b: int) -> "Bars":
        return Bars(self.tf, self.t[a:b], self.o[a:b], self.h[a:b], self.l[a:b], self.c[a:b], self.v[a:b])


def _fetch(symbol: str, tf: str, provider: str, server: str | None) -> np.ndarray:
    import psycopg

    sql = (
        "select extract(epoch from timestamp)::bigint, open, high, low, close, coalesce(tick_volume,0) "
        "from mt5_canonical_candles where broker_symbol=%s and timeframe=%s and provider=%s"
    )
    args: list = [symbol, tf, provider]
    if server:
        sql += " and broker_server=%s"
        args.append(server)
    with psycopg.connect(PG_URL, connect_timeout=10) as conn:
        rows = conn.execute(sql + " order by 1", args).fetchall()
    return np.array(rows, dtype=float) if rows else np.zeros((0, 6))


def clean(raw: np.ndarray, tf: str, *, server_clock: bool = False) -> tuple[Bars, dict]:
    """Dedupe, off-grid/bad-tick/weekend filtering. Returns bars + a data-quality report."""
    report = {"raw_rows": int(len(raw))}
    t = raw[:, 0].astype(np.int64)
    if server_clock:  # MT5 server wall clock stored as if UTC -> true UTC, DST-aware per bar
        t = np.array(
            [int(datetime.fromtimestamp(x, timezone.utc).replace(tzinfo=MT5_SERVER_TZ).timestamp()) for x in t],
            dtype=np.int64,
        )
    step = TF_SECONDS[tf]
    order = np.argsort(t, kind="stable")
    raw, t = raw[order], t[order]
    keep = np.r_[True, np.diff(t) > 0]  # duplicates: keep first
    report["duplicates"] = int((~keep).sum())
    on_grid = t % step == 0
    report["off_grid"] = int((keep & ~on_grid).sum())
    o, h, l, c, v = (raw[:, i] for i in range(1, 6))
    sane = (h >= np.maximum(o, c)) & (l <= np.minimum(o, c)) & (l > 0)
    report["bad_ohlc"] = int((keep & on_grid & ~sane).sum())
    # Bad ticks: a range > 25x the rolling median range is almost surely a data error.
    rng = h - l
    med = np.convolve(rng, np.ones(201) / 201, mode="same")
    spike = rng > 25 * np.maximum(med, 1e-12)
    report["spikes"] = int((keep & on_grid & sane & spike).sum())
    dow = ((t // 86400) + 3) % 7  # 0=Mon .. 5=Sat 6=Sun (epoch day 0 = Thursday)
    hour = (t % 86400) // 3600
    weekend = (dow == 5) | ((dow == 6) & (hour < 20))
    report["weekend_bars"] = int((keep & on_grid & sane & ~spike & weekend).sum())
    m = keep & on_grid & sane & ~spike & ~weekend
    bars = Bars(tf, t[m], o[m], h[m], l[m], c[m], v[m])
    gaps = np.diff(bars.t)
    report["rows"] = len(bars)
    report["intraweek_gaps_gt_1h"] = int(((gaps > 3600) & (gaps < 2 * 86400)).sum())
    report["start"] = _iso(bars.t[0]) if len(bars) else None
    report["end"] = _iso(bars.t[-1]) if len(bars) else None
    return bars, report


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).isoformat()


def load(symbol: str, base: str = "M15") -> tuple[Bars, dict]:
    """Cached base-timeframe bars for one symbol. base=M15 -> FOREXSB, base=M5 -> MT5 MetaQuotes-Demo."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}_{base}.npz"
    if path.exists():
        z = np.load(path)
        return Bars(base, z["t"], z["o"], z["h"], z["l"], z["c"], z["v"]), json.loads(str(z["report"]))
    if base == "M15":
        bars, rep = clean(_fetch(symbol, "M15", "FOREXSB", None), "M15")
        mt5, _ = clean(_fetch(symbol, "M15", "MT5", "MetaQuotes-Demo"), "M15", server_clock=True)
        bars, rep["mt5_gap_fill_rows"] = _fill_gaps(bars, mt5)
    else:
        bars, rep = clean(_fetch(symbol, "M5", "MT5", "MetaQuotes-Demo"), "M5", server_clock=True)
    keep = bars.t < END_TS  # the DB holds 2027-dated test rows; nothing after the FOREXSB end is trusted
    bars = Bars(bars.tf, bars.t[keep], bars.o[keep], bars.h[keep], bars.l[keep], bars.c[keep], bars.v[keep])
    rep.update(rows=len(bars), start=_iso(bars.t[0]), end=_iso(bars.t[-1]))
    np.savez(path, t=bars.t, o=bars.o, h=bars.h, l=bars.l, c=bars.c, v=bars.v, report=np.array(json.dumps(rep)))
    return bars, rep


def _fill_gaps(base: Bars, filler: Bars, min_gap: int = 4 * 86400) -> tuple[Bars, int]:
    """Insert filler bars strictly inside base gaps longer than `min_gap` (e.g. EURUSD FOREXSB 2022-26)."""
    gaps = np.flatnonzero(np.diff(base.t) > min_gap)
    take = np.zeros(len(filler), dtype=bool)
    for g in gaps:
        take |= (filler.t > base.t[g]) & (filler.t < base.t[g + 1])
    take |= filler.t > base.t[-1] if len(base) and base.t[-1] < END_TS - min_gap else False
    if not take.any():
        return base, 0
    cols = [np.r_[getattr(base, k), getattr(filler, k)[take]] for k in "tohlcv"]
    order = np.argsort(cols[0], kind="stable")
    return Bars(base.tf, *(col[order] for col in cols)), int(take.sum())


def resample(bars: Bars, tf: str) -> Bars:
    """Aggregate to a higher timeframe on UTC buckets. D1 buckets roll at 22:00 UTC (FX day); Sunday
    bars (the summer-time 20:00-22:00 open) join Monday's day instead of forming a 1-2h stub day.
    W1 buckets are labelled Saturday 22:00 UTC (no bars until the Sunday open), so the whole Sunday
    session belongs to the new week. ponytail: fixed 22:00 roll, NY-close DST shift ignored (1h)."""
    step = TF_SECONDS[tf]
    shift = 7200 if tf in ("D1", "W1") else 0  # 22:00 UTC roll == +2h shift
    if tf == "W1":
        shift += 4 * 86400  # epoch day 0 is Thursday
    key = (bars.t + shift) // step
    if tf == "D1":
        key = key + (((bars.t // 86400) + 3) % 7 == 6) * ((bars.t % 86400) < 79200)  # Sunday pre-22:00 -> Monday
    starts = np.r_[0, np.flatnonzero(np.diff(key)) + 1]
    ends = np.r_[starts[1:], len(key)]
    t = key[starts] * step - shift
    return Bars(
        tf,
        t.astype(np.int64),
        bars.o[starts],
        np.maximum.reduceat(bars.h, starts),
        np.minimum.reduceat(bars.l, starts),
        bars.c[ends - 1],
        np.add.reduceat(bars.v, starts),
    )


def last_closed(htf: Bars, at: np.ndarray) -> np.ndarray:
    """Index of the last HTF bar fully closed at time(s) `at` (-1 if none). The no-lookahead chokepoint.

    A resampled bucket is only closed once its last constituent base bar closed; using known_at
    (open + period) is exact for complete buckets and conservative for truncated ones (Friday close)."""
    return np.searchsorted(htf.known_at, at, side="right") - 1


SESSIONS = (("ASIA", 0, 7), ("LONDON", 7, 12), ("OVERLAP", 12, 16), ("NEWYORK", 16, 21))


def session_of(ts: np.ndarray) -> np.ndarray:
    """UTC-hour sessions. ponytail: fixed UTC hours, DST shifts London/NY opens by 1h half the year."""
    hour = (ts % 86400) // 3600
    out = np.full(len(ts), "OFF", dtype=object)
    for name, a, b in SESSIONS:
        out[(hour >= a) & (hour < b)] = name
    return out


def us_data_window(ts: np.ndarray, minutes_before: int = 30, minutes_after: int = 30) -> tuple[np.ndarray, np.ndarray]:
    """News proxy (no historical calendar exists before 2026-08 in this DB).

    Returns (in_830_window, is_nfp_day): within [-before, +after] minutes of 08:30 New York time on a
    weekday (CPI/NFP/GDP/claims/retail release slot), and first-Friday-of-month (NFP proxy)."""
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    win = np.zeros(len(ts), dtype=bool)
    nfp = np.zeros(len(ts), dtype=bool)
    days: dict[int, tuple[int, bool]] = {}
    for i, x in enumerate(ts):
        d = int(x // 86400)
        if d not in days:
            dt = datetime.fromtimestamp(d * 86400, timezone.utc)
            local = datetime(dt.year, dt.month, dt.day, 8, 30, tzinfo=ny)
            days[d] = (int(local.timestamp()), dt.weekday() == 4 and dt.day <= 7)
        release, is_nfp = days[d]
        win[i] = release - minutes_before * 60 <= x <= release + minutes_after * 60
        nfp[i] = is_nfp
    return win, nfp


def pip(symbol: str) -> float:
    return 0.1 if symbol == "XAUUSD" else (0.01 if symbol.endswith("JPY") else 0.0001)


def holes(bars: Bars, max_gap: int = 2 * 3600) -> list[tuple[int, int]]:
    """Missing-data spans (start, end) in UTC seconds: gaps between consecutive bars longer than `max_gap`
    that are not the normal FX weekend (Friday after 20:00 UTC -> Sunday from 20:00 UTC, <= 54h).
    A trade whose life overlaps one of these cannot be scored honestly (its stop may have been hit unseen)."""
    t = bars.t
    g = np.diff(t)
    end_prev = t[:-1] + TF_SECONDS[bars.tf]
    dow = ((t[:-1] // 86400) + 3) % 7
    weekend = (dow == 4) & ((t[:-1] % 86400) >= 20 * 3600) & (g <= 54 * 3600)
    gold_break = (g <= 2 * 3600)  # XAUUSD's daily 21:00-22:00 pause is within max_gap anyway
    idx = np.flatnonzero((g > max_gap) & ~weekend & ~gold_break)
    return [(int(end_prev[i]), int(t[i + 1])) for i in idx]


def overlaps_hole(spans: list[tuple[int, int]], t0: np.ndarray, t1: np.ndarray) -> np.ndarray:
    """True where [t0, t1] intersects any hole span."""
    out = np.zeros(len(t0), bool)
    for a, b in spans:
        out |= (t0 < b) & (t1 > a)
    return out
