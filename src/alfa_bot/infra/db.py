from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@dataclass(frozen=True)
class Readiness:
    ready: bool
    reason: str


class DatabasePort(Protocol):
    async def readiness(self) -> Readiness: ...
    async def init_models(self) -> None: ...
    async def close(self) -> None: ...


class Database:
    def __init__(self, url: str):
        self.engine = create_async_engine(
            url,
            pool_pre_ping=True,
            connect_args={"timeout": 3},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator[AsyncSession, None]:
        """Успешный выход: COMMIT; исключение: ROLLBACK; сессия всегда закрывается."""
        async with self.sessions.begin() as session:
            yield session

    async def readiness(self) -> Readiness:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except SQLAlchemyError:
            # Не возвращать пользователю exception, SQL, URL или пароль.
            return Readiness(False, "database_unavailable")
        # Это только доступность PostgreSQL, не проверка индекса или всего бота.
        return Readiness(True, "database_ready")

    async def init_models(self) -> None:
        """Создание расширения vector и таблиц моделей, если они не существуют."""
        from alfa_bot.infra.models import Base
        async with self.engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()
