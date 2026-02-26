"""Phase 2 core tables

Revision ID: 20260226_0002
Revises: 20260226_0001
Create Date: 2026-02-26 10:20:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260226_0002"
down_revision = "20260226_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("expected_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("initial_due_date", sa.Date(), nullable=False),
        sa.Column("recurrence_type", sa.String(length=24), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
        sa.Column("paid_off_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.CheckConstraint(
            "recurrence_type IN ('one_time','weekly','biweekly','monthly','yearly')",
            name="ck_payments_recurrence_type",
        ),
    )

    op.create_table(
        "occurrences",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_id", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("expected_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="scheduled"),
        sa.Column("amount_paid", sa.Numeric(12, 2), nullable=True),
        sa.Column("paid_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('scheduled','completed','skipped','canceled')",
            name="ck_occurrences_status",
        ),
        sa.UniqueConstraint("payment_id", "due_date", name="uq_occurrences_payment_due_date"),
    )
    op.create_index("ix_occurrences_due_date", "occurrences", ["due_date"])
    op.create_index("ix_occurrences_status", "occurrences", ["status"])
    op.create_index("ix_occurrences_payment_id", "occurrences", ["payment_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("occurrence_id", sa.Integer(), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["occurrence_id"], ["occurrences.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_notifications_is_read", "notifications", ["is_read"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])

    op.create_table(
        "notification_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("bucket_date", sa.Date(), nullable=False),
        sa.Column("occurrence_id", sa.Integer(), nullable=True),
        sa.Column("dedup_key", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["occurrence_id"], ["occurrences.id"], ondelete="SET NULL"),
        sa.UniqueConstraint(
            "type",
            "channel",
            "bucket_date",
            "dedup_key",
            name="uq_notification_log_dedup",
        ),
    )
    op.create_index("ix_notification_log_bucket_date", "notification_log", ["bucket_date"])

    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_name", sa.String(length=64), nullable=False),
        sa.Column("run_date", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.UniqueConstraint("job_name", "run_date", name="uq_job_runs_name_run_date"),
    )


def downgrade() -> None:
    op.drop_table("job_runs")
    op.drop_index("ix_notification_log_bucket_date", table_name="notification_log")
    op.drop_table("notification_log")
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_index("ix_notifications_is_read", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index("ix_occurrences_payment_id", table_name="occurrences")
    op.drop_index("ix_occurrences_status", table_name="occurrences")
    op.drop_index("ix_occurrences_due_date", table_name="occurrences")
    op.drop_table("occurrences")
    op.drop_table("payments")

