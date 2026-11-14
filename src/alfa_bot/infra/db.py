from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@dataclass(frozen=True)
class Readiness:
    ready: bool
    reason: str


class DatabasePort(Protocol):
    async def readiness(self) -> Readiness: ...
    async def close(self) -> None: ...


class Database:
    def __init__(self, url: str):
        self.engine = create_async_engine(
            url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 3},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def readiness(self) -> Readiness:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
                active = await conn.scalar(
                    text("""
                    SELECT EXISTS (
                        SELECT 1 FROM kb.index_state s
                        JOIN kb.ingestion_runs r ON r.id = s.active_run_id
                        WHERE r.status = 'ready'
                    )
                """)
                )
        except SQLAlchemyError:
            # Не возвращать пользователю exception, SQL, URL или пароль.
            return Readiness(False, "database_or_migrations_unavailable")
        if not active:
            return Readiness(False, "knowledge_index_missing")
        # Проверка БД/индекса не означает готовность пока отсутствующей бизнес-логики.
        return Readiness(False, "business_modules_not_implemented")

    async def close(self) -> None:
        await self.engine.dispose()
