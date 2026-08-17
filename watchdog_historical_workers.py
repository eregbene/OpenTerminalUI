"""Lightweight liveness watchdog for the overnight ForexSB historical-intelligence workers.

Not an orchestration platform: just a loop that (a) checks each tracked worker's OS process is
still alive via /proc, (b) if not, decides COMPLETED vs CRASHED using a cheap, worker-specific
check, and (c) if CRASHED, relaunches the exact same command. Every worker's relaunch command is
idempotent/resumable by construction (candle backfill and fingerprint/adaptive generation resume
from DB watermarks; the backtest now checkpoints its own records.json and resumes from it) -- so a
blind restart never redoes completed work or restarts any corpus from zero.

Status/log paths live under /data/historical_intelligence/ (the volume-mounted directory), not
/app -- /app is the container's writable layer and is NOT volume-mounted, so restart-count/
liveness history written there is silently lost on any container recreation (see
docs/HISTORICAL_INTELLIGENCE_PERSISTENCE_AUDIT.md). Status is written every cycle: RUNNING /
COMPLETED / CRASHED_RESTARTED, with last-seen and restart-count, so it can be inspected without
reading raw logs.

A prior STALE_AFTER_SECONDS/STALE status was declared but never actually checked anywhere in the
loop below (last_seen is updated on every cycle a worker is found alive, so there was never a
code path that compared elapsed time against it) -- removed rather than left as dead,
misleading-looking code. Genuine hang/stall detection would need each worker's own log parsed for
a real progress marker (there's no single generic one across all 5 workers), which is a
meaningfully bigger feature than this watchdog's "not an orchestration platform" scope covers.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

CHECK_INTERVAL_SECONDS = 90
STATUS_DIR = "/data/historical_intelligence"
STATUS_PATH = f"{STATUS_DIR}/watchdog_status.json"
WATCHDOG_LOG = f"{STATUS_DIR}/watchdog.log"

# How close a bounded worker's ingested watermark must be to its target end boundary before it's
# considered COMPLETED, not still catching up. Matches the tolerance already used by
# run_forexsb_intelligence_pipeline.py's own _symbol_ready readiness check.
_BOUNDARY_TOLERANCE = timedelta(hours=6)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(msg: str) -> None:
    os.makedirs(STATUS_DIR, exist_ok=True)
    line = f"[{_now()}] {msg}"
    print(line, flush=True)
    with open(WATCHDOG_LOG, "a") as f:
        f.write(line + "\n")


def _forexsb_fingerprint_watermark(canonical_symbol: str):
    """Max entry_time of FOREXSB-provider fingerprints for a symbol, or None if none exist yet.
    Import is deferred into the function so the watchdog can still start (and report status) even
    if the DB isn't reachable yet at process startup."""
    return _fingerprint_watermark(canonical_symbol, provider="FOREXSB")


def _fingerprint_watermark(canonical_symbol: str, *, provider: str):
    """Max entry_time of `provider`-sourced fingerprints for a symbol, or None if none exist yet."""
    from sqlalchemy import func

    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM
    from backend.shared.db import SessionLocal

    with SessionLocal() as db:
        return db.query(func.max(HistoricalPatternFingerprintORM.entry_time)).filter(
            HistoricalPatternFingerprintORM.canonical_symbol == canonical_symbol,
            HistoricalPatternFingerprintORM.provider == provider,
        ).scalar()


def _fingerprint_span_within_range(canonical_symbol: str, *, provider: str, window_start: datetime, window_end: datetime):
    """(min entry_time, max entry_time, count) of `provider`-sourced fingerprints STRICTLY WITHIN
    [window_start, window_end). Returns the actual span, not just a max -- a bare max is NOT a
    safe completion signal for a worker whose provider+symbol combination can also be touched by
    something else near the window's edge (e.g. the live trading cycle's very first real
    MT5-provider EURUSD fingerprint happens to sit within _BOUNDARY_TOLERANCE of a past gap-fill
    window's end purely by coincidence -- an unbounded-or-max-only check reads that ONE stray
    edge fingerprint as "gap filled" when the count is 1 and nothing between window_start and
    window_end was ever actually walked. Requiring the MIN to also be near window_start proves
    genuine start-to-end coverage, not a boundary artifact."""
    from sqlalchemy import func

    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM
    from backend.shared.db import SessionLocal

    with SessionLocal() as db:
        return db.query(
            func.min(HistoricalPatternFingerprintORM.entry_time),
            func.max(HistoricalPatternFingerprintORM.entry_time),
            func.count(),
        ).filter(
            HistoricalPatternFingerprintORM.canonical_symbol == canonical_symbol,
            HistoricalPatternFingerprintORM.provider == provider,
            HistoricalPatternFingerprintORM.entry_time >= window_start,
            HistoricalPatternFingerprintORM.entry_time < window_end,
        ).first()


