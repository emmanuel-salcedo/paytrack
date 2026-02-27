"""add telegram message id to notification_log

Revision ID: 20260226_0005
Revises: 20260226_0004
Create Date: 2026-02-26 01:20:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260226_0005"
down_revision = "20260226_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notification_log") as batch_op:
        batch_op.add_column(sa.Column("telegram_message_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("notification_log") as batch_op:
        batch_op.drop_column("telegram_message_id")
