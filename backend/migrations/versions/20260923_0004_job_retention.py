"""Add per-owner retention policies and durable artifact cleanup requests.

Revision ID: 20260923_0004
Revises: 20260921_0003
"""
from alembic import op
import sqlalchemy as sa


revision = "20260923_0004"
down_revision = "20260921_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("job_retention_days", "upload_retention_days"):
        op.add_column("users", sa.Column(column, sa.Integer(), nullable=True))
        op.create_check_constraint(
            op.f(f"ck_users_{column}_range"),
            "users",
            f"{column} IS NULL OR {column} BETWEEN 1 AND 36500",
        )
    for table in ("jobs", "uploads"):
        op.add_column(
            table, sa.Column("deletion_requested_at", sa.DateTime(timezone=True), nullable=True)
        )
        op.add_column(table, sa.Column("cleanup_error", sa.Text(), nullable=True))
        op.create_index(f"ix_{table}_deletion_requested_at", table, ["deletion_requested_at"])


def downgrade() -> None:
    for table in ("uploads", "jobs"):
        op.drop_index(f"ix_{table}_deletion_requested_at", table_name=table)
        op.drop_column(table, "cleanup_error")
        op.drop_column(table, "deletion_requested_at")
    for column in ("upload_retention_days", "job_retention_days"):
        op.drop_constraint(op.f(f"ck_users_{column}_range"), "users", type_="check")
        op.drop_column("users", column)
