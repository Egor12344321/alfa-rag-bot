"""Перенести данные работающего MVP, сохранив исходные public-таблицы."""

import json

import sqlalchemy as sa
from alembic import op

revision = "20261115_02"
down_revision = "20261115_01"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if inspector.has_table("reminders", schema="public"):
        required = {
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
        }
        columns = {row["name"] for row in inspector.get_columns("reminders", schema="public")}
        if not required.issubset(columns):
            raise RuntimeError("public.reminders имеет другую схему: требуется отдельная миграция")
        connection.execute(
            sa.text("""
            INSERT INTO app.reminders (id,telegram_user_id,chat_id,text,material_url,frequency,
                timezone,starts_at,next_run_at,status,last_sent_at,created_at)
            SELECT id,telegram_user_id,chat_id,text,material_url,frequency,
                timezone,starts_at,next_run_at,status,last_sent_at,created_at
            FROM public.reminders ON CONFLICT (id) DO NOTHING
        """)
        )
    if not inspector.has_table("assistant_dialogs", schema="public"):
        return
    for row in connection.execute(sa.text("SELECT * FROM public.assistant_dialogs")).mappings():
        old = row["profile"] or {}
        user_id = row["telegram_user_id"]
        stage = str(old.get("stage", "")).casefold()
        business_stage = (
            "owner" if stage in {"уже открылись", "работает", "веду бизнес"} else "unknown"
        )
        connection.execute(
            sa.text("""
            INSERT INTO app.profiles (user_id,business_stage,industry,tax_regime,goal,timezone,preferred_access)
            VALUES (:user_id,:stage,:industry,:tax_regime,:goal,:timezone,:access)
            ON CONFLICT (user_id) DO NOTHING
        """),
            {
                "user_id": user_id,
                "stage": business_stage,
                "industry": old.get("business_type"),
                "tax_regime": old.get("tax_regime"),
                "goal": old.get("goal"),
                "timezone": old.get("timezone"),
                "access": old.get("preferred_access")
                if old.get("preferred_access") in {"free", "paid", "any"}
                else "any",
            },
        )
        connection.execute(
            sa.text("""
            INSERT INTO app.dialogs (user_id,pending,recent_materials,version)
            VALUES (:user_id,CAST(:pending AS jsonb),'[]'::jsonb,1)
            ON CONFLICT (user_id) DO NOTHING
        """),
            {"user_id": user_id, "pending": json.dumps(row["pending"] or {}, ensure_ascii=False)},
        )
        has_history = connection.execute(
            sa.text("SELECT 1 FROM app.messages WHERE user_id=:id LIMIT 1"), {"id": user_id}
        ).first()
        if not has_history:
            for message in (row["history"] or [])[-10:]:
                if message.get("role") in {"user", "assistant"} and message.get("text"):
                    connection.execute(
                        sa.text(
                            "INSERT INTO app.messages(user_id,role,content) VALUES (:id,:role,:text)"
                        ),
                        {
                            "id": user_id,
                            "role": message["role"],
                            "text": message["text"],
                        },
                    )


def downgrade():
    # Backfill не меняет исходные таблицы. Откат сохраняет перенесённые записи:
    # их нельзя отличить от уже отредактированных пользователем без потери данных.
    pass
