#!/usr/bin/env sh
set -e

if [ "${SKIP_MIGRATIONS:-0}" != "1" ]; then
  alembic -c backend/alembic.ini upgrade head
fi

# Seed an initial admin account on first launch so login works out of the box.
# Idempotent: skips automatically once any user exists or if no password is set.
python scripts/seed_admin.py || true

# Optional: supervise the long-running ForexSB/adaptive-backfill historical-intelligence
# workers so they resume automatically after a container recreation instead of needing a
# manual relaunch. Off by default (see ENABLE_HISTORICAL_WATCHDOG in docker-compose.yml) --
# only starts new work if none of the tracked workers are already alive; safe to enable on a
# container that already has some of them running, since it picks up existing PIDs by cmdline
# match rather than assuming it spawned them.
if [ "${ENABLE_HISTORICAL_WATCHDOG:-0}" = "1" ]; then
  mkdir -p /data/historical_intelligence
  python watchdog_historical_workers.py >> /data/historical_intelligence/watchdog_stdout.log 2>&1 &
fi

exec python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
