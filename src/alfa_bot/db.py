"""Общие подключения, без создания таблиц при старте приложения."""

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str):
        # The knowledge repository uses psycopg transactions on the shared pool.
        url = make_url(url).set(drivername="postgresql+psycopg")
        self.engine = create_async_engine(url, pool_pre_ping=True, echo=False, hide_parameters=True)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def close(self):
        await self.engine.dispose()
