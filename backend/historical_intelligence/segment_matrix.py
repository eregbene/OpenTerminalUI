"""Phase C (edge-quality investigation): strategy x symbol x regime breakdown, classified per
combination rather than judging a whole strategy only by its aggregate performance.

Reuses walk_forward.py's existing, already-tested chronological train/OOS machinery -- this adds
no new statistics engine, only (1) real-combination enumeration (only combinations that actually
have data are evaluated -- never a synthetic cross-product of every possible value) and (2) a
mapping from walk_forward's STRONG/ACCEPTABLE/DEGRADED/FAILED_OOS/INSUFFICIENT_SAMPLE vocabulary
onto this investigation's requested POSITIVE_OOS/PROMISING/NEUTRAL/NEGATIVE_OOS/
INSUFFICIENT_SAMPLE taxonomy.

`run_walk_forward` already accepts anchor_strategy/canonical_symbol/regime/session/
confidence_band filters, so an arbitrary combination (e.g. the roadmap's own example --
mtfai1 + NZDUSD + breakout regime) can already be queried directly via that function; this
module's enumerate_* helpers exist only to avoid a human having to guess which combinations have
enough data to be worth running.
"""
from __future__ import annotations

from typing import Any

from backend.historical_intelligence import walk_forward

POSITIVE_OOS = "POSITIVE_OOS"
PROMISING = "PROMISING"
NEUTRAL = "NEUTRAL"
NEGATIVE_OOS = "NEGATIVE_OOS"
INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"

_CLASSIFICATION_MAP = {
    walk_forward.EDGE_STRONG: POSITIVE_OOS,
    walk_forward.EDGE_ACCEPTABLE: PROMISING,
    walk_forward.EDGE_DEGRADED: NEUTRAL,
    walk_forward.EDGE_FAILED_OOS: NEGATIVE_OOS,
    walk_forward.EDGE_INSUFFICIENT_SAMPLE: INSUFFICIENT_SAMPLE,
}


def classify_combination(walk_forward_result: dict[str, Any]) -> str:
    return _CLASSIFICATION_MAP[walk_forward_result["edge_stability"]]


def _distinct(column) -> list[Any]:
    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM
    from backend.shared.db import SessionLocal

    with SessionLocal() as db:
        return [row[0] for row in db.query(column).filter(column.isnot(None)).distinct().all()]


def real_strategies() -> list[str]:
    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM

    return sorted(_distinct(HistoricalPatternFingerprintORM.anchor_strategy))


def real_symbols() -> list[str]:
    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM

    return sorted(_distinct(HistoricalPatternFingerprintORM.canonical_symbol))


def real_regimes() -> list[str]:
    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM

    return sorted(_distinct(HistoricalPatternFingerprintORM.regime))


def strategy_symbol_matrix(*, min_sample: int = 5) -> list[dict[str, Any]]:
    """Every real (strategy, symbol) pair with at least `min_sample` trusted setups -- a much
    looser floor than walk_forward's own 20/10 train/OOS split floor, so a combination with real
    but modest evidence still gets reported as INSUFFICIENT_SAMPLE with its raw aggregate stats
    attached, rather than being silently skipped entirely."""
    results = []
    for strategy in real_strategies():
        for symbol in real_symbols():
            rows = walk_forward._fetch_trusted_rows(anchor_strategy=strategy, canonical_symbol=symbol, regime=None, session=None, confidence_band=None, peer_group_hash=None)
            if len(rows) < min_sample:
                continue
            outcomes = [o for _fp, o in rows]
            aggregate = walk_forward._stats_for_rows(outcomes)
            wf_result = walk_forward.run_walk_forward(anchor_strategy=strategy, canonical_symbol=symbol)
            results.append({
                "anchor_strategy": strategy, "canonical_symbol": symbol,
                "sample_size": len(rows), "aggregate": aggregate,
                "train": wf_result["train"], "oos": wf_result["oos"],
                "classification": classify_combination(wf_result),
            })
    return results


def strategy_symbol_regime_matrix(*, min_sample: int = 5) -> list[dict[str, Any]]:
    """Same idea, one dimension deeper -- only real (strategy, symbol, regime) triples that
    actually occur in the corpus."""
    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM
    from backend.shared.db import SessionLocal

    with SessionLocal() as db:
        triples = (
            db.query(HistoricalPatternFingerprintORM.anchor_strategy, HistoricalPatternFingerprintORM.canonical_symbol, HistoricalPatternFingerprintORM.regime)
            .filter(HistoricalPatternFingerprintORM.regime.isnot(None))
            .distinct()
            .all()
        )

    results = []
    for strategy, symbol, regime in triples:
        rows = walk_forward._fetch_trusted_rows(anchor_strategy=strategy, canonical_symbol=symbol, regime=regime, session=None, confidence_band=None, peer_group_hash=None)
        if len(rows) < min_sample:
            continue
        outcomes = [o for _fp, o in rows]
        aggregate = walk_forward._stats_for_rows(outcomes)
        wf_result = walk_forward.run_walk_forward(anchor_strategy=strategy, canonical_symbol=symbol, regime=regime)
        results.append({
            "anchor_strategy": strategy, "canonical_symbol": symbol, "regime": regime,
            "sample_size": len(rows), "aggregate": aggregate,
            "train": wf_result["train"], "oos": wf_result["oos"],
            "classification": classify_combination(wf_result),
        })
    return results


