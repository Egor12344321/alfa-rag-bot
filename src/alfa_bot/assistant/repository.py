from copy import deepcopy
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alfa_bot.assistant.models import AssistantDialog


class ConcurrentTurnError(RuntimeError):
    pass


@dataclass
class Dialog:
    telegram_user_id: int
    version: int
    profile: dict = field(default_factory=dict)
    history: list = field(default_factory=list)
    pending: dict = field(default_factory=dict)
    recent_materials: list = field(default_factory=list)


class AssistantRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    async def init_db(self) -> None:
        # Только собственная таблица. Не вызывает Base.metadata.create_all().
        async with self.sessions() as session:
            connection = await session.connection()
            await connection.run_sync(AssistantDialog.__table__.create, checkfirst=True)
            await session.commit()

    async def load(self, telegram_user_id: int) -> Dialog:
        async with self.sessions.begin() as session:
            dialect = session.bind.dialect.name
            if dialect not in {"postgresql", "sqlite"}:
                raise ValueError("Поддерживаются PostgreSQL и SQLite для тестов")
            insert = pg_insert if dialect == "postgresql" else sqlite_insert
            await session.execute(
                insert(AssistantDialog)
                .values(
                    telegram_user_id=telegram_user_id,
                    profile={},
                    history=[],
                    pending={},
                    recent_materials=[],
                    version=1,
                )
                .on_conflict_do_nothing(index_elements=["telegram_user_id"])
            )
            row = await session.scalar(
                select(AssistantDialog).where(
                    AssistantDialog.telegram_user_id == telegram_user_id,
                )
            )
            return Dialog(
                telegram_user_id,
                row.version,
                deepcopy(row.profile),
                deepcopy(row.history),
                deepcopy(row.pending),
                deepcopy(row.recent_materials),
            )

    async def save(self, dialog: Dialog) -> None:
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(AssistantDialog)
                .where(
                    AssistantDialog.telegram_user_id == dialog.telegram_user_id,
                    AssistantDialog.version == dialog.version,
                )
                .values(
                    profile=dialog.profile,
                    history=dialog.history,
                    pending=dialog.pending,
                    recent_materials=dialog.recent_materials,
                    version=dialog.version + 1,
                )
            )
            if result.rowcount != 1:
                raise ConcurrentTurnError("Диалог изменён другим обработчиком")
            dialog.version += 1
