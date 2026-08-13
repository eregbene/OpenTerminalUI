"""Point-in-time replay parity verification (Part 6's explicit requirement: "verify point-in-
time replay parity before building intelligence on top of it").

Compares replay.py's output against REAL, already-recorded MT5CandidateEvaluationORM rows --
never a fabricated or hand-constructed test case. Prefers the decision-snapshot source
(replay.replay_for_evaluation -> tier 1) whenever one was captured, since that path replays the
EXACT bars/regime the live engine used and is immune to both the bar-overwrite and broker-UTC-
offset issues by construction; falls back to canonical/revision reconstruction (tiers 2/3) only
when no snapshot exists (e.g. the evaluation predates snapshot_capture.py's deployment).

Richer taxonomy (Part 5 of the integrity spec: "Do not collapse everything into one number").
Four INDEPENDENT dimensions are checked and stored per candidate, so aggregate parity can be
reported as four separate percentages, not one blended score:
  - strategy_present : replay produced a candidate for the SAME strategy_id the live engine used
  - direction_match  : that candidate's direction (BUY/SELL) equals the live one
  - geometry_match   : entry price AND stop-loss are both within _PRICE_TOLERANCE_RELATIVE of live
  - regime_match     : replay's market regime read equals the live evaluation's recorded regime
Those four booleans are then collapsed into a single human-readable `verdict` by priority
(strongest match wins), purely for at-a-glance summaries -- the underlying booleans in
`diff_detail` are what any real reliability judgment should be based on:

    REPLAY_MISSING   -- no live evaluation found, OR replay status != OK, OR the strategy that
                         fired live produced no candidate in replay at all.
    EXACT_MATCH      -- direction_match AND geometry_match AND regime_match.
    DIRECTION_MATCH  -- direction_match, but geometry and/or regime differ.
    GEOMETRY_MATCH   -- geometry_match, but direction differed (levels coincide, call didn't).
    REGIME_MATCH     -- regime_match only (market-condition read agrees; direction/geometry don't).
    STRATEGY_PRESENT -- the strategy fired in both live and replay, but none of the above lined up
                         -- the weakest positive signal ("at least it was compatible").

Scope note: this compares direction, entry, stop-loss, take-profit, and regime -- the parts of a
candidate that are pure, deterministic functions of the historical price bars, the strategy code,
and (for regime) the point-in-time feature context. It deliberately does NOT compare the full
0-100 overall_confidence score, which also depends on LIVE-only inputs at generation time
(current symbol/global win-rate memory, portfolio correlation exposure, signal freshness) that
cannot be reconstructed retroactively without replaying the entire trade-memory history in
lockstep -- out of scope for this check. raw_signal_strength/ranking_score (the strategy's OWN
opinion, before those live adjustments) is compared instead, where available.

Historical bid/ask limitation (Part 6 of the integrity spec): when replay falls back to
reconstruction (no snapshot), bid/ask is approximated from the M15 close (spread=0) unless a real
historical tick is supplied -- this is never faked as a real historical spread. Entry-price
geometry differences attributable to this are an explicit, expected, and separately-trackable
source of GEOMETRY/DIRECTION mismatch for RECONSTRUCTED-source checks; SNAPSHOT-source checks are
not subject to this limitation at all (they carry the real captured bid/ask/spread).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from backend.brokers.mt5.orm import MT5CandidateEvaluationORM
from backend.historical_intelligence.orm import HistoricalReplayParityCheckORM
from backend.historical_intelligence.replay import replay_for_evaluation
from backend.shared.db import SessionLocal

_PRICE_TOLERANCE_RELATIVE = 0.0005  # 5 bps -- OHLC-derived geometry should match near-exactly on
# the SNAPSHOT path; this only absorbs floating-point/Decimal rounding there. On the
# RECONSTRUCTED path it also absorbs the documented bid/ask-approximation limitation above.

VERDICTS = ("EXACT_MATCH", "DIRECTION_MATCH", "GEOMETRY_MATCH", "REGIME_MATCH", "STRATEGY_PRESENT", "REPLAY_MISSING")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _check_id(evaluation_id: str) -> str:
    return "HRPC_" + hashlib.sha256(f"{evaluation_id}:{utcnow().isoformat()}".encode()).hexdigest()[:40]


def _prices_match(live: float | None, replay: float | None) -> bool:
    if live is None or replay is None:
        return False
    if live == 0:
        return replay == 0
    return abs(live - replay) / abs(live) <= _PRICE_TOLERANCE_RELATIVE


def _regimes_match(live_regime: str | None, replay_regime: str | None) -> bool:
    if not live_regime or not replay_regime:
        return False
    return str(live_regime).strip().lower() == str(replay_regime).strip().lower()


def _find_replay_candidate(replay_result: dict[str, Any], strategy_id: str) -> dict[str, Any] | None:
    if strategy_id == "mtfai1":
        mtfai1 = replay_result.get("mtfai1") or {}
        return mtfai1 if mtfai1.get("direction") not in (None, "NO_TRADE") else None
    for candidate in replay_result.get("families_candidates") or []:
        if candidate.get("context", {}).get("strategy_id") == strategy_id or candidate.get("strategy_id") == strategy_id:
            return candidate
    return None


def _extract_price(candidate: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in candidate and candidate[key] is not None:
            try:
                return float(candidate[key])
            except (TypeError, ValueError):
                continue
    return None


def _classify(*, strategy_present: bool, direction_match: bool, geometry_match: bool, regime_match: bool) -> str:
    if not strategy_present:
        return "REPLAY_MISSING"
    if direction_match and geometry_match and regime_match:
        return "EXACT_MATCH"
    if direction_match:
        return "DIRECTION_MATCH"
    if geometry_match:
        return "GEOMETRY_MATCH"
    if regime_match:
        return "REGIME_MATCH"
    return "STRATEGY_PRESENT"


async def verify_parity(evaluation_id: str, *, provider: str = "MT5") -> dict[str, Any]:
    with SessionLocal() as db:
        row = db.get(MT5CandidateEvaluationORM, evaluation_id)
        if row is None:
            return {"evaluation_id": evaluation_id, "verdict": "REPLAY_MISSING", "reason": "LIVE_EVALUATION_NOT_FOUND"}
        symbol, broker_symbol, strategy_id = row.symbol, row.broker_symbol, row.strategy
        live_direction, live_entry, live_sl, live_tp = row.direction, row.proposed_entry, row.proposed_stop_loss, row.proposed_take_profit
        live_confidence = row.overall_confidence
        live_regime = row.market_regime

    replay_result = await replay_for_evaluation(evaluation_id, provider=provider)
    replay_source = replay_result.get("source", "NONE")

    strategy_present = False
    direction_match = geometry_match = regime_match = False
    replay_candidate: dict[str, Any] | None = None
    replay_regime = replay_result.get("regime")

    if replay_result.get("status") == "OK":
        replay_candidate = _find_replay_candidate(replay_result, strategy_id or "")
        strategy_present = replay_candidate is not None
        regime_match = _regimes_match(live_regime, replay_regime)
        if strategy_present:
            replay_direction = str((replay_candidate or {}).get("direction") or "").upper()
            direction_match = replay_direction == str(live_direction or "").upper()
            replay_entry = _extract_price(replay_candidate or {}, "entry", "proposed_entry")
            replay_sl = _extract_price(replay_candidate or {}, "stop_loss", "proposed_stop_loss")
            geometry_match = _prices_match(live_entry, replay_entry) and _prices_match(live_sl, replay_sl)

    verdict = _classify(strategy_present=strategy_present, direction_match=direction_match, geometry_match=geometry_match, regime_match=regime_match)

    replay_direction = (replay_candidate or {}).get("direction")
    replay_entry = _extract_price(replay_candidate or {}, "entry", "proposed_entry")
    replay_sl = _extract_price(replay_candidate or {}, "stop_loss", "proposed_stop_loss")
    replay_confidence = (replay_candidate or {}).get("ranking_score") or (replay_candidate or {}).get("raw_signal_strength")

    check_id = _check_id(evaluation_id)
    diff_detail = {
        "replay_status": replay_result.get("status"),
        "replay_source": replay_source,
        "bars": replay_result.get("bars"),
        "live_regime": live_regime,
        "replay_regime": replay_regime,
        "live_take_profit": live_tp,
        "replay_take_profit": _extract_price(replay_candidate or {}, "take_profit", "proposed_take_profit"),
        "strategy_present": strategy_present,
        "direction_match": direction_match,
        "geometry_match": geometry_match,
        "regime_match": regime_match,
    }
    with SessionLocal() as db:
        db.add(HistoricalReplayParityCheckORM(
            check_id=check_id, live_evaluation_id=evaluation_id, canonical_symbol=symbol, strategy_id=strategy_id or "unknown",
            live_direction=live_direction, replay_direction=replay_direction,
            live_entry=live_entry, replay_entry=replay_entry,
            live_stop_loss=live_sl, replay_stop_loss=replay_sl,
            live_confidence=live_confidence, replay_confidence=replay_confidence,
            verdict=verdict, diff_detail=diff_detail, created_at=utcnow(),
        ))
        db.commit()

    return {
        "check_id": check_id, "evaluation_id": evaluation_id, "symbol": symbol, "strategy_id": strategy_id,
        "verdict": verdict, "source": replay_source,
        "live": {"direction": live_direction, "entry": live_entry, "stop_loss": live_sl, "take_profit": live_tp, "regime": live_regime},
        "replay": {"direction": replay_direction, "entry": replay_entry, "stop_loss": replay_sl, "regime": replay_regime},
        "diff_detail": diff_detail,
    }


async def run_parity_batch(evaluation_ids: list[str], *, provider: str = "MT5") -> dict[str, Any]:
    """Runs verify_parity over an explicit list of REAL evaluation_ids (the caller selects which
    real candidates to check -- this function never invents or samples fabricated data) and
    returns an aggregate summary: the verdict-bucket distribution (waterfall, mutually exclusive)
    PLUS four independent parity percentages (regime/strategy-presence/direction/geometry), per
    the explicit "do not collapse everything into one number" requirement."""
    results = []
    for evaluation_id in evaluation_ids:
        try:
            result = await verify_parity(evaluation_id, provider=provider)
        except Exception as exc:
            result = {"evaluation_id": evaluation_id, "verdict": "ERROR", "error": f"{exc.__class__.__name__}: {exc}"}
        results.append(result)

    verdict_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    flag_true: dict[str, int] = {"strategy_present": 0, "direction_match": 0, "geometry_match": 0, "regime_match": 0}
    flag_total = 0
    for r in results:
        verdict_counts[r["verdict"]] = verdict_counts.get(r["verdict"], 0) + 1
        source = r.get("source")
        if source:
            source_counts[source] = source_counts.get(source, 0) + 1
        detail = r.get("diff_detail") or {}
        if "strategy_present" in detail:
            flag_total += 1
            for key in flag_true:
                if detail.get(key):
                    flag_true[key] += 1

    parity_percentages = {
        f"{key}_pct": round(100.0 * count / flag_total, 1) if flag_total else None
        for key, count in flag_true.items()
    }

    return {
        "total": len(results),
        "verdict_counts": verdict_counts,
        "source_counts": source_counts,
        "parity_percentages": parity_percentages,
        "results": results,
    }
