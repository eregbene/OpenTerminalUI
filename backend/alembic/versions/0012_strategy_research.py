"""strategy research workflow storage

Revision ID: 0012_strategy_research
Revises: 0011_saved_views
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0012_strategy_research"
down_revision = "0011_saved_views"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in (
        "research_experiments",
        "research_backtest_runs",
        "research_optimization_jobs",
        "research_validation_jobs",
        "research_scorecards",
        "research_candidates",
        "research_artifacts",
        "research_events",
    ):
        op.create_table(
            table,
            sa.Column("id", sa.String(length=80), primary_key=True),
            sa.Column("status", sa.String(length=40), nullable=True),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    for table in (
        "research_events",
        "research_artifacts",
        "research_candidates",
        "research_scorecards",
        "research_validation_jobs",
        "research_optimization_jobs",
        "research_backtest_runs",
        "research_experiments",
    ):
        op.drop_table(table)
