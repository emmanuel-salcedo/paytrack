"""Add telegram config fields to app_settings

Revision ID: 20260226_0003
Revises: 20260226_0002
Create Date: 2026-02-26 12:05:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260226_0003"
down_revision = "20260226_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_settings", sa.Column("telegram_bot_token", sa.String(length=255), nullable=True))
    op.add_column("app_settings", sa.Column("telegram_chat_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("app_settings", "telegram_chat_id")
    op.drop_column("app_settings", "telegram_bot_token")