def strategy_dominance_analysis(anchor_strategy: str) -> dict[str, Any]:
    """Phase E (edge-quality investigation): how much of this strategy's real evidence is
    STANDALONE (fired with no other contributing strategy) vs INDEPENDENTLY CONFIRMED (another
    real strategy also fired on the same setup), and does expectancy/immediate-failure-rate
    differ between the two -- the same real question SMC-confirmation and momentum-confirmation
    ask, computed from the same `contributing_strategies` JSON list and SMC boolean columns
    every fingerprint already carries. Answers whether tightening the existing confirmation gate
    is evidence-supported, without tuning anything itself."""
    from backend.historical_intelligence.orm import HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM
    from backend.shared.db import SessionLocal

    with SessionLocal() as db:
        rows = (
            db.query(HistoricalPatternFingerprintORM, HistoricalSetupOutcomeORM)
            .join(HistoricalSetupOutcomeORM, HistoricalSetupOutcomeORM.fingerprint_id == HistoricalPatternFingerprintORM.fingerprint_id)
            .filter(HistoricalPatternFingerprintORM.anchor_strategy == anchor_strategy, HistoricalSetupOutcomeORM.resolution_status == "RESOLVED", HistoricalSetupOutcomeORM.data_quality != "UNTRUSTED")
            .all()
        )

    def _is_standalone(fp: Any) -> bool:
        others = [s for s in (fp.contributing_strategies or []) if s != anchor_strategy]
        return len(others) == 0

    total_fires = len(rows)
    standalone = [(fp, o) for fp, o in rows if _is_standalone(fp)]
    confirmed = [(fp, o) for fp, o in rows if not _is_standalone(fp)]
    with_momentum = [(fp, o) for fp, o in rows if "momentum" in (fp.contributing_strategies or []) and anchor_strategy != "momentum"]
    with_smc = [(fp, o) for fp, o in rows if fp.bos_present or fp.choch_present or fp.mss_present or fp.displacement_present]
    without_smc = [(fp, o) for fp, o in rows if not (fp.bos_present or fp.choch_present or fp.mss_present or fp.displacement_present)]

    def _group_stats(group: list[tuple[Any, Any]]) -> dict[str, Any]:
        outcomes = [o for _fp, o in group]
        stats = walk_forward._stats_for_rows(outcomes)
        stats["fire_count"] = len(group)
        stats["fire_rate_of_total"] = round(len(group) / total_fires, 4) if total_fires else None
        return stats

    return {
        "anchor_strategy": anchor_strategy,
        "total_fires": total_fires,
        "standalone": _group_stats(standalone),
        "independently_confirmed": _group_stats(confirmed),
        "with_momentum_confirmation": _group_stats(with_momentum),
        "with_smc_confirmation": _group_stats(with_smc),
        "without_smc_confirmation": _group_stats(without_smc),
        "by_symbol": {symbol: _group_stats([(fp, o) for fp, o in rows if fp.canonical_symbol == symbol]) for symbol in sorted({fp.canonical_symbol for fp, _o in rows})},
        "by_regime": {regime: _group_stats([(fp, o) for fp, o in rows if fp.regime == regime]) for regime in sorted({fp.regime for fp, _o in rows if fp.regime})},
        "by_session": {session: _group_stats([(fp, o) for fp, o in rows if fp.session == session]) for session in sorted({fp.session for fp, _o in rows if fp.session})},
    }


def combination(*, anchor_strategy: str | None = None, canonical_symbol: str | None = None, regime: str | None = None, session: str | None = None, confidence_band: str | None = None) -> dict[str, Any]:
    """Query any single, specific combination on demand -- e.g. the roadmap's own example,
    combination(anchor_strategy="mtfai1", canonical_symbol="NZDUSD", regime="breakout")."""
    rows = walk_forward._fetch_trusted_rows(anchor_strategy=anchor_strategy, canonical_symbol=canonical_symbol, regime=regime, session=session, confidence_band=confidence_band, peer_group_hash=None)
    outcomes = [o for _fp, o in rows]
    aggregate = walk_forward._stats_for_rows(outcomes)
    wf_result = walk_forward.run_walk_forward(anchor_strategy=anchor_strategy, canonical_symbol=canonical_symbol, regime=regime, session=session, confidence_band=confidence_band)
    return {
        "filters": {"anchor_strategy": anchor_strategy, "canonical_symbol": canonical_symbol, "regime": regime, "session": session, "confidence_band": confidence_band},
        "sample_size": len(rows), "aggregate": aggregate,
        "train": wf_result["train"], "oos": wf_result["oos"],
        "classification": classify_combination(wf_result),
    }