def _bounded_bulk_replay_complete(canonical_symbol: str, *, provider: str, window_start: datetime, window_end: datetime, min_count: int = 100):
    """min_count (not just an earliest-near-window_start check) is what actually rules out the
    single-stray-fingerprint false positive this function was originally written to catch (see
    git history) -- real, quiet, genuinely candidate-free stretches of MANY hours turned out to
    be normal at BOTH ends of a real gap-fill window (confirmed: ~13h at this window's start,
    ~17h at its end, neither an error), so requiring earliest to land within _BOUNDARY_TOLERANCE
    of window_start is too strict and would misreport real, substantial, genuine coverage as
    incomplete forever. A large persisted count plus reaching near window_end is strong enough
    evidence on its own; one coincidental edge fingerprint could never satisfy min_count."""
    def _check() -> bool:
        try:
            earliest, latest, count = _fingerprint_span_within_range(canonical_symbol, provider=provider, window_start=window_start, window_end=window_end)
        except Exception:
            return False
        if earliest is None or latest is None or not count or count < min_count:
            return False
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        return latest >= window_end - _BOUNDARY_TOLERANCE
    return _check


def _bulk_replay_complete(canonical_symbol: str, target_end: datetime, *, provider: str = "FOREXSB"):
    def _check() -> bool:
        try:
            watermark = _fingerprint_watermark(canonical_symbol, provider=provider)
        except Exception:
            return False
        if watermark is None:
            return False
        if watermark.tzinfo is None:
            watermark = watermark.replace(tzinfo=timezone.utc)
        return watermark >= target_end - _BOUNDARY_TOLERANCE
    return _check


# Backwards-compatible alias -- existing WORKERS entries below reference this name.
_bulk_replay_forexsb_complete = _bulk_replay_complete


def _eurusd_adaptive_backfill_perpetual() -> bool:
    """Intentionally always False: unlike the bulk_replay workers above, this worker's relaunch
    command passes no `provider`/end boundary -- by design it re-scans ALL of EURUSD's fingerprints
    (both the still-growing ForexSB historical corpus and the continuously-arriving live MT5
    corpus) every time it runs, to backfill adaptive states for whatever's newly available. It has
    no natural end state while either upstream feed is still active, so it is meant to be
    relaunched indefinitely, not a bug to be "fixed" into reporting COMPLETED."""
    return False


def _proc_cmdlines() -> dict[int, str]:
    out = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                raw = f.read()
        except OSError:
            continue
        cmd = raw.replace(b"\x00", b" ").decode(errors="replace").strip()
        if cmd:
            out[pid] = cmd
    return out


def _is_alive(match_substrings: list[str], cmdlines: dict[int, str]) -> int | None:
    for pid, cmd in cmdlines.items():
        if all(s in cmd for s in match_substrings):
            return pid
    return None


