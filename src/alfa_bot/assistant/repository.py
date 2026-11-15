"""Короткие транзакции; сетевые вызовы LLM выполняются за их пределами."""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alfa_bot.assistant.models import DialogModel, Message, Profile, TurnResult
from alfa_bot.contracts import Reply, UserProfile


class ConcurrentTurnError(RuntimeError):
    """Более новый запрос уже изменил диалог."""


@dataclass
class Dialog:
    user_id: int
    version: int
    profile: UserProfile
    pending: dict = field(default_factory=dict)
    recent_materials: list[dict] = field(default_factory=list)
    last_update_id: int | None = None
    last_reply: dict | None = None


class AssistantRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    async def get_reply(self, user_id: int, update_id: int | None) -> Reply | None:
        if update_id is None:
            return None
        async with self.sessions() as session:
            row = await session.get(TurnResult, (user_id, update_id))
            return Reply.model_validate(row.reply) if row else None

    async def load(self, user_id: int) -> Dialog:
        async with self.sessions.begin() as session:
            defaults = UserProfile(user_id=user_id).model_dump()
            await session.execute(insert(Profile).values(**defaults).on_conflict_do_nothing())
            await session.execute(
                insert(DialogModel)
                .values(
                    user_id=user_id,
                    pending={},
                    recent_materials=[],
                    version=1,
                )
                .on_conflict_do_nothing()
            )
            profile = await session.get(Profile, user_id)
            state = await session.get(DialogModel, user_id)
            values = {key: getattr(profile, key) for key in defaults}
            return Dialog(
                user_id=user_id,
                version=state.version,
                profile=UserProfile(**values),
                pending=deepcopy(state.pending),
                recent_materials=deepcopy(state.recent_materials),
                last_update_id=state.last_update_id,
                last_reply=deepcopy(state.last_reply),
            )

    async def history(self, user_id: int, limit: int) -> list[BaseMessage]:
        async with self.sessions() as session:
            rows = list(
                await session.scalars(
                    select(Message)
                    .where(
                        Message.user_id == user_id,
                    )
                    .order_by(Message.id.desc())
                    .limit(limit)
                )
            )
        rows.reverse()
        return [
            HumanMessage(content=row.content)
            if row.role == "user"
            else AIMessage(content=row.content)
            for row in rows
        ]

    async def commit_turn(
        self,
        dialog: Dialog,
        messages: Sequence[BaseMessage],
        *,
        reply: Reply,
        update_id: int | None,
        history_limit: int,
    ) -> None:
        async with self.sessions.begin() as session:
            result = await session.execute(
                update(DialogModel)
                .where(
                    DialogModel.user_id == dialog.user_id,
                    DialogModel.version == dialog.version,
                )
                .values(
                    pending=dialog.pending,
                    recent_materials=dialog.recent_materials,
                    version=dialog.version + 1,
                    last_update_id=update_id,
                    last_reply=reply.model_dump(mode="json"),
                )
            )
            if result.rowcount != 1:
                raise ConcurrentTurnError("Диалог изменён другим обработчиком")
            if update_id is not None:
                session.add(
                    TurnResult(
                        user_id=dialog.user_id,
                        update_id=update_id,
                        reply=reply.model_dump(mode="json"),
                    )
                )
            values = dialog.profile.model_dump(exclude={"user_id"})
            await session.execute(
                update(Profile).where(Profile.user_id == dialog.user_id).values(**values)
            )
            session.add_all(
                [
                    Message(
                        user_id=dialog.user_id,
                        role="user" if isinstance(item, HumanMessage) else "assistant",
                        content=str(item.content)[:12000],
                    )
                    for item in messages
                ]
            )
            await session.flush()
            # Храним ограниченную историю, а не бесконечный журнал персональных данных.
            obsolete = (
                select(Message.id)
                .where(Message.user_id == dialog.user_id)
                .order_by(
                    Message.id.desc(),
                )
                .offset(history_limit)
                .subquery()
            )
            await session.execute(delete(Message).where(Message.id.in_(select(obsolete.c.id))))
        dialog.version += 1

    async def reset(self, user_id: int) -> None:
        # Загружаем строку до reset, чтобы одинаково обрабатывать нового пользователя.
        await self.load(user_id)
        async with self.sessions.begin() as session:
            await session.execute(
                update(Profile)
                .where(Profile.user_id == user_id)
                .values(
                    **UserProfile(user_id=user_id).model_dump(exclude={"user_id"}),
                )
            )
            await session.execute(
                update(DialogModel)
                .where(DialogModel.user_id == user_id)
                .values(
                    pending={},
                    recent_materials=[],
                    version=DialogModel.version + 1,
                    last_update_id=None,
                    last_reply=None,
                )
            )
            await session.execute(delete(Message).where(Message.user_id == user_id))
