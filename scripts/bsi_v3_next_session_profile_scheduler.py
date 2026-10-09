from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREP_SCRIPT = ROOT / "scripts" / "bsi_v3_prepare_next_session_profile.py"
LOG_PATH = Path(os.getenv("BSI_V3_PROFILE_SCHEDULER_LOG", "/data/research/bsi_v3_profile_scheduler.log"))


def _utc_time(value: str) -> dt_time:
    hour, minute = value.split(":", 1)
    return dt_time(hour=int(hour), minute=int(minute), tzinfo=timezone.utc)


def _stop_after() -> date:
    raw = os.getenv("BSI_V3_PROFILE_SCHEDULER_STOP_AFTER", "2027-12-31")
    return datetime.strptime(raw, "%Y-%m-%d").date()


def _next_run(now: datetime, run_at: dt_time) -> datetime:
    candidate = datetime.combine(now.date(), run_at, tzinfo=timezone.utc)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _run_once(as_of: date) -> None:
    env = os.environ.copy()
    env.setdefault("BSI_V3_PROFILE_OUTPUT_DIR", "/data/research")
    env.setdefault("BSI_V3_PROFILE_CURRENT_PATH", "/data/research/bsi_v3_next_session_profile_current.json")
    args = [sys.executable, str(PREP_SCRIPT), "--as-of", as_of.isoformat()]
    if env.get("BSI_V3_PROFILE_SCHEDULER_REFRESH", "false").strip().lower() in {"1", "true", "yes", "on"}:
        args.append("--refresh")
    logging.warning("running BSI V3 next-session profile prep as_of=%s", as_of.isoformat())
    result = subprocess.run(args, cwd=ROOT, env=env, text=True, capture_output=True)
    if result.returncode != 0:
        logging.error("BSI V3 profile prep failed rc=%s stdout=%s stderr=%s", result.returncode, result.stdout[-2000:], result.stderr[-4000:])
        return
    logging.warning("BSI V3 profile prep completed stdout=%s", result.stdout[-2000:])


def main() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(LOG_PATH),
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    run_at = _utc_time(os.getenv("BSI_V3_PROFILE_SCHEDULER_UTC_TIME", "22:10"))
    stop_after = _stop_after()
    if os.getenv("BSI_V3_PROFILE_SCHEDULER_RUN_ON_START", "true").strip().lower() in {"1", "true", "yes", "on"}:
        _run_once(datetime.now(timezone.utc).date())
    while True:
        now = datetime.now(timezone.utc)
        if now.date() > stop_after:
            logging.warning("BSI V3 profile scheduler stopped at configured date=%s", stop_after.isoformat())
            return
        next_run = _next_run(now, run_at)
        time.sleep(max(1.0, (next_run - now).total_seconds()))
        _run_once(datetime.now(timezone.utc).date())


if __name__ == "__main__":
    main()
