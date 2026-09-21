"""Add database-only partial mode for scheduled Telegram reports.

Revision ID: 20260921_0002
Revises: 20260813_0001
Create Date: 2026-09-21
"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260921_0002"
down_revision: str | None = "20260813_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_report_schedules",
        sa.Column(
            "force_partial_telegram_sync",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.alter_column(
        "telegram_report_schedules",
        "force_partial_telegram_sync",
        server_default=None,
    )


def downgrade() -> None:
    op.drop_column("telegram_report_schedules", "force_partial_telegram_sync")
