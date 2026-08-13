"""make adaptive-manager idempotency uniqueness composite with account_id

Revision ID: 0049_adaptive_idempotency_account_scope
Revises: 0048_portfolio_snapshot_account_scope
Create Date: 2026-08-12

Confirmed latent bug (multi-account risk/protection audit, Bug 5): adaptive_management_actions
.idempotency_key and adaptive_partial_profit_stages.idempotency_key were declared UNIQUE on that
single column, DB-wide across every account. In practice a cross-account collision was already
prevented indirectly -- every non-demo_10k AdaptivePositionStateORM.position_id is account-id-
prefixed (see backend/adaptive_management/service.py::_position_id), and _idempotency_key()
hashes that position_id, so the key string itself already differed per account. But that
protection was implicit (it depended on position_id's format never changing) rather than an
enforced invariant, and the lookup query that checks for an existing action didn't filter by
account_id either. This migration (paired with the service.py fix that now hashes account_id
directly and filters the lookup query by it) makes the DB constraint itself composite
(account_id, idempotency_key), so "one account's action can never masquerade as another
account's duplicate" is enforced at the schema level, not just by convention.

adaptive_partial_profit_stages carried BOTH a column-level UNIQUE constraint AND a separate
unique index on idempotency_key (migrations 0034); both single-column uniques are dropped here.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0049_adaptive_idempotency_account_scope"
down_revision: Union[str, Sequence[str], None] = "0048_portfolio_snapshot_account_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("adaptive_management_actions_idempotency_key_key", "adaptive_management_actions", type_="unique")
    op.create_unique_constraint("uq_adaptive_management_action_account_idempotency", "adaptive_management_actions", ["account_id", "idempotency_key"])

    op.drop_constraint("adaptive_partial_profit_stages_idempotency_key_key", "adaptive_partial_profit_stages", type_="unique")
    op.drop_index("ix_adaptive_partial_profit_stages_idempotency_key", table_name="adaptive_partial_profit_stages")
    op.create_index("ix_adaptive_partial_profit_stages_idempotency_key", "adaptive_partial_profit_stages", ["idempotency_key"], unique=False)
    op.create_unique_constraint("uq_adaptive_partial_profit_account_idempotency", "adaptive_partial_profit_stages", ["account_id", "idempotency_key"])


def downgrade() -> None:
    op.drop_constraint("uq_adaptive_partial_profit_account_idempotency", "adaptive_partial_profit_stages", type_="unique")
    op.drop_index("ix_adaptive_partial_profit_stages_idempotency_key", table_name="adaptive_partial_profit_stages")
    op.create_index("ix_adaptive_partial_profit_stages_idempotency_key", "adaptive_partial_profit_stages", ["idempotency_key"], unique=True)
    op.create_unique_constraint("adaptive_partial_profit_stages_idempotency_key_key", "adaptive_partial_profit_stages", ["idempotency_key"])

    op.drop_constraint("uq_adaptive_management_action_account_idempotency", "adaptive_management_actions", type_="unique")
    op.create_unique_constraint("adaptive_management_actions_idempotency_key_key", "adaptive_management_actions", ["idempotency_key"])
