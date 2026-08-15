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
    from sqlalchemy import func

    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM
    from backend.shared.db import SessionLocal

    with SessionLocal() as db:
        return db.query(func.max(HistoricalPatternFingerprintORM.entry_time)).filter(
            HistoricalPatternFingerprintORM.canonical_symbol == canonical_symbol,
            HistoricalPatternFingerprintORM.provider == "FOREXSB",
        ).scalar()


def _bulk_replay_forexsb_complete(canonical_symbol: str, target_end: datetime):
    def _check() -> bool:
        try:
            watermark = _forexsb_fingerprint_watermark(canonical_symbol)
        except Exception:
            return False
        if watermark is None:
            return False
        if watermark.tzinfo is None:
            watermark = watermark.replace(tzinfo=timezone.utc)
        return watermark >= target_end - _BOUNDARY_TOLERANCE
    return _check


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
