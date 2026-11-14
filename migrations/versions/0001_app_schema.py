"""Таблицы диалогов и напоминаний. Сохраняем таблицы прежнего запуска."""

import sqlalchemy as sa
from alembic import context, op
from alembic.util.exc import CommandError
from sqlalchemy.dialects import postgresql

revision = "0001_app_schema"
down_revision = None
branch_labels = None
depends_on = None


def _create_dialogs() -> None:
    op.create_table(
        "assistant_dialogs",
        sa.Column("telegram_user_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("profile", postgresql.JSONB(), nullable=False),
        sa.Column("history", postgresql.JSONB(), nullable=False),
        sa.Column("pending", postgresql.JSONB(), nullable=False),
        sa.Column("recent_materials", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        schema="public",
        if_not_exists=True,
    )


def _create_reminders() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("material_url", sa.Text()),
        sa.Column("frequency", sa.String(16), server_default="once", nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("last_sent_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "frequency IN ('once', 'daily', 'weekly', 'monthly')", name="ck_reminders_frequency"
        ),
        sa.CheckConstraint("status IN ('active', 'paused', 'done')", name="ck_reminders_status"),
        schema="public",
        if_not_exists=True,
    )
    # Ссылка необязательна и может отсутствовать в старой таблице.
    op.add_column(
        "reminders", sa.Column("material_url", sa.Text()), schema="public", if_not_exists=True
    )
    op.create_index(
        "ix_reminders_due",
        "reminders",
        ["status", "next_run_at"],
        schema="public",
        if_not_exists=True,
    )
    op.create_index(
        "ix_reminders_owner", "reminders", ["telegram_user_id"], schema="public", if_not_exists=True
    )


def _check_columns(table: str, expected: set[str]) -> None:
    inspector = sa.inspect(op.get_bind())
    actual = {column["name"] for column in inspector.get_columns(table, schema="public")}
    missing = sorted(expected - actual)
    if missing:
        message = ", ".join(missing)
        raise CommandError(f"Существующая public.{table} требует переноса данных. Нет: {message}")


def upgrade() -> None:
    _create_dialogs()
    _create_reminders()
    if not context.is_offline_mode():
        _check_columns(
            "assistant_dialogs",
            {
                "telegram_user_id",
                "profile",
                "history",
                "pending",
                "recent_materials",
                "version",
                "updated_at",
            },
        )
        _check_columns(
            "reminders",
            {
                "id",
                "telegram_user_id",
                "chat_id",
                "text",
                "material_url",
                "frequency",
                "timezone",
                "starts_at",
                "next_run_at",
                "status",
                "last_sent_at",
                "created_at",
            },
        )


def downgrade() -> None:
    # У таблиц могла быть история до Alembic. Откат не удаляет данные пользователя.
    raise CommandError("Базовая ревизия сохраняет public.assistant_dialogs и public.reminders")
