"""Общие настройки процесса и проверка миграций перед запуском."""

import asyncio
import logging
import os

from sqlalchemy import text

from alfa_bot.db import Database

SCHEMA_REVISION = "20261115_04"


def configure_runtime(log_level: str = "INFO") -> None:
    # История и контекст не должны уходить в автоматическую внешнюю трассировку.
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for name in ["httpx", "httpcore", "sqlalchemy.engine", "langsmith"]:
        logging.getLogger(name).setLevel(logging.WARNING)


async def require_migrations(database: Database) -> None:
    async with database.engine.connect() as connection:
        exists = await connection.scalar(text("SELECT to_regclass('public.alembic_version')"))
        if not exists:
            raise RuntimeError("Сначала выполните python -m alembic upgrade head")
        revision = await connection.scalar(text("SELECT version_num FROM public.alembic_version"))
        if revision != SCHEMA_REVISION:
            raise RuntimeError("Схема БД устарела. Выполните python -m alembic upgrade head")


def run_cli(coroutine, *, operation: str) -> None:
    """Граница CLI: SQLAlchemy traceback может содержать тексты в параметрах."""
    try:
        asyncio.run(coroutine)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except Exception as error:  # noqa: BLE001 — безопасная граница процесса CLI.
        logging.getLogger(__name__).error(
            "%s failed error_type=%s",
            operation,
            type(error).__name__,
        )
        raise SystemExit(
            "Операция не завершена. Проверьте настройки .env, миграции, доступ к БД "
            "и доступность локальной модели. Класс ошибки указан в журнале."
        ) from None