def _spawn(shell_cmd: str, log_path: str) -> None:
    full = f"cd /app && ({shell_cmd}) >> {log_path} 2>&1"
    subprocess.Popen(
        ["setsid", "sh", "-c", full],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _backtest_complete() -> bool:
    try:
        with open(f"{STATUS_DIR}/eurusd_backtest_progress.json") as f:
            p = json.load(f)
        return p.get("evaluated", 0) >= p.get("total", 1)
    except (OSError, json.JSONDecodeError):
        return False


def _backfill_complete() -> bool:
    try:
        with open(f"{STATUS_DIR}/forexsb_backfill.log") as f:
            content = f.read()
        return "ALL SYMBOLS COMPLETE" in content
    except OSError:
        return False


# EURUSD's ForexSB bulk-replay walk is bounded to stop exactly where MT5's own native corpus
# starts (run_forexsb_backfill.py's MT5_M15_START["EURUSD"]); GBPUSD's boundary is later since its
# own native MT5 corpus starts later. Kept here (not imported) because run_forexsb_backfill.py's
# MT5_M15_START dict has entries for symbols this watchdog doesn't run a bulk_replay worker for at
# all -- importing it would suggest a broader coupling than actually exists.
_EURUSD_FOREXSB_END = datetime(2022, 8, 4, 22, 45, tzinfo=timezone.utc)
_GBPUSD_FOREXSB_END = datetime(2024, 8, 7, 11, 15, tzinfo=timezone.utc)
# EURUSD's MT5-provider fingerprint gap-fill window (2026-08-17: closes the ~2-year hole between
# ForexSB's end boundary and when live-trading fingerprint generation began). Originally ended at
# 2024-08-07T00:00 (a day of margin before the live corpus's first fingerprint, to dodge a
# separate edge-collision bug -- see git history). That end date turned out to sit inside a real
# ~17-hour stretch (2024-08-06 06:45 -> 2024-08-07 00:00) where every strategy family genuinely
# produced zero candidates -- confirmed via a direct replay_at() call (status OK, real regime/SMC
# data, families_candidates: []). bulk_replay's checkpoint mechanism only advances on PERSISTED
# fingerprints, so a candidate-free stretch can never be "marked visited" -- the worker re-walked
# those same ~68 empty instants every single restart, forever, without erroring (0 errors, 0 new,
# 0 existing every run). Found after 157 silent restarts. 27,324 real fingerprints WERE
# successfully persisted for 2022-08-05 through 2024-08-06 06:45 (essentially the whole window) --
# the end boundary is pulled back to exactly that achieved watermark so the worker reports
# COMPLETED instead of spinning on a stretch that will never produce anything to persist.
_EURUSD_MT5_GAP_END = datetime(2024, 8, 6, 6, 45, tzinfo=timezone.utc)

# The remaining 8 symbols (all but EURUSD/GBPUSD, which already have dedicated workers above):
# ForexSB M15 candle backfill completed for all 10 symbols on 2026-08-17 (forexsb_multi_symbol_
# backfill), but -- exactly like EURUSD/GBPUSD before this -- no bulk_replay/fingerprint
# generation has ever been run against that history for these 8. start = each symbol's own real
# earliest ForexSB M15 candle (queried directly, 2026-08-17, varies by symbol -- ForexSB's own
# M15 floor is ~2018, not the wider M30/H1/H4 floor of ~2010); end = run_forexsb_backfill.py's own
# MT5_M15_START boundary (where the live-native MT5 corpus begins for that symbol).
_REMAINING_PAIR_WINDOWS = {
    "USDJPY": (datetime(2018, 8, 7, 13, 30, tzinfo=timezone.utc), datetime(2024, 8, 7, 13, 15, tzinfo=timezone.utc)),
    "AUDUSD": (datetime(2018, 8, 7, 11, 0, tzinfo=timezone.utc), datetime(2024, 8, 7, 12, 45, tzinfo=timezone.utc)),
    "USDCAD": (datetime(2018, 8, 7, 8, 0, tzinfo=timezone.utc), datetime(2024, 8, 7, 13, 15, tzinfo=timezone.utc)),
    "USDCHF": (datetime(2018, 8, 6, 19, 30, tzinfo=timezone.utc), datetime(2024, 8, 7, 13, 30, tzinfo=timezone.utc)),
    "NZDUSD": (datetime(2018, 8, 6, 16, 45, tzinfo=timezone.utc), datetime(2024, 8, 7, 13, 15, tzinfo=timezone.utc)),
    "EURJPY": (datetime(2018, 8, 7, 6, 30, tzinfo=timezone.utc), datetime(2024, 8, 7, 12, 15, tzinfo=timezone.utc)),
    "GBPJPY": (datetime(2018, 8, 7, 3, 30, tzinfo=timezone.utc), datetime(2024, 8, 7, 13, 15, tzinfo=timezone.utc)),
    "XAUUSD": (datetime(2018, 2, 28, 3, 30, tzinfo=timezone.utc), datetime(2024, 6, 25, 18, 0, tzinfo=timezone.utc)),
}


def _remaining_pair_bulk_replay_workers() -> dict:
    workers = {}
    for symbol, (start, end) in _REMAINING_PAIR_WINDOWS.items():
        # Match substring is the END date (unique per symbol, like EURUSD/GBPUSD's own workers) --
        # never the start date, which is a real, distinct-per-symbol ForexSB floor here so no
        # cross-worker collision risk exists the way EURUSD's two workers had.
        end_match = f"{end.year},{end.month},{end.day},{end.hour},{end.minute}"
        workers[f"{symbol.lower()}_bulk_replay_forexsb"] = {
            "match": ["replay_symbol_history_fast", end_match],
            "relaunch": (
                "python -c \""
                "import asyncio\n"
                "from datetime import datetime, timezone\n"
                "from backend.historical_intelligence.bulk_replay import replay_symbol_history_fast\n"
                "async def main():\n"
                "    result = await replay_symbol_history_fast(\n"
                f"        canonical_symbol='{symbol}', broker_symbol='{symbol}',\n"
                f"        start=datetime({start.year},{start.month},{start.day},{start.hour},{start.minute},tzinfo=timezone.utc), "
                f"end=datetime({end.year},{end.month},{end.day},{end.hour},{end.minute},tzinfo=timezone.utc),\n"
                "        provider='FOREXSB',\n"
                "    )\n"
                f"    print('{symbol} FOREXSB bulk_replay result:', result, flush=True)\n"
                "asyncio.run(main())\n"
                "\""
            ),
            "log": f"{STATUS_DIR}/{symbol.lower()}_bulk_replay.log",
            "complete_check": _bounded_bulk_replay_complete(symbol, provider="FOREXSB", window_start=start, window_end=end),
        }
    return workers


WORKERS = {
    "eurusd_backtest": {
        "match": ["run_eurusd_backtest.py"],
        "relaunch": "python run_eurusd_backtest.py",
        "log": f"{STATUS_DIR}/eurusd_backtest_run.log",
        "complete_check": _backtest_complete,
    },
    "eurusd_bulk_replay_forexsb": {
        "match": ["replay_symbol_history_fast", "FOREXSB"],
        "relaunch": (
            "python -c \""
            "import asyncio\n"
            "from datetime import datetime, timezone\n"
            "from backend.historical_intelligence.bulk_replay import replay_symbol_history_fast\n"
            "async def main():\n"
            "    result = await replay_symbol_history_fast(\n"
            "        canonical_symbol='EURUSD', broker_symbol='EURUSD',\n"
            "        start=datetime(2018,8,7,7,15,tzinfo=timezone.utc), end=datetime(2022,8,4,22,45,tzinfo=timezone.utc),\n"
            "        provider='FOREXSB',\n"
            "    )\n"
            "    print('EURUSD FOREXSB bulk_replay result:', result, flush=True)\n"
            "asyncio.run(main())\n"
            "\""
        ),
        "log": f"{STATUS_DIR}/eurusd_bulk_replay.log",
        "complete_check": _bulk_replay_forexsb_complete("EURUSD", _EURUSD_FOREXSB_END),
    },
    "eurusd_bulk_replay_mt5_gap": {
        # Closes a real gap found 2026-08-17: MT5-native EURUSD M15 candles exist continuously
        # from 2022-08-04 (MT5_M15_START) onward, but fingerprint generation against them
        # (bulk_replay, provider=MT5) only ever started from 2024-08-08 -- the date live trading
        # itself began -- never backfilled for the ~2-year window in between, even though the
        # raw candle data for it has been sitting in mt5_canonical_candles the whole time. This
        # worker walks that gap with the SAME resumable, idempotent replay_symbol_history_fast
        # used for the ForexSB-era corpus, just with provider="MT5" and a narrower date range.
        # Match substring is the literal END-date args (2024,8,6,6,45) -- NOT the start date:
        # this worker's start (2022,8,4,22,45) is deliberately identical to eurusd_bulk_replay_
        # forexsb's END date (chronological continuity), so matching on start caused a real
        # false-positive collision (both processes' cmdlines contained that substring, and
        # _is_alive() picked whichever PID /proc happened to enumerate first for BOTH worker
        # entries -- found and fixed before this worker was ever correctly tracked). The end date
        # is unique to this worker. broker_symbol must stay the REAL 'EURUSD' (that's what
        # candles are actually keyed under in mt5_canonical_candles); a fake symbol there would
        # silently return zero candles.
        "match": ["replay_symbol_history_fast", "2024,8,6,6,45"],
        "relaunch": (
            "python -c \""
            "import asyncio\n"
            "from datetime import datetime, timezone\n"
            "from backend.historical_intelligence.bulk_replay import replay_symbol_history_fast\n"
            "async def main():\n"
            "    result = await replay_symbol_history_fast(\n"
            "        canonical_symbol='EURUSD', broker_symbol='EURUSD',\n"
            "        start=datetime(2022,8,4,22,45,tzinfo=timezone.utc), end=datetime(2024,8,6,6,45,tzinfo=timezone.utc),\n"
            "        provider='MT5',\n"
            "    )\n"
            "    print('EURUSD MT5-gap bulk_replay result:', result, flush=True)\n"
            "asyncio.run(main())\n"
            "\""
        ),
        "log": f"{STATUS_DIR}/eurusd_bulk_replay_mt5_gap.log",
        "complete_check": _bounded_bulk_replay_complete(
            "EURUSD", provider="MT5",
            window_start=datetime(2022, 8, 4, 22, 45, tzinfo=timezone.utc),
            window_end=_EURUSD_MT5_GAP_END,
        ),
    },
    "eurusd_adaptive_backfill": {
        "match": ["run_adaptive_backfill", "EURUSD"],
        "relaunch": (
            "python -c \""
            "import asyncio\n"
            "from backend.historical_intelligence.adaptive_backfill import run_adaptive_backfill\n"
            "async def main():\n"
            "    result = await run_adaptive_backfill(canonical_symbol='EURUSD', resume=False)\n"
            "    print('EURUSD adaptive backfill result:', result, flush=True)\n"
            "asyncio.run(main())\n"
            "\""
        ),
        "log": f"{STATUS_DIR}/eurusd_adaptive_backfill.log",
        "complete_check": _eurusd_adaptive_backfill_perpetual,
    },
    "gbpusd_bulk_replay_forexsb": {
        "match": ["replay_symbol_history_fast", "GBPUSD"],
        "relaunch": (
            "python -c \""
            "import asyncio\n"
            "from datetime import datetime, timezone\n"
            "from backend.historical_intelligence.bulk_replay import replay_symbol_history_fast\n"
            "async def main():\n"
            "    result = await replay_symbol_history_fast(\n"
            "        canonical_symbol='GBPUSD', broker_symbol='GBPUSD',\n"
            "        start=datetime(2018,8,6,20,45,tzinfo=timezone.utc), end=datetime(2024,8,7,11,15,tzinfo=timezone.utc),\n"
            "        provider='FOREXSB',\n"
            "    )\n"
            "    print('GBPUSD FOREXSB bulk_replay result:', result, flush=True)\n"
            "asyncio.run(main())\n"
            "\""
        ),
        "log": f"{STATUS_DIR}/gbpusd_bulk_replay.log",
        "complete_check": _bulk_replay_forexsb_complete("GBPUSD", _GBPUSD_FOREXSB_END),
    },
    "forexsb_multi_symbol_backfill": {
        "match": ["run_forexsb_backfill.py"],
        "relaunch": "python run_forexsb_backfill.py USDJPY AUDUSD USDCAD USDCHF NZDUSD EURJPY GBPJPY XAUUSD",
        "log": f"{STATUS_DIR}/forexsb_backfill.log",
        "complete_check": _backfill_complete,
    },
    **_remaining_pair_bulk_replay_workers(),
}


def _load_prior_status() -> dict[str, dict]:
    """Preserve restart counts across watchdog restarts (e.g. adding a new tracked worker) --
    a fresh in-memory dict would silently reset tonight's real restart history to 0, losing the
    honest record of what actually crashed."""
    try:
        with open(STATUS_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def main() -> None:
    prior = _load_prior_status()
    status: dict[str, dict] = {}
    for name in WORKERS:
        old = prior.get(name)
        if old and old.get("status") == "COMPLETED":
            # A prior COMPLETED verdict must stick across watchdog restarts -- previously this
            # branch was unreachable (the condition below caught COMPLETED too, via its `else`),
            # which silently reset a genuinely-finished worker back to RUNNING and caused a real
            # incident: eurusd_backtest got relaunched from scratch on a watchdog restart even
            # though it had already evaluated all 4,542 candidates.
            status[name] = old
        elif old:
            status[name] = {**old, "status": "RUNNING", "last_seen": _now()}
        else:
            status[name] = {"status": "RUNNING", "restarts": 0, "last_seen": _now(), "last_restart": None}
    _log(f"Watchdog (re)started, tracking {list(WORKERS)}, restart counts preserved from prior status where available")

    while True:
        cmdlines = _proc_cmdlines()
        any_active = False
        for name, spec in WORKERS.items():
            st = status[name]
            if st["status"] == "COMPLETED":
                continue
            pid = _is_alive(spec["match"], cmdlines)
            if pid is not None:
                st["status"] = "RUNNING"
                st["pid"] = pid
                st["last_seen"] = _now()
                any_active = True
                continue
            # process not found -- COMPLETED or CRASHED?
            if spec["complete_check"]():
                st["status"] = "COMPLETED"
                st["last_seen"] = _now()
                _log(f"{name}: detected COMPLETED (no restart)")
                continue
            st["restarts"] += 1
            st["status"] = "CRASHED_RESTARTED"
            st["last_restart"] = _now()
            _log(f"{name}: process gone, not complete -> RESTARTING (restart #{st['restarts']})")
            _spawn(spec["relaunch"], spec["log"])
            any_active = True

        with open(STATUS_PATH + ".tmp", "w") as f:
            json.dump(status, f, indent=2)
        os.replace(STATUS_PATH + ".tmp", STATUS_PATH)

        if not any_active:
            _log("All tracked workers COMPLETED. Watchdog exiting.")
            break

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
