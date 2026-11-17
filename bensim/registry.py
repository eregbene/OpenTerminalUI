"""Experiment registry (audit P1, 2026-11-03): one append-only JSON line per experiment, never rewritten.

    python -m bensim.registry import     # once: index every historical run (DB backtest_runs + research logs), read-only
    python -m bensim.registry count      # trials so far (the N the Deflated Sharpe Ratio must use)

data/experiments/registry.jsonl. A record: id, kind, created_at, code (git commit + dirty flag + hash of bensim/*.py),
command, strategy, params, exec, symbols, period, data fingerprint, outputs, summary. Historical imports keep the
original numbers and point at where they live; nothing in the DB or logs is changed or moved.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REG = Path("data/experiments/registry.jsonl")


def code_version() -> dict:
    def git(*a):
        return subprocess.run(["git", *a], capture_output=True, text=True).stdout.strip()
    h = hashlib.sha256()
    for f in sorted(Path("bensim").glob("*.py")):
        h.update(f.name.encode() + f.read_bytes())
    return {"commit": git("rev-parse", "--short", "HEAD"), "dirty": bool(git("status", "--porcelain", "bensim")),
            "bensim_sha256": h.hexdigest()[:16]}


def data_fingerprint(symbols: list[str], start: str, end: str) -> dict:
    from . import db
    with db.connect() as c:
        rows = c.execute("""select symbol, count(*), min(ts)::text, max(ts)::text, sum(close)::text from candles
                            where tf='M5' and symbol = any(%s) and ts >= %s and ts < %s group by 1 order by 1""",
                         (symbols, start, end)).fetchall()
    return {r[0]: {"bars": r[1], "first": r[2], "last": r[3], "close_sum": r[4]} for r in rows}


def append(rec: dict) -> str:
    REG.parent.mkdir(parents=True, exist_ok=True)
    rec = {"id": rec.get("id") or f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{hashlib.sha1(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()[:8]}",
           "created_at": datetime.now(timezone.utc).isoformat(), **rec}
    with REG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec["id"]


def records() -> list[dict]:
    return [json.loads(x) for x in REG.read_text(encoding="utf-8").splitlines() if x.strip()] if REG.exists() else []


def trial_sharpes(min_trades: int = 30) -> list[float]:
    """Per-trade Sharpe (t / sqrt(n)) of registered experiments with >= min_trades trades -- the DSR's trial variance.
    Tiny runs are left out of the VARIANCE only (an all-loss run of 4 trades gives SR -36); they still count in N."""
    out = []
    for r in records():
        s = r.get("summary") or {}
        t, n = s.get("t_stat"), s.get("trades")
        if t is not None and n and float(n) >= min_trades:
            out.append(float(t) / float(n) ** 0.5)
    return out


def import_history() -> int:
    if any(r.get("kind", "").startswith("historical") for r in records()):
        print("history already imported")
        return 0
    from . import db
    n = 0
    with db.connect() as c:
        for rid, created, syms, a, b, params, ex, summ in c.execute(
                "select id, created_at, symbols, start_date, end_date, params, exec, summary from backtest_runs order by id").fetchall():
            append({"id": f"db-run-{rid}", "kind": "historical backtest_run (imported read-only)", "original_created_at": created.isoformat(),
                    "symbols": syms, "period": [a, b], "params": params, "exec": ex,
                    "summary": {k: (summ or {}).get(k) for k in ("trades", "avg_r", "t_stat", "profit_factor", "total_r", "max_dd_pct")},
                    "outputs": {"db": f"backtest_runs.id={rid}"}})
            n += 1
    for f in sorted(list(Path("logs").glob("*.log")) + list(Path("data").glob("*.log"))):
        append({"id": f"log-{f.as_posix()}", "kind": "historical research log (imported, unsummarised)",
                "original_mtime": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat(),
                "outputs": {"log": f.as_posix(), "bytes": f.stat().st_size}})
        n += 1
    return n


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "count"
    if cmd == "import":
        print("imported", import_history())
    rs = records()
    print(f"{len(rs)} experiments registered; {len(trial_sharpes())} with a per-trade Sharpe")
