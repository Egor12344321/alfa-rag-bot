"""Alembic читает то же подключение, что и приложение."""

import asyncio

from alembic import context
from alembic.util.exc import CommandError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alfa_bot.infra.config import Settings


def run_offline() -> None:
    context.configure(dialect_name="postgresql", literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_on_connection(connection) -> None:
    context.configure(connection=connection)
    with context.begin_transaction():
        # Блокировка охватывает чтение alembic_version и применение всех ревизий.
        connection.execute(text("SELECT pg_advisory_xact_lock(519879024216151104)"))
        context.run_migrations()


async def run_online() -> None:
    url = Settings().database_url
    if not url:
        raise CommandError("Для применения миграций задайте DATABASE_URL")
    engine = create_async_engine(url, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_on_connection)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
