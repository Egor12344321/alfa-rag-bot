"""Интеграционные тесты запускаются только на явно заданной тестовой БД."""

import os
from pathlib import Path

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from alfa_bot.config import Settings
from alfa_bot.db import Database


@pytest.fixture(scope="session")
def database_url():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Задайте TEST_DATABASE_URL для проверки на PostgreSQL")
    name = make_url(url).database or ""
    if not (name.endswith("_test") or name.startswith("test_")):
        raise ValueError("TEST_DATABASE_URL должен указывать на отдельную *_test/test_* БД")
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    return url


@pytest_asyncio.fixture
async def db(database_url):
    database = Database(database_url)
    async with database.engine.begin() as connection:
        await connection.execute(
            text("""
            TRUNCATE app.turn_results, app.messages, app.dialogs, app.profiles,
                     app.reminder_drafts, app.reminders,
                     kb.chunks, kb.materials, kb.ingestion_runs
            RESTART IDENTITY CASCADE
        """)
        )
    yield database
    await database.close()


@pytest.fixture
def settings():
    return Settings(_env_file=None)
