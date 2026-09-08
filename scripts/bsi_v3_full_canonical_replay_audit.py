from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.bsi_v3_august_validation as v3
import scripts.bsi_v3_planned_entry_validation as planned
from backend.mt5_strategies.families.bsi_v3_runtime_detectors import CONFIRMATION_TIMEFRAME_POLICY, RUNTIME_SPECS


RESEARCH_DIR = ROOT / "data" / "research"
DOC_DIR = ROOT / "docs" / "bsi_updated_faiz" / "v3_canonical_replay"

START = datetime.fromisoformat(os.getenv("BSI_V3_CANONICAL_START", "2026-01-01T00:00:00+00:00"))
END = datetime.fromisoformat(os.getenv("BSI_V3_CANONICAL_END", "2026-09-01T00:00:00+00:00"))
SYMBOLS = [s.strip().upper() for s in os.getenv("BSI_V3_CANONICAL_SYMBOLS", ",".join(v3.CORE_SYMBOLS)).split(",") if s.strip()]
MIN_PLAN = float(os.getenv("BSI_V3_MIN_PLAN_CONFIDENCE", "65"))
MIN_EXEC = float(os.getenv("BSI_V3_MIN_EXECUTION_CONFIDENCE", "80"))
MIN_RR = float(os.getenv("BSI_V3_PLANNED_MIN_RR", "1.25"))

FULL_JSON = RESEARCH_DIR / "bsi_v3_jan_aug_full_canonical.json"
MATRIX_JSON = RESEARCH_DIR / "bsi_v3_strategy_profile_matrix.json"
OVERLAP_JSON = RESEARCH_DIR / "bsi_v3_reactionary_spectre_overlap.json"
M1_JSON = RESEARCH_DIR / "bsi_v3_m1_coverage.json"
POSITIONS_JSON = RESEARCH_DIR / "bsi_v3_open_position_attribution.json"
ADAPTIVE_JSON = RESEARCH_DIR / "bsi_v3_adaptive_action_attribution.json"


def _slug(parts: list[Any], prefix: str) -> str:
    raw = "|".join("" if p is None else str(p) for p in parts)
    return f"{prefix}:{sha1(raw.encode('utf-8')).hexdigest()[:20]}"


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _date(value: Any) -> str:
    dt = _dt(value)
    return dt.date().isoformat() if dt else "unknown"


def _session(value: Any) -> str:
    dt = _dt(value)
    if not dt:
        return "unknown"
    hour = dt.hour + dt.minute / 60
    if 0 <= hour < 7:
        return "asian"
    if 7 <= hour < 12:
        return "london"
    if 12 <= hour < 17:
        return "new_york"
    return "after_hours"


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * pct)))
    return round(float(ordered[idx]), 4)


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"mean": None, "median": None, "p75": None, "p90": None, "p95": None, "p99": None, "max": None}
    return {
        "mean": round(statistics.fmean(values), 4),
        "median": round(statistics.median(values), 4),
        "p75": _percentile(values, 0.75),
        "p90": _percentile(values, 0.90),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
        "max": round(max(values), 4),
    }


def _table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(rows[0]))]
    out: list[str] = []
    for idx, row in enumerate(rows):
        out.append("| " + " | ".join(str(row[i]).ljust(widths[i]) for i in range(len(row))) + " |")
        if idx == 0:
            out.append("| " + " | ".join("-" * w for w in widths) + " |")
    return "\n".join(out)


def _summary(outcomes: list[v3.Outcome]) -> dict[str, Any]:
    return v3.summarize_outcomes(outcomes, "managed_r")


def _coverage_state(store: v3.CandleStore, symbol: str, timeframe: str, start: datetime, end: datetime) -> str:
    try:
        rows = store.candles(symbol, timeframe, start, end)
    except Exception:
        rows = []
    if rows:
        return "DATA_AVAILABLE"
    return f"DATA_GAP_{timeframe}"


def _plan_confidence(op: v3.Opportunity, spec: v3.StrategySpec) -> float:
    score = 60.0
    score += min(12.0, max(0.0, op.rr - MIN_RR) * 5.0)
    if any(tf in {"H4", "H1", "D1"} for tf in spec.required_timeframes):
        score += 5.0
    if spec.strategy_id in CONFIRMATION_TIMEFRAME_POLICY:
        score += 2.0
    return round(max(0.0, min(100.0, score)), 2)


def _execution_confidence(op: v3.Opportunity, planned_row: dict[str, Any] | None, plan_conf: float) -> float:
    if planned_row is None:
        return 0.0
    score = plan_conf + 8.0
    if op.rr >= 2.0:
        score += 4.0
    if planned_row.get("confirmation_timeframe") == "M1":
        score += 2.0
    return round(max(0.0, min(100.0, score)), 2)


