"""One-off backfill (NOT part of the permanent codebase -- analysis/reporting script only, per
the same scratch_*.py convention already used this session): applies execution_costs.py's
cost-provenance model to the EXISTING ~2M-row historical corpus.

Read-only against the corpus's own geometry (entry/stop_loss from HistoricalPatternFingerprintORM,
outcome_r from HistoricalSetupOutcomeORM); writes ONLY the new cost-provenance columns this
phase's migration (0068) added. Never touches outcome_r, resolution_status, data_quality, or
anything else historical_intelligence's live-influencing statistics already depend on.

Finding baked into this script's design: the entire corpus was built via bulk_replay.py's
RECONSTRUCTED-source path, which NEVER supplies real_spread (that only exists for live decision-
snapshot SNAPSHOT-source replay). So HISTORICAL_ESTIMATE tier can never fire for this corpus either
(there are zero genuine OBSERVED rows anywhere in it to seed the point-in-time index) -- the only
way to get a non-UNKNOWN net_r for this corpus is CONFIG_FALLBACK. MT5_HISTORICAL_SPREAD_FALLBACK_
<SYMBOL> is set below from REAL median spread observations (mt5_decision_snapshots.spread, n=559-
1675 per symbol) captured by actual recent live decision cycles -- genuine broker-observed data,
just representative of CURRENT market conditions rather than period-exact for a 2018 trade. This
is disclosed explicitly in the report as a known limitation, not hidden.
"""
from __future__ import annotations

import os
import time

# Real median spreads observed via live decision snapshots (see the query this script's author
# ran interactively: SELECT canonical_symbol, median(spread) FROM mt5_decision_snapshots WHERE
# spread > 0 GROUP BY canonical_symbol; n=559-1675 samples per symbol, 2026-08 window).
_REAL_MEDIAN_SPREAD = {
    "XAUUSD": 0.19, "EURJPY": 0.003, "USDCAD": 0.00001, "USDJPY": 0.004, "EURUSD": 0.00001,
    "AUDUSD": 0.00001, "GBPJPY": 0.008, "USDCHF": 0.00002, "GBPUSD": 0.00002, "NZDUSD": 0.00001,
}
for _sym, _val in _REAL_MEDIAN_SPREAD.items():
    os.environ[f"MT5_HISTORICAL_SPREAD_FALLBACK_{_sym}"] = str(_val)
os.environ.setdefault("MT5_COMMISSION_MODE", "BROKER_REPORTED")

from sqlalchemy import text  # noqa: E402

from backend.historical_intelligence import execution_costs  # noqa: E402
from backend.shared.db import engine  # noqa: E402

_CHUNK = 20000


def backfill() -> None:
    execution_costs.invalidate_spread_index()
    execution_costs.invalidate_commission_cache()
    commission = execution_costs.resolve_commission_cost_r()
    print(f"commission resolved once: {commission.commission_cost_r} ({commission.provenance})", flush=True)

    # Skipping an exact COUNT(*) over the full JOIN -- measured as a slow, expensive scan on its
    # own (tens of seconds) that adds nothing to the actual backfill logic. Progress is reported
    # incrementally instead (processed count + rate), which is all this needs.
    processed = 0
    start = time.time()
    last_fp = ""
    while True:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT hso.outcome_id, hpf.fingerprint_id, hpf.canonical_symbol, hpf.entry_time, "
                "hpf.entry, hpf.stop_loss, hso.outcome_r "
                "FROM historical_setup_outcomes hso "
                "JOIN historical_pattern_fingerprints hpf ON hpf.fingerprint_id = hso.fingerprint_id "
                "WHERE hso.resolution_status='RESOLVED' AND hso.outcome_r IS NOT NULL "
                "AND hso.spread_cost_provenance = 'UNKNOWN' AND hpf.fingerprint_id > :last_fp "
                "ORDER BY hpf.fingerprint_id ASC LIMIT :lim"
            ), {"last_fp": last_fp, "lim": _CHUNK}).fetchall()
        if not rows:
            break

        updates = []
        for outcome_id, fingerprint_id, symbol, entry_time, entry, stop_loss, outcome_r in rows:
            risk = abs(float(entry) - float(stop_loss))
            if risk <= 0:
                continue
            spread = execution_costs.resolve_spread_cost(canonical_symbol=symbol, entry_time=entry_time, risk=risk, real_spread=None)
            net_r = None
            if spread.spread_cost_r is not None:
                net_r = float(outcome_r) - spread.spread_cost_r - (commission.commission_cost_r or 0.0)
            updates.append({
                "id": outcome_id, "net_r": round(net_r, 4) if net_r is not None else None,
                "real_spread": spread.real_spread_price, "spread_cost_r": spread.spread_cost_r,
                "spread_prov": spread.provenance, "commission_cost_r": commission.commission_cost_r,
                "commission_prov": commission.provenance,
            })
            last_fp = fingerprint_id

        if updates:
            with engine.begin() as conn:
                conn.execute(text(
                    "UPDATE historical_setup_outcomes SET "
                    "net_outcome_r = :net_r, real_spread_price = :real_spread, spread_cost_r = :spread_cost_r, "
                    "spread_cost_provenance = :spread_prov, commission_cost_r = :commission_cost_r, "
                    "commission_cost_provenance = :commission_prov "
                    "WHERE outcome_id = :id"
                ), updates)

        processed += len(rows)
        elapsed = time.time() - start
        rate = processed / elapsed if elapsed > 0 else 0
        print(f"processed={processed} rate={rate:.0f}/s elapsed={elapsed:.0f}s", flush=True)

    print(f"DONE processed={processed} elapsed={time.time()-start:.0f}s", flush=True)


if __name__ == "__main__":
    backfill()
