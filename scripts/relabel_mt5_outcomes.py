"""Re-label stored MT5-provider outcomes with the server-clock fix (commit 81f519d).

Before the fix, outcomes._future_candles_with_quality read raw MT5 server time as UTC, so every MT5
label started 2-3h before its entry. Fingerprint entry_times are already UTC (bars_as_of corrected
them), so re-running label_outcome now labels from the right bars.

Scope: provider='MT5' fingerprints except the V3 backfill (bsi-v3-planned-m15conf-v1, which was built
on the raw server clock and is internally consistent as stored).

Safety: the affected outcome rows are copied to BACKUP_TABLE once before anything is overwritten.
Resumable: progress is checkpointed by fingerprint_id in CHECKPOINT.

Run:
  DATABASE_URL=postgresql://openterminalui:openterminalui@localhost:5432/openterminalui \
  python scripts/relabel_mt5_outcomes.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import text  # noqa: E402

from backend.historical_intelligence import outcomes  # noqa: E402
from backend.shared.db import SessionLocal  # noqa: E402

V3_VERSION = "bsi-v3-planned-m15conf-v1"
BACKUP_TABLE = "historical_setup_outcomes_backup_20260926_mt5"
CHECKPOINT = Path("logs/relabel_mt5_checkpoint.txt")
BATCH = 500
SCOPE = "f.provider = 'MT5' and f.strategy_version <> :v3"


def main() -> None:
    with SessionLocal() as db:
        exists = db.execute(text("select to_regclass(:t)"), {"t": BACKUP_TABLE}).scalar()
        if exists is None:
            db.execute(text(
                f"create table {BACKUP_TABLE} as select o.* from historical_setup_outcomes o "
                f"join historical_pattern_fingerprints f using (fingerprint_id) where {SCOPE}"
            ), {"v3": V3_VERSION})
            db.commit()
        backed_up = db.execute(text(f"select count(*) from {BACKUP_TABLE}")).scalar()
        total = db.execute(text(f"select count(*) from historical_pattern_fingerprints f where {SCOPE}"), {"v3": V3_VERSION}).scalar()
    print(f"backup {BACKUP_TABLE}: {backed_up} rows; fingerprints in scope: {total}", flush=True)

    last = CHECKPOINT.read_text().strip() if CHECKPOINT.exists() else ""
    done, t0 = 0, time.perf_counter()
    while True:
        with SessionLocal() as db:
            rows = db.execute(text(
                f"select f.fingerprint_id, f.canonical_symbol, f.direction, f.entry, f.stop_loss, f.take_profit, f.entry_time "
                f"from historical_pattern_fingerprints f where {SCOPE} and f.fingerprint_id > :last "
                f"order by f.fingerprint_id limit :n"
            ), {"v3": V3_VERSION, "last": last, "n": BATCH}).fetchall()
            if not rows:
                break
            for fid, symbol, direction, entry, stop, target, entry_time in rows:
                outcomes.label_outcome(
                    fingerprint_id=fid, canonical_symbol=symbol, broker_symbol=symbol, direction=direction,
                    entry=float(entry), stop_loss=float(stop), take_profit=float(target), entry_time=entry_time,
                    provider="MT5", db=db,
                )
            db.commit()
        last = rows[-1][0]
        CHECKPOINT.write_text(last)
        done += len(rows)
        print(f"  {done}/{total} relabelled, {time.perf_counter() - t0:.0f}s", flush=True)
    print(f"DONE {done} relabelled in {time.perf_counter() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
