"""adaptive position states: engineering-contamination flag, and mark ticket 57873187767

Revision ID: 0033_engineering_contamination_flag
Revises: 0032_trade_intelligence_entry_context
Create Date: 2026-08-07

Ticket 57873187767 (XAUUSD, INTERNAL_DEMO) is marked contaminated as part of this migration:
its management history includes real broker-executed PARTIAL_PROFIT closes fired by a since-
fixed bug (R computed from the live/current SL instead of the trade's original risk distance,
which exploded to ~854,999R for several cycles after a breakeven SL move). Those closes are real
broker fills, not a strategy decision -- excluding this ticket from clean performance/
probability/replay-policy-promotion/learning statistics prevents the bug's side effects from
being counted as trading edge. Its full broker/execution history is left completely intact for
incident analysis (position_detail/position_history/replay_results/management_quality_verdict
all still work normally by position_id -- only the AGGREGATE statistics exclude it).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033_engineering_contamination_flag"
down_revision = "0032_trade_intelligence_entry_context"
branch_labels = None
depends_on = None

CONTAMINATED_TICKET = "57873187767"
CONTAMINATION_REASON = (
    "runaway_r_calculation_bug_2026-08-07: R computed from live/current SL instead of original "
    "risk distance after a breakeven SL move (SL ~= entry) exploded max_achieved_r to ~854,999R "
    "for several monitor cycles, firing real erroneous PARTIAL_PROFIT broker closes that reduced "
    "position volume from 0.10 to 0.03 lot before the bug was caught and fixed. See adaptive "
    "trade manager R-anchoring/self-healing-clamp hardening in the same incident."
)


def upgrade() -> None:
    op.add_column("adaptive_position_states", sa.Column("contaminated", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("ix_adaptive_position_states_contaminated", "adaptive_position_states", ["contaminated"])
    op.add_column("adaptive_position_states", sa.Column("contamination_reason", sa.String(512), nullable=True))

    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE adaptive_position_states SET contaminated = true, contamination_reason = :reason WHERE broker_ticket = :ticket"
        ),
        {"reason": CONTAMINATION_REASON, "ticket": CONTAMINATED_TICKET},
    )


def downgrade() -> None:
    op.drop_index("ix_adaptive_position_states_contaminated", table_name="adaptive_position_states")
    op.drop_column("adaptive_position_states", "contamination_reason")
    op.drop_column("adaptive_position_states", "contaminated")
