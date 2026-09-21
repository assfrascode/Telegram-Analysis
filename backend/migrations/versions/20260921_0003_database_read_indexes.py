"""Index bounded chat snapshots and job-event replay.

Revision ID: 20260921_0003
Revises: 20260921_0002

These ordinary transactional builds require a maintenance window on large
installations because PostgreSQL blocks writes to the indexed tables while
building. See migrations/README.md for the concurrent prebuild procedure.
"""
from alembic import op


revision = "20260921_0003"
down_revision = "20260921_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_collected_messages_chat_timestamp_id",
        "collected_telegram_messages",
        ["chat_id", "timestamp", "telegram_message_id"],
        if_not_exists=True,
    )
    op.create_index("ix_job_events_job_id_id", "job_events", ["job_id", "id"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_job_events_job_id_id", table_name="job_events")
    op.drop_index("ix_collected_messages_chat_timestamp_id", table_name="collected_telegram_messages")
