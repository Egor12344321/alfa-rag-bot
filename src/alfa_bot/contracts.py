from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID
from enum import StrEnum


class ReplyType(StrEnum):
    ANSWER = "answer"
    REMINDER = "reminder"


@dataclass(frozen=True)
class ReminderIntent:
    """Местная дата без tzinfo и часовой пояс IANA."""

    text: str
    local_datetime: datetime
    timezone: str
    frequency: str = "once"
    material_url: str | None = None


@dataclass(frozen=True)
class Reply:
    type: ReplyType
    text: str = ""
    reminder: ReminderIntent | None = None


class AssistantPort(Protocol):
    async def handle(
        self,
        *,
        telegram_user_id: int,
        text: str,
        now: datetime,
    ) -> Reply:
        ...


class ValidationError(Exception):
    """Исключение при некорректных параметрах расписания или контрактов."""
    pass


@dataclass(frozen=True)
class ReminderDraft:
    id: UUID
    telegram_user_id: int
    chat_id: int
    text: str
    frequency: str
    timezone: str
    starts_at: datetime
    material_url: str | None = None


@dataclass(frozen=True)
class ReminderView:
    id: UUID
    text: str
    frequency: str
    timezone: str
    starts_at: datetime
    next_run_at: datetime
    status: str
    material_url: str | None = None


@dataclass(frozen=True)
class RecommendedItem:
    """Карточка рекомендованного курса или материала платформы."""
    material_id: int
    title: str
    url: str
    access_type: str               # 'free' | 'paid' | 'unknown'
    price: float | None = None     # Цена, если платный
    reason: str = ""               # Почему именно этот материал предложен





class AssistantPort(Protocol):
    """Контракт модуля assistant (зона ответственности Python 2)."""

    async def handle(self, user_id: int, text: str):
        """
        Обработка реплики пользователя:
        - ведение профиля
        - RAG-поиск и ответ по фрагментам
        - выявление необходимости уточнения
        - детекция намерения создать напоминание
        """
        ...


