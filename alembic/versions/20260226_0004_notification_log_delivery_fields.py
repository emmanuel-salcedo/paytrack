"""add notification_log delivery metadata fields

Revision ID: 20260226_0004
Revises: 20260226_0003
Create Date: 2026-02-26 00:40:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260226_0004"
down_revision = "20260226_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notification_log") as batch_op:
        batch_op.add_column(sa.Column("status", sa.String(length=16), nullable=False, server_default="sent"))
        batch_op.add_column(sa.Column("error_message", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("delivered_at", sa.DateTime(timezone=False), nullable=True))
    op.execute("UPDATE notification_log SET delivered_at = created_at WHERE status = 'sent' AND delivered_at IS NULL")
    with op.batch_alter_table("notification_log") as batch_op:
        batch_op.alter_column("status", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("notification_log") as batch_op:
        batch_op.drop_column("delivered_at")
        batch_op.drop_column("error_message")
        batch_op.drop_column("status")
