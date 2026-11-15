"""Alembic использует DATABASE_URL из .env; пароли не попадают в ini и логи."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from alfa_bot.assistant import models as _assistant_models  # noqa: F401
from alfa_bot.config import load_config
from alfa_bot.db import Base
from alfa_bot.reminders import models as _reminder_models  # noqa: F401

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_with_connection(connection):
    def include_name(name, type_, parent_names):
        # Старые public-таблицы и чужие схемы не должны попадать в autogenerate DROP.
        if type_ == "schema":
            return name in {"app", "kb"}
        return True

    def include_object(obj, name, type_, reflected, compare_to):
        # The contribution manages kb with explicit SQL migrations.
        # Autogenerate must never propose dropping those non-ORM tables.
        return not (getattr(obj, "schema", None) == "kb")

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_schemas=True,
        compare_type=True,
        include_name=include_name,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_online():
    url = config.attributes.get("database_url") or load_config().database_url
    if not url:
        raise ValueError("Заполните DATABASE_URL перед запуском миграций")
    url = make_url(url).set(drivername="postgresql+psycopg")
    engine = create_async_engine(url, echo=False)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT pg_advisory_lock(471225, 1)"))
            await connection.commit()
            try:
                await connection.run_sync(run_with_connection)
            finally:
                if connection.in_transaction():
                    await connection.rollback()
                await connection.execute(text("SELECT pg_advisory_unlock(471225, 1)"))
                await connection.commit()
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Эти миграции проверяют старые таблицы и требуют подключения к PostgreSQL")
else:
    asyncio.run(run_online())