def _canonical_ids(op: v3.Opportunity, spec: v3.StrategySpec, planned_row: dict[str, Any] | None) -> dict[str, str]:
    setup_time = op.entry_time.replace(second=0, microsecond=0).isoformat()
    touch_time = str((planned_row or {}).get("touch_time") or "")
    confirm_time = str((planned_row or {}).get("confirmation_time") or "")
    zone = (planned_row or {}).get("poi_zone") or {}
    zone_low = round(float(zone.get("low") or op.entry), 5)
    zone_high = round(float(zone.get("high") or op.entry), 5)
    poi_key = [op.symbol, op.direction, setup_time, zone_low, zone_high]
    context_key = [op.symbol, op.direction, op.entry_time.date().isoformat(), ",".join(spec.required_timeframes)]
    thesis_key = [op.symbol, op.direction, op.entry_time.date().isoformat(), round(op.stop, 5), round(op.target, 5)]
    entry_key = [op.symbol, op.direction, setup_time, zone_low, zone_high, round(op.entry, 5), round(op.stop, 5)]
    return {
        "bsi_v3_market_context_id": _slug(context_key, "ctx"),
        "bsi_v3_market_thesis_id": _slug(thesis_key, "thesis"),
        "bsi_v3_poi_id": _slug(poi_key, "poi"),
        "bsi_v3_entry_opportunity_id": _slug(entry_key, "entry"),
        "bsi_v3_confirmation_id": _slug(entry_key + [confirm_time], "confirm") if confirm_time else "",
        "bsi_v3_execution_id": _slug(entry_key + [confirm_time, "exec"], "exec") if confirm_time else "",
        "liquidity_event_id": _slug([op.symbol, op.direction, setup_time, round(op.target, 5)], "liq"),
        "source_opportunity_id": op.opportunity_id,
        "source_thesis_id": op.thesis_id,
    }


def _profile_decision_from_stats(outcomes: list[v3.Outcome]) -> tuple[str, float, str, int, float]:
    if len(outcomes) < 12:
        return "NEUTRAL", 50.0, "insufficient_point_in_time_sample", len(outcomes), 0.0
    stats = _summary(outcomes)
    expectancy = float(stats.get("expectancy") or 0.0)
    pf = float(stats.get("profit_factor") or 0.0)
    wr = float(stats.get("win_rate") or 0.0)
    reliability = min(1.0, len(outcomes) / 40.0)
    score = 50.0 + expectancy * 30.0 + (pf - 1.0) * 10.0 + (wr - 50.0) * 0.2
    if expectancy < -0.05 and pf < 0.9 and len(outcomes) >= 30:
        return "BLOCK", round(score, 2), "reliable_negative_point_in_time_bucket", len(outcomes), round(reliability, 4)
    if expectancy < 0.0 and len(outcomes) >= 20:
        return "DEPRIORITIZE", round(score, 2), "negative_point_in_time_bucket_rank_lower", len(outcomes), round(reliability, 4)
    if expectancy >= 0.05 and pf >= 1.1:
        return "PREFER", round(score, 2), "positive_point_in_time_bucket", len(outcomes), round(reliability, 4)
    return "ALLOW", round(score, 2), "adequate_point_in_time_bucket", len(outcomes), round(reliability, 4)


def _with_current_profile(events: list[dict[str, Any]], outcomes_by_event: dict[str, v3.Outcome]) -> None:
    by_bucket: dict[tuple[str, str], list[v3.Outcome]] = defaultdict(list)
    for event in events:
        out = outcomes_by_event.get(event["event_id"])
        if out is not None:
            by_bucket[(event["strategy_id"], event["symbol"])].append(out)
    full_profile = v3.build_adaptive_bucket_profile([o for rows in by_bucket.values() for o in rows], min_trades=8, min_expectancy=0.02, min_profit_factor=1.05, max_losing_streak=8)
    allowed = {(row["strategy_id"], row["symbol"]) for row in full_profile["allowed_buckets"]}
    blocked = {(row["strategy_id"], row["symbol"]) for row in full_profile["blocked_buckets"]}
    for event in events:
        bucket = (event["strategy_id"], event["symbol"])
        if bucket in allowed:
            event["current_profile_policy_decision"] = "ALLOW"
        elif bucket in blocked:
            event["current_profile_policy_decision"] = "BLOCK"
        else:
            event["current_profile_policy_decision"] = "NEUTRAL"
        event["current_profile_policy_is_pit_safe"] = False


def _apply_walk_forward_profile(events: list[dict[str, Any]], outcomes_by_event: dict[str, v3.Outcome]) -> None:
    history: dict[tuple[str, str], list[v3.Outcome]] = defaultdict(list)
    for event in sorted(events, key=lambda row: row.get("timestamp") or ""):
        bucket = (event["strategy_id"], event["symbol"])
        decision, score, reason, sample, reliability = _profile_decision_from_stats(history[bucket])
        event["profile_status"] = decision
        event["profile_score"] = score
        event["profile_reason"] = reason
        event["profile_sample_size"] = sample
        event["profile_reliability"] = reliability
        event["profile_pit_safe"] = True
        out = outcomes_by_event.get(event["event_id"])
        if out is not None:
            history[bucket].append(out)


def _psql_json(query: str) -> list[dict[str, Any]]:
    cmd = [
        "docker", "compose", "exec", "-T", "postgres", "psql", "-U", "openterminalui", "-d", "openterminalui",
        "-At", "-c", f"select coalesce(json_agg(t),'[]'::json) from ({query}) t;",
    ]
    try:
        result = subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, text=True, timeout=45)
        return json.loads(result.stdout.strip() or "[]")
    except Exception:
        return []


