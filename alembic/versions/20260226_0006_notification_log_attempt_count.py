"""add attempt_count to notification_log

Revision ID: 20260226_0006
Revises: 20260226_0005
Create Date: 2026-02-26 01:55:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260226_0006"
down_revision = "20260226_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("notification_log") as batch_op:
        batch_op.add_column(sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="1"))
    with op.batch_alter_table("notification_log") as batch_op:
        batch_op.alter_column("attempt_count", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("notification_log") as batch_op:
        batch_op.drop_column("attempt_count")
