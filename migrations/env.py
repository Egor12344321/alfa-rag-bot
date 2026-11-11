import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from alfa_bot.infra.config import Settings

# Схема пока SQL-first. metadata=None запрещает преждевременный autogenerate.
target_metadata = None


def run_offline() -> None:
    settings = Settings()
    context.configure(
        url=settings.database_url.get_secret_value(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_on_connection(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_online() -> None:
    engine = create_async_engine(Settings().database_url.get_secret_value())
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_on_connection)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_offline()
else:
    asyncio.run(run_online())
