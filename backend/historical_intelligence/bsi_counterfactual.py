"""BSI counterfactual persistence (BSI Intelligence Migration Phase C, second half: "finalize the
draft counterfactual schema from the follow-up report").

Promotes tonight's earlier design-only draft (`bsi_management_counterfactual_schema_draft.py`,
itself modeled on the existing, working `CounterfactualOutcomeORM` -- see that draft's own
docstring for the full reasoning) into a real, applied table plus a sync function that persists
`bsi_replay.py::replay_bsi_candidate()`'s output.

NET_MANAGER_CONTRIBUTION (directive Section 5's own explicit formula) is computed and stored
directly on each row, not left for a downstream consumer to re-derive inconsistently:
    NET_MANAGER_CONTRIBUTION = R_SAVED_FROM_LOSERS + EXTRA_R_CAPTURED_FROM_WINNERS - R_LOST_FROM_PREMATURE_WINNER_EXITS
Per-row this collapses to the single, already-familiar quantity `delta_r = hypothetical_r -
baseline_r` for THAT one trade; the three-way split only becomes meaningful in AGGREGATE (summing
delta_r separately over baseline-losers vs baseline-winners) -- `avoided_loss`/`false_early_exit`
booleans on each row are exactly what a later aggregation groups by to reconstruct the three
components validly, which is why they are stored per-row rather than only computed at query time.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.historical_intelligence.bsi_replay import BSIPolicyResult, BSITrade, BaselineResolution
from backend.historical_intelligence.orm import utcnow
from backend.shared.db import Base


class BSIManagementCounterfactualORM(Base):
    """One row per (BSI fingerprint, management policy). See module docstring for the
    NET_MANAGER_CONTRIBUTION formula this table's aggregate queries reconstruct."""

    __tablename__ = "bsi_management_counterfactuals"

    outcome_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    fingerprint_id: Mapped[str] = mapped_column(String(160), ForeignKey("historical_pattern_fingerprints.fingerprint_id"), nullable=False, index=True)
    subtype: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    policy_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    baseline_r: Mapped[float] = mapped_column(Float, nullable=False)
    hypothetical_r: Mapped[float] = mapped_column(Float, nullable=False)
    delta_r: Mapped[float] = mapped_column(Float, nullable=False)

    avoided_loss: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)  # baseline<=0, this policy scratched/won it
    false_early_exit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)  # baseline>0 (real eventual winner), this policy exited it at a worse R
    max_favorable_r: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    mfe_capture_r: Mapped[float | None] = mapped_column(Float, nullable=True)  # hypothetical_r / max_favorable_r when max_favorable_r>0

    resolution_method: Mapped[str] = mapped_column(String(24), nullable=False, default="EXACT_BAR_SEQUENCE")  # EXACT_BAR_SEQUENCE | MILESTONE_FLAG_APPROXIMATION -- never silently blend rows of different provenance
    intrabar_ambiguous_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolution_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # SL_HIT | TP_HIT | MTM_TIMEOUT

    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)

    __table_args__ = (
        UniqueConstraint("fingerprint_id", "policy_id", name="uq_bsi_mgmt_cf_fingerprint_policy"),
        Index("ix_bsi_mgmt_cf_subtype_policy", "subtype", "policy_id"),
    )


def _outcome_id(fingerprint_id: str, policy_id: str) -> str:
    return "BSICF_" + hashlib.sha256(f"{fingerprint_id}:{policy_id}".encode()).hexdigest()[:32]


def build_counterfactual_row(*, fingerprint_id: str, subtype: str, baseline: BaselineResolution, result: BSIPolicyResult) -> BSIManagementCounterfactualORM:
    """Pure transformation: (shared baseline resolution, one policy's result) -> one persistable
    row. Never re-walks candles -- both inputs already come from the SAME
    replay_bsi_candidate() call, so this function cannot itself introduce any inconsistency
    between what different policies were compared against."""
    delta = (result.outcome_r or 0.0) - baseline.outcome_r
    avoided_loss = baseline.outcome_r <= 0 and (result.outcome_r or 0.0) > baseline.outcome_r
    false_early_exit = baseline.outcome_r > 0 and (result.outcome_r or 0.0) < baseline.outcome_r
    mfe_capture = (result.outcome_r / baseline.max_favorable_r) if baseline.max_favorable_r > 0 and result.outcome_r is not None else None
    return BSIManagementCounterfactualORM(
        outcome_id=_outcome_id(fingerprint_id, result.policy_id),
        fingerprint_id=fingerprint_id,
        subtype=subtype,
        policy_id=result.policy_id,
        baseline_r=baseline.outcome_r,
        hypothetical_r=result.outcome_r if result.outcome_r is not None else baseline.outcome_r,
        delta_r=delta,
        avoided_loss=avoided_loss,
        false_early_exit=false_early_exit,
        max_favorable_r=baseline.max_favorable_r,
        mfe_capture_r=mfe_capture,
        resolution_method="EXACT_BAR_SEQUENCE",
        intrabar_ambiguous_events=baseline.ambiguous_intrabar_events,
        resolution_kind=baseline.resolution_kind,
        raw_payload={"exit_reason": result.exit_reason, "evidence": result.evidence},
    )


def net_manager_contribution(rows: list[BSIManagementCounterfactualORM]) -> dict[str, float]:
    """Directive Section 5's exact formula, reconstructed in aggregate from per-row deltas:
    NET_MANAGER_CONTRIBUTION = R_SAVED_FROM_LOSERS + EXTRA_R_CAPTURED_FROM_WINNERS - R_LOST_FROM_PREMATURE_WINNER_EXITS
    `rows` must all share the same policy_id (caller's responsibility -- this function does not
    itself filter, so mixing policies here would silently misattribute contribution)."""
    saved = sum(r.delta_r for r in rows if r.avoided_loss)
    destroyed = sum(-r.delta_r for r in rows if r.false_early_exit)
    # "extra captured" = positive delta on rows that are neither an avoided-loss nor a
    # false-early-exit (i.e. a baseline winner whose realized R the policy INCREASED, or a
    # baseline loser whose R improved without crossing into avoided_loss's own >0 threshold --
    # rare but real, e.g. a loss reduced from -1.0 to -0.5 without ever reaching breakeven).
    extra_captured = sum(r.delta_r for r in rows if not r.avoided_loss and not r.false_early_exit and r.delta_r > 0)
    return {
        "r_saved_from_losers": round(saved, 4),
        "extra_r_captured_from_winners": round(extra_captured, 4),
        "r_lost_from_premature_winner_exits": round(destroyed, 4),
        "net_manager_contribution": round(saved + extra_captured - destroyed, 4),
        "n": len(rows),
    }
