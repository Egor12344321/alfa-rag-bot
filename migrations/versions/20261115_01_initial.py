"""Создать рабочие app и kb; исходный дамп хранится в отдельной базе."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261115_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    op.execute("CREATE SCHEMA IF NOT EXISTS kb")
    op.create_table(
        "profiles",
        sa.Column("user_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("business_stage", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("experience_level", sa.String(24), nullable=False, server_default="unknown"),
        sa.Column("industry", sa.Text()),
        sa.Column("tax_regime", sa.Text()),
        sa.Column("response_style", sa.String(24), nullable=False, server_default="auto"),
        sa.Column("preferred_access", sa.String(16), nullable=False, server_default="any"),
        sa.Column("wants_courses", sa.Boolean()),
        sa.Column("goal", sa.Text()),
        sa.Column("timezone", sa.String(64)),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "business_stage IN ('beginner','owner','solo_founder','unknown')",
            name="ck_profile_stage",
        ),
        sa.CheckConstraint(
            "experience_level IN ('novice','experienced','unknown')", name="ck_profile_experience"
        ),
        sa.CheckConstraint(
            "response_style IN ('auto','brief','detailed','template','learning')",
            name="ck_profile_style",
        ),
        sa.CheckConstraint("preferred_access IN ('any','free','paid')", name="ck_profile_access"),
        schema="app",
    )
    op.create_table(
        "dialogs",
        sa.Column("user_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("pending", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "recent_materials", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_update_id", sa.BigInteger()),
        sa.Column("last_reply", JSONB()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("version > 0", name="ck_dialog_version"),
        schema="app",
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_message_role"),
        schema="app",
    )
    op.create_index("ix_messages_user_id_id", "messages", ["user_id", "id"], schema="app")
    op.create_table(
        "reminders",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("material_url", sa.Text()),
        sa.Column("frequency", sa.String(16), nullable=False, server_default="once"),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("last_sent_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "frequency IN ('once','daily','weekly','monthly')", name="ck_reminders_frequency"
        ),
        sa.CheckConstraint("status IN ('active','paused','done')", name="ck_reminders_status"),
        schema="app",
    )
    op.create_index("ix_reminders_due", "reminders", ["status", "next_run_at"], schema="app")
    op.create_index(
        "ix_reminders_owner", "reminders", ["telegram_user_id", "chat_id"], schema="app"
    )
    op.create_table(
        "reminder_drafts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("material_url", sa.Text()),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "frequency IN ('once','daily','weekly','monthly')", name="ck_reminder_drafts_frequency"
        ),
        sa.CheckConstraint(
            "status IN ('pending','confirmed','cancelled')", name="ck_reminder_drafts_status"
        ),
        schema="app",
    )
    op.create_index(
        "ix_reminder_drafts_owner",
        "reminder_drafts",
        ["telegram_user_id", "chat_id", "status"],
        schema="app",
    )
    op.create_index("ix_reminder_drafts_expires", "reminder_drafts", ["expires_at"], schema="app")
    op.create_table(
        "ingestion_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("embedding_revision", sa.Text(), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("source_fingerprint", sa.Text(), nullable=False),
        sa.Column("pipeline_version", sa.Text(), nullable=False),
        sa.Column("metrics", JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('building','ready','failed')", name="ck_kb_run_status"),
        sa.CheckConstraint("NOT is_active OR status = 'ready'", name="ck_kb_active_ready"),
        sa.CheckConstraint("embedding_dimension > 0", name="ck_kb_dimension_positive"),
        schema="kb",
    )
    op.create_index(
        "uq_kb_one_active_run",
        "ingestion_runs",
        ["is_active"],
        unique=True,
        schema="kb",
        postgresql_where=sa.text("is_active"),
    )
    op.create_table(
        "materials",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("kb.ingestion_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("metadata", JSONB(), nullable=False),
        sa.UniqueConstraint("run_id", "source_key", name="uq_kb_material_source"),
        sa.UniqueConstraint("run_id", "id", name="uq_kb_material_run_id"),
        schema="kb",
    )
    op.create_table(
        "chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("kb.ingestion_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("material_id", UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("chunk_no", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("metadata", JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id", "material_id"],
            ["kb.materials.run_id", "kb.materials.id"],
            ondelete="CASCADE",
            name="fk_kb_chunk_material_run",
        ),
        sa.UniqueConstraint("run_id", "material_id", "purpose", "chunk_no", name="uq_kb_chunk"),
        sa.CheckConstraint("purpose IN ('knowledge','catalog')", name="ck_kb_chunk_purpose"),
        sa.CheckConstraint("chunk_no >= 0", name="ck_kb_chunk_number"),
        sa.CheckConstraint("length(btrim(text)) > 0", name="ck_kb_chunk_text"),
        schema="kb",
    )
    op.create_index(
        "ix_kb_chunks_scope", "chunks", ["run_id", "purpose", "material_id"], schema="kb"
    )


def downgrade():
    for table in ["chunks", "materials", "ingestion_runs"]:
        op.drop_table(table, schema="kb")
    for table in ["reminder_drafts", "reminders", "messages", "dialogs", "profiles"]:
        op.drop_table(table, schema="app")
    # Расширение и схемы могут использовать другие приложения: не удаляем их.
