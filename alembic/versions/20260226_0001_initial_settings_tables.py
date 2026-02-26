"""Initial settings tables for Phase 0

Revision ID: 20260226_0001
Revises: 
Create Date: 2026-02-26 09:50:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260226_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pay_schedule",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("anchor_payday_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )

    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("due_soon_days", sa.Integer(), nullable=False),
        sa.Column("daily_summary_time", sa.String(length=5), nullable=False),
        sa.Column("telegram_enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_table("pay_schedule")

