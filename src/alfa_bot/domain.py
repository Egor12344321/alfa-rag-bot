"""Внутренние DTO и порты contributed assistant/knowledge.

Публичный Telegram API находится в contracts.py; integration.py преобразует
внутренний результат в Reply и связывает UUID-контекст с Telegram-владельцем.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol
from uuid import UUID

AccessType = Literal["free", "paid", "subscription", "unknown"]
RepeatKind = Literal["once", "daily", "weekly", "monthly"]


class ValidationError(Exception):
    """Некорректные входные значения; не ошибка зависимости."""


class NotFound(Exception):
    """Объект отсутствует или недоступен этому пользователю."""


class Conflict(Exception):
    """Версия объекта/черновика уже изменилась."""


class DependencyUnavailable(Exception):
    """Временно недоступны БД, embeddings или генератор."""


@dataclass(frozen=True)
class Profile:
    stage: str | None = None
    industry: str | None = None
    experience: str | None = None
    current_goal: str | None = None
    tax_regime: str | None = None
    free_only: bool = False
    timezone: str | None = None


@dataclass(frozen=True)
class SourceKey:
    source_table: str
    source_id: str


@dataclass(frozen=True)
class Evidence:
    chunk_id: UUID
    material_id: UUID
    source: SourceKey
    title: str
    text: str
    url: str | None
    distance: float  # cosine distance, НЕ вероятность правильности


@dataclass(frozen=True)
class SearchResult:
    run_id: UUID
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class MaterialCard:
    id: UUID
    run_id: UUID
    source: SourceKey
    kind: str
    title: str
    public_summary: str
    url: str  # только разрешённая предоставленная ссылка
    access_type: AccessType
    price: Decimal | None = None
    currency: str | None = None
    price_note: str | None = None
    reason_features: tuple[str, ...] = ()
    score: float | None = None


class Knowledge(Protocol):
    async def retrieve(
        self,
        query: str,
        *,
        limit: int = 6,
        run_id: UUID | None = None,
        external_context: bool = True,
    ) -> SearchResult:
        """Только knowledge-фрагменты. Пустой evidence — штатный результат.

        Если run_id не передан, выбрать active_run_id. external_context=True
        дополнительно требует external_allowed. Профиль не фильтрует доступ.
        """
        ...

    async def recommend(
        self,
        query: str,
        profile: Profile,
        *,
        run_id: UUID,
        exclude: tuple[SourceKey, ...] = (),
        limit: int = 1,
    ) -> tuple[MaterialCard, ...]:
        """Активные разрешённые карточки того же run; free_only — жёсткий фильтр."""
        ...

    async def get_card(
        self,
        source: SourceKey,
        *,
        run_id: UUID | None = None,
    ) -> MaterialCard | None:
        """По исходному ключу разрешить карточку указанной/активной версии."""
        ...


@dataclass(frozen=True)
class UserTurn:
    update_id: int
    user_id: UUID
    conversation_id: UUID
    text: str
    received_at: datetime  # aware datetime


@dataclass(frozen=True)
class HandledTurn:
    message_id: UUID  # opaque internal result marker; public Reply is persisted by the adapter
    delivery_ids: tuple[UUID, ...]
    outcome: Literal["answer", "clarification", "no_answer", "reminder", "error"]


@dataclass(frozen=True)
class ReminderIntent:
    text: str | None
    local_start: datetime | None  # naive local datetime + отдельная IANA timezone
    timezone: str | None
    repeat_kind: RepeatKind | None
    interval_n: int = 1
    material: SourceKey | None = None
    date_origin: Literal["user", "verified_source"] = "user"
    date_source_note: str | None = None


@dataclass(frozen=True)
class ReminderDraft:
    draft_id: UUID
    version: int
    intent: ReminderIntent
    missing_fields: tuple[str, ...]
    preview_text: str
    next_run_at: datetime | None  # aware UTC; None пока нельзя подтверждать
    expires_at: datetime | None = None
    material_url: str | None = None


class Reminders(Protocol):
    async def prepare(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        intent: ReminderIntent,
        now: datetime,
    ) -> ReminderDraft:
        """Сохраняет только черновик conversation; now передаётся явно для тестов."""
        ...