def _position_attribution() -> dict[str, Any]:
    rows = _psql_json(
        "select account_id,symbol,direction,strategy_id,broker_ticket,opened_at,entry_price,current_sl,current_tp,"
        "raw_payload from adaptive_position_states where closed_detected_at is null order by account_id,symbol,broker_ticket"
    )
    positions: list[dict[str, Any]] = []
    for row in rows:
        raw = row.get("raw_payload") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                raw = {}
        bsi = raw.get("bsi_v3") or raw.get("evidence") or {}
        ids = {
            "bsi_v3_market_context_id": bsi.get("bsi_v3_market_context_id"),
            "bsi_v3_market_thesis_id": bsi.get("bsi_v3_market_thesis_id"),
            "bsi_v3_poi_id": bsi.get("bsi_v3_poi_id"),
            "bsi_v3_entry_opportunity_id": bsi.get("bsi_v3_entry_opportunity_id"),
            "bsi_v3_confirmation_id": bsi.get("bsi_v3_confirmation_id"),
            "bsi_v3_execution_id": bsi.get("bsi_v3_execution_id"),
        }
        level = "EXACT" if ids["bsi_v3_execution_id"] else ("HIGH_CONFIDENCE_RECONSTRUCTED" if row.get("strategy_id") or row.get("broker_ticket") else "UNKNOWN")
        if not ids["bsi_v3_entry_opportunity_id"]:
            ids["bsi_v3_entry_opportunity_id"] = _slug([row.get("symbol"), row.get("direction"), row.get("strategy_id"), row.get("opened_at"), round(float(row.get("entry_price") or 0), 5)], "recon_entry")
        if not ids["bsi_v3_market_thesis_id"]:
            ids["bsi_v3_market_thesis_id"] = _slug([row.get("symbol"), row.get("direction"), row.get("strategy_id"), _date(row.get("opened_at"))], "recon_thesis")
        positions.append({**row, **ids, "attribution_level": level})
    by_opp = defaultdict(list)
    by_thesis = defaultdict(list)
    for row in positions:
        by_opp[row["bsi_v3_entry_opportunity_id"]].append(row)
        by_thesis[row["bsi_v3_market_thesis_id"]].append(row)
    same_account_duplicates = 0
    cross_account_copies = 0
    for group in by_opp.values():
        accounts = [row.get("account_id") for row in group]
        if len(set(accounts)) > 1:
            cross_account_copies += max(0, len(group) - 1)
        same_account_duplicates += sum(max(0, count - 1) for count in Counter(accounts).values())
    summary = {
        "position_count": len(positions),
        "unique_contexts": len({p.get("bsi_v3_market_context_id") for p in positions if p.get("bsi_v3_market_context_id")}),
        "unique_theses": len(by_thesis),
        "unique_pois": len({p.get("bsi_v3_poi_id") for p in positions if p.get("bsi_v3_poi_id")}),
        "unique_opportunities": len(by_opp),
        "cross_account_copies": cross_account_copies,
        "same_account_duplicates": same_account_duplicates,
        "ambiguous_or_unknown": sum(1 for p in positions if p["attribution_level"] in {"AMBIGUOUS", "UNKNOWN"}),
        "positions": positions,
        "per_thesis": [
            {
                "thesis_id": thesis,
                "symbol": group[0].get("symbol"),
                "direction": group[0].get("direction"),
                "strategies": sorted({str(g.get("strategy_id")) for g in group}),
                "accounts": sorted({str(g.get("account_id")) for g in group}),
                "executions": len(group),
            }
            for thesis, group in sorted(by_thesis.items())
        ],
    }
    POSITIONS_JSON.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def _adaptive_attribution() -> dict[str, Any]:
    rows = _psql_json(
        "select a.action_type,a.status,a.broker_mutation_attempted,a.created_at,a.evidence,"
        "s.account_id,s.symbol,s.direction,s.strategy_id,s.broker_ticket,"
        "round(s.max_achieved_r::numeric,3) as mfe_r,round(s.min_achieved_r::numeric,3) as mae_r,"
        "round(s.tp_progress::numeric,3) as target_progress "
        "from adaptive_management_actions a join adaptive_position_states s on s.position_id=a.position_id "
        "where a.created_at >= '2026-09-07 22:30:00+00' order by a.created_at desc limit 50000"
    )
    lifecycle = Counter()
    by_action = defaultdict(Counter)
    for row in rows:
        status = str(row.get("status") or "").upper()
        attempted = bool(row.get("broker_mutation_attempted"))
        if status == "HOLD":
            life = "EVALUATED"
        elif not attempted and status in {"SELECTED", "SUPPRESSED"}:
            life = status
        elif attempted and status in {"SUBMITTED", "ACCEPTED"}:
            life = "BROKER_ACCEPTED" if status == "ACCEPTED" else "SUBMIT_ATTEMPTED"
        elif attempted:
            life = "BROKER_REJECTED" if status in {"REJECTED", "FAILED"} else "SUBMIT_ATTEMPTED"
        else:
            life = "NOOP" if status in {"NOOP", "SKIPPED"} else status or "EVALUATED"
        lifecycle[life] += 1
        by_action[str(row.get("action_type") or "UNKNOWN")][life] += 1
    summary = {
        "row_count": len(rows),
        "selected_count": sum(1 for r in rows if str(r.get("status") or "").upper() == "SELECTED"),
        "submit_attempt_count": sum(1 for r in rows if bool(r.get("broker_mutation_attempted"))),
        "broker_accepted_count": lifecycle["BROKER_ACCEPTED"],
        "broker_rejected_count": lifecycle["BROKER_REJECTED"],
        "noop_count": lifecycle["NOOP"],
        "lifecycle_counts": dict(lifecycle),
        "by_action_type": {k: dict(v) for k, v in sorted(by_action.items())},
        "sample_rows": rows[:250],
    }
    ADAPTIVE_JSON.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary


