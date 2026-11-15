from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from alfa_bot.domain import (
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
    pending_reminder: dict = field(default_factory=dict)
    recent_materials: tuple = ()


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
        cards: tuple = (),
        draft: object | None = None,
    ) -> HandledTurn: ...
