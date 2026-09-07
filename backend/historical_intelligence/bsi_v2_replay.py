"""BSI V2 point-in-time replay helpers.

This module does not register BSI V2 in the production dispatcher. It reuses
historical_intelligence.replay for point-in-time context construction, then calls
the isolated BSI V2 research evaluators directly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.historical_intelligence.replay import replay_at
from backend.mt5_strategies.families.bsi_v2_engine import BSI_V2_RESEARCH_EVALUATORS, evaluate_bsi_v2_subtype
from backend.mt5_strategies.families.bsi_v2_lifecycle import BSIDurableLifecycleStore, BSILifecycleStore
from backend.mt5_strategies.families.bsi_v2_scaffold import BSI_BASELINE_V2_AUDIOVISUAL


@dataclass
class BSIV2StrategyFunnel:
    subtype: str
    evaluated: int = 0
    valid_setups: int = 0
    executable_entries: int = 0
    consumed_entries: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)

    def observe(self, signal: Any) -> None:
        self.evaluated += 1
        if signal.valid:
            self.valid_setups += 1
            if signal.proposed_entry is not None and signal.stop_loss is not None and signal.take_profit is not None:
                self.executable_entries += 1
            if signal.evidence.get("lifecycle_state") == "CONSUMED":
                self.consumed_entries += 1
            return
        reason = signal.rejection_reason or "UNKNOWN_REJECTION"
        self.rejection_reasons[reason] = self.rejection_reasons.get(reason, 0) + 1


@dataclass
class BSIV2ReplayFunnelReport:
    bsi_version: str = BSI_BASELINE_V2_AUDIOVISUAL
    contexts_evaluated: int = 0
    source_counts: dict[str, int] = field(default_factory=dict)
    bars_insufficient: int = 0
    strategy_funnels: dict[str, BSIV2StrategyFunnel] = field(
        default_factory=lambda: {subtype: BSIV2StrategyFunnel(subtype) for subtype in BSI_V2_RESEARCH_EVALUATORS}
    )

    @property
    def total_valid_setups(self) -> int:
        return sum(row.valid_setups for row in self.strategy_funnels.values())

    @property
    def total_executable_entries(self) -> int:
        return sum(row.executable_entries for row in self.strategy_funnels.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "bsi_version": self.bsi_version,
            "contexts_evaluated": self.contexts_evaluated,
            "source_counts": dict(self.source_counts),
            "bars_insufficient": self.bars_insufficient,
            "total_valid_setups": self.total_valid_setups,
            "total_executable_entries": self.total_executable_entries,
            "strategy_funnels": {
                subtype: {
                    "evaluated": row.evaluated,
                    "valid_setups": row.valid_setups,
                    "executable_entries": row.executable_entries,
                    "consumed_entries": row.consumed_entries,
                    "rejection_reasons": dict(row.rejection_reasons),
                }
                for subtype, row in self.strategy_funnels.items()
            },
        }


def evaluate_bsi_v2_context(
    ctx: Any,
    *,
    lifecycle: BSILifecycleStore | None = None,
    subtype_order: tuple[str, ...] | None = None,
) -> tuple[list[Any], BSIV2ReplayFunnelReport]:
    """Evaluate all requested V2 subtypes against an already-built context."""
    store = lifecycle or BSILifecycleStore()
    report = BSIV2ReplayFunnelReport(contexts_evaluated=1)
    signals: list[Any] = []
    for subtype in subtype_order or tuple(BSI_V2_RESEARCH_EVALUATORS):
        signal = evaluate_bsi_v2_subtype(ctx, subtype, lifecycle=store)
        signals.append(signal)
        report.strategy_funnels[subtype].observe(signal)
    return signals, report


async def replay_bsi_v2_at(
    *,
    canonical_symbol: str,
    broker_symbol: str,
    at: datetime,
    provider: str = "MT5",
    lifecycle_path: str | Path | None = None,
    replay_run_id: str = "bsi_v2_pit_replay",
    account_id: str | None = None,
    subtype_order: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Point-in-time DB replay for BSI V2 research.

    Uses existing replay_at() for no-lookahead bar reconstruction and context
    construction. BSI V2 remains outside production evaluate_all().
    """
    base = await replay_at(canonical_symbol=canonical_symbol, broker_symbol=broker_symbol, at=at, provider=provider)
    if base.get("status") != "OK" or base.get("_ctx") is None:
        return {
            "status": base.get("status", "REPLAY_FAILED"),
            "source": base.get("source"),
            "as_of": base.get("as_of"),
            "bars": base.get("bars", {}),
            "bsi_v2": BSIV2ReplayFunnelReport(contexts_evaluated=0, bars_insufficient=1).as_dict(),
        }

    ctx = base["_ctx"]
    if account_id is not None:
        object.__setattr__(ctx, "account_id", account_id)
    object.__setattr__(ctx, "replay_run_id", replay_run_id)
    lifecycle: BSILifecycleStore
    if lifecycle_path is not None:
        lifecycle = BSIDurableLifecycleStore(lifecycle_path, namespace=BSI_BASELINE_V2_AUDIOVISUAL, replay_run_id=replay_run_id)
    else:
        lifecycle = BSILifecycleStore()

    signals, report = evaluate_bsi_v2_context(ctx, lifecycle=lifecycle, subtype_order=subtype_order)
    report.source_counts[base.get("source", "UNKNOWN")] = 1
    return {
        "status": "OK",
        "source": base.get("source"),
        "as_of": base.get("as_of"),
        "bars": base.get("bars", {}),
        "signals": signals,
        "bsi_v2": report.as_dict(),
    }


async def replay_bsi_v2_grid(
    points: list[dict[str, Any]],
    *,
    lifecycle_path: str | Path | None = None,
    replay_run_id: str = "bsi_v2_pit_replay",
    account_id: str | None = None,
    provider: str = "MT5",
) -> dict[str, Any]:
    """Replay a small explicit PIT grid and aggregate all-nine funnel counts."""
    aggregate = BSIV2ReplayFunnelReport()
    results = []
    for point in points:
        result = await replay_bsi_v2_at(
            canonical_symbol=point["canonical_symbol"],
            broker_symbol=point.get("broker_symbol", point["canonical_symbol"]),
            at=point["at"],
            provider=point.get("provider", provider),
            lifecycle_path=lifecycle_path,
            replay_run_id=replay_run_id,
            account_id=account_id,
        )
        results.append(result)
        report = result["bsi_v2"]
        aggregate.contexts_evaluated += report["contexts_evaluated"]
        aggregate.bars_insufficient += report["bars_insufficient"]
        for source, count in report["source_counts"].items():
            aggregate.source_counts[source] = aggregate.source_counts.get(source, 0) + count
        for subtype, row in report["strategy_funnels"].items():
            target = aggregate.strategy_funnels[subtype]
            target.evaluated += row["evaluated"]
            target.valid_setups += row["valid_setups"]
            target.executable_entries += row["executable_entries"]
            target.consumed_entries += row["consumed_entries"]
            for reason, count in row["rejection_reasons"].items():
                target.rejection_reasons[reason] = target.rejection_reasons.get(reason, 0) + count
    return {"status": "OK", "replay_run_id": replay_run_id, "aggregate": aggregate.as_dict(), "results": results}