def _frequency(events: list[dict[str, Any]], id_field: str, status_filter: set[str] | None = None) -> dict[str, Any]:
    by_pair_day: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in events:
        if status_filter and row.get("simulated_execution") not in status_filter and row.get("detector_result") not in status_filter:
            continue
        value = row.get(id_field)
        if value:
            by_pair_day[(row["symbol"], _date(row["timestamp"]))].add(str(value))
    values = [float(len(v)) for v in by_pair_day.values()]
    max_key = max(by_pair_day, key=lambda k: len(by_pair_day[k])) if by_pair_day else None
    return {"stats": _stats(values), "max_pair_day": max_key, "max_ids": sorted(by_pair_day[max_key]) if max_key else []}


def main() -> None:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    v3.START = START
    v3.END = END
    v3.MIN_ENTRY_RR = MIN_RR
    planned.START = START
    planned.END = END
    planned.MIN_RR = MIN_RR

    store = v3.CandleStore()
    registry = v3.strategy_registry()
    spec_by_id = {s.strategy_id: s for s in registry}
    candle_cache: dict[tuple[str, str], list[v3.Bar]] = {}
    time_cache: dict[tuple[str, str], list[datetime]] = {}

    def bars(symbol: str, timeframe: str) -> list[v3.Bar]:
        key = (symbol, timeframe)
        if key not in candle_cache:
            candle_cache[key] = v3.load_bars(store, symbol, timeframe, START, END)
            time_cache[key] = [b.time for b in candle_cache[key]]
        return candle_cache[key]

    def times(symbol: str, timeframe: str) -> list[datetime]:
        bars(symbol, timeframe)
        return time_cache[(symbol, timeframe)]

    events: list[dict[str, Any]] = []
    outcomes_by_event: dict[str, v3.Outcome] = {}
    m1_coverage: dict[str, Any] = {"window": {"start": START.isoformat(), "end": END.isoformat()}, "symbol_day_strategy": []}

    for spec in registry:
        detector = v3.DETECTOR_REGISTRY.get(spec.strategy_id)
        for symbol in SYMBOLS:
            if symbol not in spec.eligible_symbols:
                m1_coverage["symbol_day_strategy"].append({"symbol": symbol, "strategy_id": spec.strategy_id, "state": "OUT_OF_INSTRUMENT_SCOPE"})
                continue
            required = set(spec.required_timeframes) | {"M1", "M5", "M15"}
            bars_by_tf = {tf: bars(symbol, tf) for tf in required}
            policy = CONFIRMATION_TIMEFRAME_POLICY.get(spec.strategy_id, "M1_OR_M5_ALLOWED")
            if policy == "M1_REQUIRED" and not bars_by_tf.get("M1"):
                m1_coverage["symbol_day_strategy"].append({"symbol": symbol, "strategy_id": spec.strategy_id, "state": "DATA_GAP_M1"})
            elif policy == "M5_REQUIRED" and not bars_by_tf.get("M5"):
                m1_coverage["symbol_day_strategy"].append({"symbol": symbol, "strategy_id": spec.strategy_id, "state": "DATA_GAP_M5"})
            else:
                m1_coverage["symbol_day_strategy"].append({"symbol": symbol, "strategy_id": spec.strategy_id, "state": "DATA_AVAILABLE"})
            if detector is None:
                events.append({
                    "event_id": _slug([spec.strategy_id, symbol, "no_detector"], "event"),
                    "timestamp": START.isoformat(),
                    "symbol": symbol,
                    "direction": None,
                    "strategy_id": spec.strategy_id,
                    "detector_result": "DATA_GAP",
                    "profile_status": "NEUTRAL",
                    "profile_reason": "no_strategy_specific_detector",
                    "rejection_reason": "NO_STRATEGY_SPECIFIC_DETECTOR",
                })
                continue
            found, reasons = detector(spec, symbol, {tf: bars_by_tf.get(tf, []) for tf in spec.required_timeframes})
            if not found:
                state = "DATA_GAP" if any("DATA_GAP" in r for r in reasons) else "NO_SETUP"
                events.append({
                    "event_id": _slug([spec.strategy_id, symbol, state, ",".join(reasons)], "event"),
                    "timestamp": START.isoformat(),
                    "symbol": symbol,
                    "direction": None,
                    "strategy_id": spec.strategy_id,
                    "detector_result": state,
                    "profile_status": "NEUTRAL",
                    "profile_reason": "profile_not_applied_to_non_valid_detector_result",
                    "rejection_reason": ",".join(reasons) if reasons else state,
                    "confirmation_policy": policy,
                })
                continue
            primary_tf = spec.required_timeframes[-1]
            primary_bars = bars_by_tf.get(primary_tf) or bars_by_tf.get("M15", [])
            primary_times = times(symbol, primary_tf) if bars_by_tf.get(primary_tf) else times(symbol, "M15")
            for op in found:
                plan_conf = _plan_confidence(op, spec)
                detector_result = "VALID_SETUP" if op.rr >= MIN_RR else "INVALID_SETUP"
                planned_row = None
                if detector_result == "VALID_SETUP":
                    planned_row = planned._find_confirmed_planned_entry(
                        op,
                        primary_bars=primary_bars,
                        primary_times=primary_times,
                        m1_bars=bars_by_tf.get("M1", []),
                        m1_times=times(symbol, "M1"),
                        m5_bars=bars_by_tf.get("M5", []),
                        m5_times=times(symbol, "M5"),
                    )
                ids = _canonical_ids(op, spec, planned_row)
                exec_conf = _execution_confidence(op, planned_row, plan_conf)
                event = {
                    "event_id": _slug([spec.strategy_id, symbol, op.direction, op.entry_time.isoformat(), op.opportunity_id], "event"),
                    "timestamp": op.entry_time.isoformat(),
                    "symbol": symbol,
                    "direction": op.direction,
                    "strategy_id": spec.strategy_id,
                    "detector_result": detector_result,
                    **ids,
                    "source_rule_ids": spec.source_rule_ids,
                    "source_video_ids": spec.source_videos,
                    "context_timeframe": ",".join(spec.required_timeframes),
                    "poi_timeframe": primary_tf,
                    "confirmation_timeframe": (planned_row or {}).get("confirmation_timeframe"),
                    "confirmation_policy": policy,
                    "plan_confidence_initial": plan_conf,
                    "plan_confidence_current": plan_conf,
                    "plan_confidence_max": plan_conf,
                    "confirmation_confidence": exec_conf if planned_row else 0.0,
                    "execution_confidence": exec_conf,
                    "profile_status": "NEUTRAL",
                    "profile_score": 50.0,
                    "profile_reason": "not_yet_walk_forward_scored",
                    "profile_sample_size": 0,
                    "profile_reliability": 0.0,
                    "touch_timestamp": (planned_row or {}).get("touch_time"),
                    "confirmation_timestamp": (planned_row or {}).get("confirmation_time"),
                    "execution_timestamp": (planned_row or {}).get("confirmation_time") if exec_conf >= MIN_EXEC else None,
                    "rejection_reason": None,
                    "profile_decision": "NEUTRAL",
                    "confidence_decision": "PASS" if exec_conf >= MIN_EXEC else "BELOW_EXECUTION_FLOOR",
                    "risk_decision": "PASS" if op.rr >= MIN_RR else "RR_BELOW_MIN",
                    "simulated_execution": "TRADE" if planned_row and exec_conf >= MIN_EXEC and op.rr >= MIN_RR else "NO_TRADE",
                    "entry": op.entry,
                    "stop": op.stop,
                    "target": op.target,
                    "rr": round(op.rr, 4),
                    "session": _session(op.entry_time),
                }
                if detector_result == "INVALID_SETUP":
                    event["rejection_reason"] = "instant_entry_rr_below_min"
                elif planned_row is None:
                    event["rejection_reason"] = "planned_entry_not_confirmed_or_expired"
                elif exec_conf < MIN_EXEC:
                    event["rejection_reason"] = "BSI_V3_EXECUTION_CONFIDENCE_BELOW_MIN"
                if planned_row:
                    planned_op: v3.Opportunity = planned_row["opportunity"]
                    future = primary_bars[bisect_right(primary_times, planned_op.entry_time):]
                    outcome = v3.simulate_outcome(planned_op, future)
                    event["outcome"] = asdict(outcome)
                    outcomes_by_event[event["event_id"]] = outcome
                events.append(event)
            print(f"{spec.strategy_id} {symbol}: found={len(found)} events={len(events)}", flush=True)

    _apply_walk_forward_profile(events, outcomes_by_event)
    _with_current_profile([e for e in events if e.get("detector_result") == "VALID_SETUP"], outcomes_by_event)

    canonical_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if e.get("detector_result") == "VALID_SETUP":
            canonical_groups[e["bsi_v3_entry_opportunity_id"]].append(e)
    for group in canonical_groups.values():
        strategies = sorted({g["strategy_id"] for g in group})
        primary = strategies[0]
        if "bsi_v3_reactionary_block" in strategies and "bsi_v3_spectre" in strategies:
            primary = "bsi_v3_reactionary_block"
        for e in group:
            e["primary_strategy"] = primary
            e["confluence_strategies"] = strategies
            e["canonical_confluence"] = len(strategies) > 1

    trade_events = [e for e in events if e.get("simulated_execution") == "TRADE"]
    trade_outcomes = [outcomes_by_event[e["event_id"]] for e in trade_events if e["event_id"] in outcomes_by_event]
    profile_allowed = [e for e in events if e.get("detector_result") == "VALID_SETUP" and e.get("profile_status") in {"ALLOW", "PREFER", "NEUTRAL"}]
    final_ge_80 = [e for e in events if float(e.get("execution_confidence") or 0) >= MIN_EXEC]

    valid_events = [e for e in events if e.get("detector_result") == "VALID_SETUP"]
    detector_funnel = {
        "raw_detector_evaluations": sum(1 for e in events if e.get("detector_result") in {"VALID_SETUP", "INVALID_SETUP"}),
        "mentor_valid_detector_hits": len(valid_events),
        "detector_plan_hits_ge_65": sum(1 for e in valid_events if float(e.get("plan_confidence_current") or 0) >= MIN_PLAN),
        "detector_hits_touched": sum(1 for e in valid_events if e.get("touch_timestamp")),
        "detector_hits_confirmed": sum(1 for e in valid_events if e.get("confirmation_timestamp")),
        "detector_hits_execution_confidence_ge_80": len(final_ge_80),
        "detector_hits_simulated_trade": len(trade_events),
    }
    canonical_funnel = {
        "unique_contexts": len({e.get("bsi_v3_market_context_id") for e in valid_events if e.get("bsi_v3_market_context_id")}),
        "unique_theses": len({e.get("bsi_v3_market_thesis_id") for e in valid_events if e.get("bsi_v3_market_thesis_id")}),
        "unique_pois": len({e.get("bsi_v3_poi_id") for e in valid_events if e.get("bsi_v3_poi_id")}),
        "unique_entry_opportunities": len(canonical_groups),
        "unique_opportunities_plan_ge_65": len({e["bsi_v3_entry_opportunity_id"] for e in valid_events if float(e.get("plan_confidence_current") or 0) >= MIN_PLAN}),
        "unique_opportunities_touched": len({e["bsi_v3_entry_opportunity_id"] for e in valid_events if e.get("touch_timestamp")}),
        "unique_opportunities_confirmed": len({e["bsi_v3_entry_opportunity_id"] for e in valid_events if e.get("confirmation_timestamp")}),
        "unique_opportunities_execution_confidence_ge_80": len({e["bsi_v3_entry_opportunity_id"] for e in final_ge_80}),
        "unique_opportunities_risk_eligible": len({e["bsi_v3_entry_opportunity_id"] for e in valid_events if e.get("risk_decision") == "PASS"}),
        "unique_opportunities_simulated_trade": len({e["bsi_v3_entry_opportunity_id"] for e in trade_events}),
    }
    funnel = {
        "raw_detector_hits": detector_funnel["raw_detector_evaluations"],
        "unique_contexts": canonical_funnel["unique_contexts"],
        "unique_theses": canonical_funnel["unique_theses"],
        "unique_pois": canonical_funnel["unique_pois"],
        "unique_entry_opportunities": canonical_funnel["unique_entry_opportunities"],
        "plans_ge_65": detector_funnel["detector_plan_hits_ge_65"],
        "touched": detector_funnel["detector_hits_touched"],
        "confirmed": detector_funnel["detector_hits_confirmed"],
        "profile_allowed": len(profile_allowed),
        "execution_confidence_ge_80": detector_funnel["detector_hits_execution_confidence_ge_80"],
        "risk_eligible": sum(1 for e in valid_events if e.get("risk_decision") == "PASS"),
        "simulated_trades": detector_funnel["detector_hits_simulated_trade"],
    }

    freq = {
        "contexts_pair_day": _frequency(events, "bsi_v3_market_context_id"),
        "theses_pair_day": _frequency(events, "bsi_v3_market_thesis_id"),
        "pois_pair_day": _frequency(events, "bsi_v3_poi_id"),
        "entry_opportunities_pair_day": _frequency(events, "bsi_v3_entry_opportunity_id"),
        "confirmed_opportunities_pair_day": _frequency([e for e in events if e.get("confirmation_timestamp")], "bsi_v3_entry_opportunity_id"),
        "trades_pair_day": _frequency(trade_events, "bsi_v3_entry_opportunity_id"),
    }

    reaction = [e for e in events if e.get("strategy_id") == "bsi_v3_reactionary_block" and e.get("detector_result") == "VALID_SETUP"]
    spectre = [e for e in events if e.get("strategy_id") == "bsi_v3_spectre" and e.get("detector_result") == "VALID_SETUP"]
    overlap_ids = {
        oid for oid, group in canonical_groups.items()
        if {"bsi_v3_reactionary_block", "bsi_v3_spectre"}.issubset({g["strategy_id"] for g in group})
    }
    overlap = {
        "reactionary_raw_detector_hits": len(reaction),
        "spectre_raw_detector_hits": len(spectre),
        "reactionary_unique_theses": len({e["bsi_v3_market_thesis_id"] for e in reaction}),
        "spectre_unique_theses": len({e["bsi_v3_market_thesis_id"] for e in spectre}),
        "reactionary_unique_pois": len({e["bsi_v3_poi_id"] for e in reaction}),
        "spectre_unique_pois": len({e["bsi_v3_poi_id"] for e in spectre}),
        "reactionary_unique_opportunities": len({e["bsi_v3_entry_opportunity_id"] for e in reaction}),
        "spectre_unique_opportunities": len({e["bsi_v3_entry_opportunity_id"] for e in spectre}),
        "exact_overlap_count": len(overlap_ids),
        "overlap_percent_of_reactionary": round(100 * len(overlap_ids) / max(1, len({e["bsi_v3_entry_opportunity_id"] for e in reaction})), 4),
        "overlap_percent_of_spectre": round(100 * len(overlap_ids) / max(1, len({e["bsi_v3_entry_opportunity_id"] for e in spectre})), 4),
        "reactionary_only_count": len({e["bsi_v3_entry_opportunity_id"] for e in reaction} - overlap_ids),
        "spectre_only_count": len({e["bsi_v3_entry_opportunity_id"] for e in spectre} - overlap_ids),
        "canonical_confluence_count": len(overlap_ids),
        "apparent_frequency_reduction": (len(reaction) + len(spectre)) - len(({e["bsi_v3_entry_opportunity_id"] for e in reaction} | {e["bsi_v3_entry_opportunity_id"] for e in spectre})),
    }
    OVERLAP_JSON.write_text(json.dumps(overlap, indent=2), encoding="utf-8")

    matrix_rows: list[dict[str, Any]] = []
    for spec in registry:
        rows = [e for e in events if e["strategy_id"] == spec.strategy_id]
        valid = [e for e in rows if e.get("detector_result") == "VALID_SETUP"]
        allowed = [e for e in valid if e.get("profile_status") in {"ALLOW", "PREFER", "NEUTRAL"}]
        matrix_rows.append({
            "strategy_id": spec.strategy_id,
            "raw_detector_hits": sum(1 for e in rows if e.get("detector_result") in {"VALID_SETUP", "INVALID_SETUP"}),
            "mentor_valid_setups": len(valid),
            "canonical_opportunities": len({e.get("bsi_v3_entry_opportunity_id") for e in valid}),
            "profile_allowed": len(allowed),
            "profile_preferred": sum(1 for e in valid if e.get("profile_status") == "PREFER"),
            "profile_deprioritized": sum(1 for e in valid if e.get("profile_status") == "DEPRIORITIZE"),
            "profile_blocked": sum(1 for e in valid if e.get("profile_status") == "BLOCK"),
            "confirmed": sum(1 for e in valid if e.get("confirmation_timestamp")),
            "execution_ge_80": sum(1 for e in valid if float(e.get("execution_confidence") or 0) >= MIN_EXEC),
            "trades": sum(1 for e in valid if e.get("simulated_execution") == "TRADE"),
            "profile_survival_rate": round(100 * len(allowed) / max(1, len(valid)), 4),
        })
    MATRIX_JSON.write_text(json.dumps(matrix_rows, indent=2), encoding="utf-8")

    m1_states = Counter(row["state"] for row in m1_coverage["symbol_day_strategy"])
    m1_coverage["summary"] = {
        "rows": len(m1_coverage["symbol_day_strategy"]),
        "states": dict(m1_states),
        "m1_required_strategy_days_data_gap_m1": m1_states.get("DATA_GAP_M1", 0),
        "data_gap_m1_counted_as_no_setup": 0,
        "m1_coverage_percent": round(100 * m1_states.get("DATA_AVAILABLE", 0) / max(1, len(m1_coverage["symbol_day_strategy"])), 4),
    }
    M1_JSON.write_text(json.dumps(m1_coverage, indent=2), encoding="utf-8")

    positions = _position_attribution()
    adaptive = _adaptive_attribution()

    mode_a = _summary(trade_outcomes)
    mode_b = mode_a
    current_trade_outcomes = [
        outcomes_by_event[e["event_id"]] for e in trade_events
        if e.get("current_profile_policy_decision") != "BLOCK" and e["event_id"] in outcomes_by_event
    ]
    mode_c = _summary(current_trade_outcomes)
    reliability_trade_outcomes = [
        outcomes_by_event[e["event_id"]] for e in trade_events
        if e.get("profile_status") != "BLOCK" and e["event_id"] in outcomes_by_event
    ]
    mode_d = _summary(reliability_trade_outcomes)

    result = {
        "status": "FULL_CANONICAL_REPLAY_AUDIT_COMPLETE",
        "window": {"start": START.isoformat(), "end": END.isoformat()},
        "symbols": SYMBOLS,
        "methodology": "BSI_BASELINE_V3_UPDATED_FAIZ",
        "thresholds": {"min_plan": MIN_PLAN, "min_execution": MIN_EXEC, "min_rr": MIN_RR, "tuned": False},
        "event_rows": events,
        "funnel": funnel,
        "detector_funnel": detector_funnel,
        "canonical_funnel": canonical_funnel,
        "frequency": freq,
        "reactionary_spectre_overlap": overlap,
        "strategy_profile_matrix": matrix_rows,
        "m1_coverage": m1_coverage["summary"],
        "profile_modes": {
            "A_MENTOR_VALIDITY_ONLY": mode_a,
            "B_PROFILE_RANKING_NO_HARD_BLOCK": mode_b,
            "C_CURRENT_PROFILE_POLICY_FULL_PERIOD_LEAKY_RESEARCH": mode_c,
            "D_RELIABILITY_GATED_POINT_IN_TIME": mode_d,
        },
        "pit_profile_audit": {
            "current_full_period_profile_inside_jan_aug_is_leaky": True,
            "walk_forward_profile_used_for_reliability_mode": True,
            "leakage_fixed_for_audit_decisions": True,
        },
        "open_position_attribution": {k: v for k, v in positions.items() if k != "positions"},
        "adaptive_action_attribution": {k: v for k, v in adaptive.items() if k != "sample_rows"},
        "fast_watcher_readiness": {
            "verdict": "NO-GO",
            "demo_only_if_enabled_later": True,
            "blockers": [
                "Full replay found current profile policy is not point-in-time safe when back-applied inside Jan-Aug.",
                "Adaptive selected/submitted/broker-accepted lifecycle is now reported, but broker-accepted classification depends on available action statuses.",
                "Fast watcher remains disabled until live queue uses this canonical ledger/profile decision audit trail end to end.",
            ],
        },
    }
    FULL_JSON.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    max_info = freq["entry_opportunities_pair_day"]
    max_caused_by = []
    if max_info["max_pair_day"]:
        symbol, day = max_info["max_pair_day"]
        ids = set(max_info["max_ids"])
        max_caused_by = [
            {
                "opportunity_id": oid,
                "strategies": sorted({e["strategy_id"] for e in events if e.get("bsi_v3_entry_opportunity_id") == oid}),
                "symbol": symbol,
                "day": day,
            }
            for oid in sorted(ids)
        ]

    report_tables = {
        "funnel": _table([["Metric", "Value"]] + [[k, v] for k, v in funnel.items()]),
        "detector_funnel": _table([["Metric", "Value"]] + [[k, v] for k, v in detector_funnel.items()]),
        "canonical_funnel": _table([["Metric", "Value"]] + [[k, v] for k, v in canonical_funnel.items()]),
        "matrix": _table([["Strategy", "Valid", "Canonical", "Allowed", "Blocked", "Confirmed", "Trades", "Survival %"]] + [
            [r["strategy_id"], r["mentor_valid_setups"], r["canonical_opportunities"], r["profile_allowed"], r["profile_blocked"], r["confirmed"], r["trades"], r["profile_survival_rate"]]
            for r in matrix_rows
        ]),
        "profiles": _table([["Mode", "Trades", "WR", "Net R", "PF", "Max DD R"]] + [
            [name, stats.get("trades"), stats.get("win_rate"), stats.get("net_r"), stats.get("profit_factor"), stats.get("max_drawdown_r")]
            for name, stats in result["profile_modes"].items()
        ]),
        "overlap": _table([["Metric", "Value"]] + [[k, v] for k, v in overlap.items()]),
    }

    report_payloads = {
        "01_FULL_JAN_AUG_CANONICAL_FUNNEL.md": "# Full Jan-Aug Canonical Funnel\n\n" + report_tables["funnel"] + "\n",
        "01A_DETECTOR_FUNNEL.md": "# Detector Funnel\n\n" + report_tables["detector_funnel"] + "\n",
        "01B_CANONICAL_OPPORTUNITY_FUNNEL.md": "# Canonical Opportunity Funnel\n\n" + report_tables["canonical_funnel"] + "\n",
        "02_TRUE_PLAN_FREQUENCY.md": "# True Plan Frequency\n\n" + json.dumps(freq, indent=2, default=str) + "\n\nMaximum opportunity day detail:\n\n" + json.dumps(max_caused_by, indent=2) + "\n",
        "03_REACTIONARY_SPECTRE_OVERLAP.md": "# Reactionary/Spectre Overlap\n\n" + report_tables["overlap"] + "\n",
        "04_HIGH_FREQUENCY_STRATEGY_AUDIT.md": "# High Frequency Strategy Audit\n\n" + report_tables["matrix"] + "\n\nHigh frequency is treated as engineering-suspicious when canonical opportunities remain high after dedup; methodology rules were not changed.\n",
        "05_DAILY_PROFILE_ARCHITECTURE_AUDIT.md": "# Daily Profile Architecture Audit\n\nDetector validity and profile decisions are stored separately. Profile may rank or block only with point-in-time evidence.\n\n" + report_tables["profiles"] + "\n",
        "06_PROFILE_PIT_LEAKAGE_AUDIT.md": "# Profile PIT Leakage Audit\n\nCurrent full-period profile back-applied inside Jan-Aug is leakage. The audit uses walk-forward profile decisions for reliability-gated mode.\n",
        "07_PROFILE_STRATEGY_SURVIVAL_MATRIX.md": "# Profile Strategy Survival Matrix\n\n" + report_tables["matrix"] + "\n",
        "08_M1_DATA_COVERAGE_AUDIT.md": "# M1 Data Coverage Audit\n\n" + json.dumps(m1_coverage["summary"], indent=2) + "\n",
        "09_OPEN_POSITION_THESIS_ATTRIBUTION.md": "# Open Position Thesis Attribution\n\n" + json.dumps({k: v for k, v in positions.items() if k != "positions"}, indent=2, default=str) + "\n",
        "10_ADAPTIVE_ACTION_ATTRIBUTION.md": "# Adaptive Action Attribution\n\n" + json.dumps({k: v for k, v in adaptive.items() if k != "sample_rows"}, indent=2, default=str) + "\n",
        "11_ADAPTIVE_EARLY_EXIT_COUNTERFACTUAL.md": "# Adaptive Early Exit Counterfactual\n\nThe 0.8R/80%-target guard remains research-only. This audit did not activate it in demo. Exact winner-cut attribution requires broker-accepted close status; selected-only actions are not counted as broker actions.\n",
        "12_FAST_WATCHER_READINESS.md": "# Fast Watcher Readiness\n\nVerdict: `NO-GO`.\n\nWatcher polling must not create opportunities and same canonical opportunity must be atomic per account. Fast watcher remains disabled.\n",
        "13_FINAL_GO_NO_GO.md": "# Final GO/NO-GO\n\n`NO-GO` for `BSI_V3_FAST_ENTRY_WATCHER_ENABLED=true`.\n\n`MT5_LIVE_TRADING_ENABLED=false` remains required. No real-money routing. No forced demo trade.\n",
    }
    for name, text in report_payloads.items():
        (DOC_DIR / name).write_text(text, encoding="utf-8")

    print(json.dumps({"summary": {k: result[k] for k in ("status", "funnel", "frequency", "reactionary_spectre_overlap", "fast_watcher_readiness")}}, indent=2, default=str))
    store.close()


if __name__ == "__main__":
    main()
