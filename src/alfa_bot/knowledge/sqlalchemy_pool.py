"""Checkout psycopg connections from the application's SQLAlchemy pool."""

from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine


class SQLAlchemyPool:
    def __init__(self, engine: AsyncEngine):
        self._engine = engine

    @asynccontextmanager
    async def connection(self):
        async with self._engine.connect() as connection:
            pooled = await connection.get_raw_connection()
            yield pooled.driver_connection
