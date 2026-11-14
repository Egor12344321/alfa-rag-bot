"""Векторные фрагменты базы знаний и состояние последней загрузки."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision = "0002_kb_pgvector"
down_revision = "0001_app_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS kb")
    op.create_table(
        "index_state",
        sa.Column("id", sa.SmallInteger(), primary_key=True, autoincrement=False),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_index_state_singleton"),
        sa.CheckConstraint("dimensions > 0", name="ck_index_state_dimensions"),
        schema="kb",
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("material_id", sa.Text(), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint("length(btrim(text)) > 0", name="ck_chunks_text"),
        sa.CheckConstraint("purpose IN ('knowledge', 'catalog')", name="ck_chunks_purpose"),
        sa.CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_chunks_metadata"),
        sa.CheckConstraint(
            "purpose <> 'knowledge' OR ("
            "(metadata->>'access' = 'free') IS TRUE AND "
            "(metadata->>'kind' <> 'course') IS TRUE)",
            name="ck_chunks_answer_access",
        ),
        schema="kb",
    )
    op.create_index("ix_chunks_purpose_material", "chunks", ["purpose", "material_id"], schema="kb")


def downgrade() -> None:
    # Снимок можно пересобрать. Общие расширение и схема остаются.
    op.drop_table("chunks", schema="kb")
    op.drop_table("index_state", schema="kb")
