import asyncio
import io
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alfa_bot.assistant.models import AssistantDialog
from alfa_bot.reminders.models import Reminder
from alfa_bot.reminders.scheduler import ReminderScheduler
from alfa_bot.reminders.service import ReminderService

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def test_postgresql_migrations_can_be_rendered_as_sql(monkeypatch):
    output = io.StringIO()
    monkeypatch.setenv("DATABASE_URL", "")
    config = Config(str(ALEMBIC_INI), output_buffer=output)
    try:
        command.upgrade(config, "head", sql=True)
    except CommandError as error:
        pytest.fail(str(error))
    sql = output.getvalue()
    assert "CREATE TABLE IF NOT EXISTS public.assistant_dialogs" in sql
    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "CREATE TABLE kb.chunks" in sql
    assert "embedding VECTOR NOT NULL" in sql


@pytest.fixture
def postgres_database(monkeypatch):
    configured = os.environ.get("ALFA_TEST_POSTGRES_URL")
    if not configured:
        pytest.skip("Укажите ALFA_TEST_POSTGRES_URL для интеграционной проверки PostgreSQL")
    admin = make_url(configured)
    name = f"alfa_migrations_{uuid4().hex}"
    url = admin.set(database=name)

    async def admin_execute(statement):
        engine = create_async_engine(admin, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as connection:
                await connection.execute(text(statement))
        finally:
            await engine.dispose()

    asyncio.run(admin_execute(f'CREATE DATABASE "{name}"'))
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    try:
        yield url
    finally:
        asyncio.run(admin_execute(f'DROP DATABASE "{name}"'))


def test_migrations_keep_the_existing_dialog_and_reminder(postgres_database):
    async def prepare_legacy_data():
        engine = create_async_engine(postgres_database)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(AssistantDialog.__table__.create)
                await connection.run_sync(Reminder.__table__.create)
                await connection.execute(
                    AssistantDialog.__table__.insert().values(
                        telegram_user_id=123,
                        profile={"stage": "запуск"},
                        history=[{"role": "user", "text": "Первый вопрос"}],
                        version=4,
                    )
                )
                await connection.execute(
                    text(
                        "INSERT INTO reminders "
                        "(id, telegram_user_id, chat_id, text, timezone, starts_at, next_run_at) "
                        "VALUES (gen_random_uuid(), 123, 123, 'Читать', 'Europe/Moscow', "
                        "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    )
                )
        finally:
            await engine.dispose()

    async def check_data():
        engine = create_async_engine(postgres_database)
        try:
            async with engine.connect() as connection:
                dialog = (
                    await connection.execute(
                        text("SELECT profile, history, version FROM assistant_dialogs")
                    )
                ).one()
                assert dialog == (
                    {"stage": "запуск"},
                    [{"role": "user", "text": "Первый вопрос"}],
                    4,
                )
                reminder = await connection.execute(text("SELECT text FROM reminders"))
                assert reminder.scalar_one() == "Читать"
        finally:
            await engine.dispose()

    asyncio.run(prepare_legacy_data())
    command.upgrade(Config(str(ALEMBIC_INI)), "0001_app_schema")
    asyncio.run(check_data())


def test_fresh_database_accepts_catalog_but_rejects_paid_answer_body(postgres_database):
    config = Config(str(ALEMBIC_INI))
    command.upgrade(config, "head")
    command.upgrade(config, "head")

    async def check_schema():
        engine = create_async_engine(postgres_database)
        insert_chunk = text(
            "INSERT INTO kb.chunks (id, text, embedding, material_id, purpose, metadata) "
            "VALUES (:id, 'Материал', '[1,0]', 'paid', :purpose, "
            "CAST(:metadata AS jsonb))"
        )
        paid_course = '{"kind":"course","access":"paid"}'
        try:
            async with engine.begin() as connection:
                await connection.execute(
                    insert_chunk,
                    {"id": "course-card", "purpose": "catalog", "metadata": paid_course},
                )
                await connection.execute(
                    text(
                        "INSERT INTO kb.index_state (id, model_name, dimensions, fingerprint) "
                        "VALUES (1, 'test', 2, 'cache-key')"
                    )
                )
                dimensions = await connection.execute(text("SELECT dimensions FROM kb.index_state"))
                assert dimensions.scalar_one() == 2
                assert (
                    await connection.execute(text("SELECT count(*) FROM assistant_dialogs"))
                ).scalar_one() == 0
            with pytest.raises(IntegrityError, match="ck_chunks_answer_access"):
                async with engine.begin() as connection:
                    await connection.execute(
                        insert_chunk,
                        {"id": "body", "purpose": "knowledge", "metadata": paid_course},
                    )
        finally:
            await engine.dispose()

    asyncio.run(check_schema())


def test_scheduler_delivers_and_can_disable_repeats(postgres_database):
    command.upgrade(Config(str(ALEMBIC_INI)), "0001_app_schema")

    async def scenario():
        engine = create_async_engine(postgres_database)
        try:
            start = datetime(2026, 11, 14, 10, tzinfo=UTC)
            now = start
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            reminders = ReminderService(sessions)
            bot = AsyncMock()
            scheduler = ReminderScheduler(sessions, bot, clock=lambda: now)
            for frequency in ("once", "daily"):
                draft = reminders.prepare(
                    telegram_user_id=123,
                    chat_id=123,
                    text="Продолжить обучение",
                    local_datetime=datetime(2026, 11, 14, 13, 2),
                    timezone_name="Europe/Moscow",
                    frequency=frequency,
                    now=start,
                )
                saved = await reminders.confirm(
                    draft=draft, telegram_user_id=123, chat_id=123, now=start,
                )
                if frequency == "daily":
                    repeating = saved

            now = start + timedelta(minutes=2)
            assert await scheduler.run_once() == 2
            assert await scheduler.run_once() == 0
            assert bot.send_message.await_count == 2
            assert all(call.kwargs["chat_id"] == 123 for call in bot.send_message.await_args_list)

            updated = await reminders.change_frequency(
                reminder_id=repeating.id, telegram_user_id=123, frequency="weekly", now=now,
            )
            assert updated.next_run_at == now + timedelta(weeks=1)
            await reminders.disable(reminder_id=repeating.id, telegram_user_id=123)
            now += timedelta(weeks=1)
            assert await scheduler.run_once() == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())
