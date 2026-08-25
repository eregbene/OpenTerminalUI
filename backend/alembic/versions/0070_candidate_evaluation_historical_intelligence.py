"""mt5_candidate_evaluations: persist Historical Intelligence adjustment/status per candidate

Revision ID: 0070_candidate_evaluation_historical_intelligence
Revises: 0069_strategy_lifecycle
Create Date: 2026-08-25

2026-08-25 MTFAI1 V2 Confidence Architecture & Calibration Audit, forward-tracking follow-up:
candidate["historical_intelligence"] (status, ranking_adjustment, historical_decision, peer-group
evidence, or MTFAI1 V2's own NEUTRAL/MTFAI1_V2_HI_NOT_YET_VERSION_COMPATIBLE marker) was computed
every cycle (backend/brokers/mt5/autonomous.py::_apply_historical_intelligence) but never actually
persisted anywhere -- capture_cycle_candidate_evaluations() read confidence["components"] and
everything else, but never candidate["historical_intelligence"]. Without this column there is no
way to verify, from real DEMO data, whether HI stayed neutral for MTFAI1 V2 as designed, or to
compare HI-adjusted vs HI-neutral outcomes once V2 fingerprints exist.

Purely additive: nullable JSON column, existing rows simply have no HI record (honest -- they
predate this column, not fabricated as empty/neutral).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0070_candidate_evaluation_historical_intelligence"
down_revision: Union[str, Sequence[str], None] = "0069_strategy_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "mt5_candidate_evaluations"


def _add_column_if_missing(inspector: sa.Inspector, table_name: str, column: sa.Column) -> None:
    existing = {c["name"] for c in inspector.get_columns(table_name)}
    if column.name in existing:
        return
    op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    _add_column_if_missing(inspector, _TABLE, sa.Column("historical_intelligence", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column(_TABLE, "historical_intelligence")
