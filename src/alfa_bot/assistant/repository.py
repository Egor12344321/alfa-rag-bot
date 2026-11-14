from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from alfa_bot.contracts import (
    Evidence,
    HandledTurn,
    Profile,
    UserTurn,
)


@dataclass
class AssistantState:
    version: int
    history: list[str] = field(default_factory=list)
    profile: Profile = field(default_factory=Profile)
    pending_question: str | None = None
    pending_clarification: str | None = None


class AssistantRepository(Protocol):
    async def get_handled(
        self,
        update_id: int,
    ) -> HandledTurn | None: ...

    async def load_state(
        self,
        conversation_id: UUID,
    ) -> AssistantState: ...

    async def save_result(
        self,
        *,
        turn: UserTurn,
        expected_version: int,
        text: str,
        outcome: str,
        run_id: UUID | None = None,
        evidence: tuple[Evidence, ...] = (),
        new_state: AssistantState | None = None,
    ) -> HandledTurn: ...


class UnconfiguredAssistantRepository:
    """Временная заглушка до подключения PostgreSQL."""

    def __init__(self, sessions: object):
        self.sessions = sessions

    async def get_handled(
        self,
        update_id: int,
    ) -> HandledTurn | None:
        raise NotImplementedError("AssistantRepository пока не подключён к БД")

    async def load_state(
        self,
        conversation_id: UUID,
    ) -> AssistantState:
        raise NotImplementedError("Загрузка состояния диалога пока не реализована")

    async def save_result(
        self,
        *,
        turn: UserTurn,
        expected_version: int,
        text: str,
        outcome: str,
        run_id: UUID | None = None,
        evidence: tuple[Evidence, ...] = (),
        new_state: AssistantState | None = None,
    ) -> HandledTurn:
        raise NotImplementedError("Сохранение ответа пока не реализовано")
