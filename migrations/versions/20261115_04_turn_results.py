"""Persist user-scoped results for duplicate Telegram message handling."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20261115_04"
down_revision = "20261115_03"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "turn_results",
        sa.Column("user_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("update_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("reply", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="app",
    )


def downgrade():
    op.drop_table("turn_results", schema="app")
