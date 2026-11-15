"""Публичные DTO Telegram, ответов и управления напоминаниями."""

from datetime import datetime
from enum import StrEnum
from typing import Literal, Protocol
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class UserProfile(DTO):
    """Сохранённые сведения и предпочтения пользователя."""

    user_id: int = Field(gt=0)
    business_stage: Literal["beginner", "owner", "solo_founder", "unknown"] = "unknown"
    experience_level: Literal["novice", "experienced", "unknown"] = "unknown"
    industry: str | None = Field(default=None, max_length=200)
    tax_regime: str | None = Field(default=None, max_length=100)
    response_style: Literal["auto", "brief", "detailed", "template", "learning"] = "auto"
    preferred_access: Literal["any", "free", "paid"] = "any"
    wants_courses: bool | None = None
    goal: str | None = Field(default=None, max_length=500)
    timezone: str | None = Field(default=None, max_length=64)


class Material(DTO):
    """Карточка материала с разрешённой ссылкой и известными условиями доступа."""

    id: str
    source_type: Literal["article", "document", "glossary", "course"]
    source_id: str
    title: str = Field(min_length=1)
    description: str = ""
    url: str | None = None
    is_paid: bool | None = None
    price: str | None = None
    level: Literal["intro", "advanced", "any"] = "any"
    audience: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        if value is None:
            return None
        try:
            parts = urlsplit(value)
            valid = (
                parts.scheme in {"http", "https"}
                and bool(parts.hostname)
                and parts.username is None
                and parts.password is None
                and not any(c.isspace() for c in value)
            )
            _ = parts.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Ожидается HTTP(S)-ссылка без логина и пароля")
        return value


class RetrievedChunk(DTO):
    id: UUID
    text: str
    score: float = Field(ge=-1.00001, le=1.00001)
    material: Material


class SearchRequest(DTO):
    query: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=5, ge=1, le=20)
    score_threshold: float = Field(default=0.6, ge=-1, le=1)
    purpose: Literal["knowledge", "catalog"] = "knowledge"
    profile: UserProfile | None = None


class SearchResult(DTO):
    chunks: list[RetrievedChunk] = Field(default_factory=list)
    best_score: float | None = None


class Recommendation(DTO):
    material: Material
    reason: str
    score: float = Field(ge=-1.00001, le=1.00001)


class KnowledgePort(Protocol):
    async def search(self, request: SearchRequest) -> SearchResult: ...
    async def recommend(
        self,
        query: str,
        profile: UserProfile,
        *,
        exclude: set[str] | None = None,
        limit: int = 1,
    ) -> list[Recommendation]: ...
    async def get_material(self, material_id: str) -> Material | None: ...


class ReminderIntent(DTO):
    """Проверенное локальное время и параметры создаваемого напоминания."""

    text: str = Field(min_length=1, max_length=1000)
    local_datetime: datetime
    timezone: str
    frequency: Literal["once", "daily", "weekly", "monthly"] = "once"
    material_url: str | None = None

    @field_validator("material_url")
    @classmethod
    def valid_material_url(cls, value):
        return Material.valid_url(value)

    @field_validator("local_datetime")
    @classmethod
    def local_time_is_naive(cls, value):
        if value.tzinfo is not None:
            raise ValueError("Местное время передаётся без tzinfo")
        return value


class ReminderDraft(DTO):
    """Серверный черновик: напоминание появится только после подтверждения."""

    id: UUID
    text: str
    starts_at: datetime
    timezone: str
    frequency: str
    material_url: str | None = None
    expires_at: datetime

    @field_validator("material_url")
    @classmethod
    def valid_material_url(cls, value):
        return Material.valid_url(value)


class ReminderView(DTO):
    """Текущее состояние подтверждённого напоминания."""

    id: UUID
    text: str
    starts_at: datetime
    next_run_at: datetime
    timezone: str
    frequency: str
    status: str
    material_url: str | None = None

    @field_validator("material_url")
    @classmethod
    def valid_material_url(cls, value):
        return Material.valid_url(value)


class Answer(DTO):
    """Текст ответа, источники и отдельные рекомендованные карточки."""

    text: str = Field(min_length=1)
    sources: list[Material] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)


class ReplyType(StrEnum):
    ANSWER = "answer"
    REMINDER = "reminder"
    CLARIFICATION = "clarification"
    OUT_OF_SCOPE = "out_of_scope"


class Reply(DTO):
    """Ответ assistant для Telegram: тип определяет допустимые поля."""

    type: ReplyType
    text: str = ""
    answer: Answer | None = None
    draft: ReminderDraft | None = None

    @model_validator(mode="after")
    def correct_payload(self):
        if self.type == ReplyType.ANSWER:
            valid = self.answer is not None and self.draft is None and not self.text
        elif self.type == ReplyType.REMINDER:
            valid = self.draft is not None and self.answer is None and not self.text
        elif self.type == ReplyType.CLARIFICATION:
            valid = bool(self.text.strip()) and self.answer is None and self.draft is None
        else:
            valid = self.answer is None and self.draft is None
        if not valid:
            raise ValueError("Поля Reply не соответствуют типу")
        return self


class UserRequest(DTO):
    """Входное сообщение Telegram с владельцем, временем и ключом повтора."""

    user_id: int = Field(gt=0)
    chat_id: int
    text: str = Field(max_length=8000)
    now: datetime
    update_id: int | None = None

    @field_validator("now")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("now должен содержать часовой пояс")
        return value


class ReminderAction(DTO):
    """Действие кнопки или команды над напоминанием текущего пользователя."""

    user_id: int = Field(gt=0)
    chat_id: int
    action: Literal[
        "list",
        "show",
        "confirm",
        "cancel_draft",
        "frequency",
        "disable",
        "resume",
        "delete",
    ]
    reminder_id: UUID | None = None
    frequency: Literal["once", "daily", "weekly", "monthly"] | None = None
    now: datetime

    @field_validator("now")
    @classmethod
    def aware(cls, value):
        return UserRequest.aware(value)


class ActionResult(DTO):
    """Результат управления: уведомление, одна карточка либо список."""

    text: str = ""
    reminder: ReminderView | None = None
    items: list[ReminderView] = Field(default_factory=list)


class AssistantPort(Protocol):
    async def handle(self, request: UserRequest) -> Reply: ...
    async def reminder_action(self, request: ReminderAction) -> ActionResult: ...


class Delivery(DTO):
    id: UUID
    chat_id: int
    text: str
    material_url: str | None = None

    @field_validator("material_url")
    @classmethod
    def valid_material_url(cls, value):
        return Material.valid_url(value)


class DeliveryRejected(Exception):
    """Постоянная ошибка доставки: пользователь заблокировал бота и т.п."""


class DeliveryRetry(Exception):
    def __init__(self, seconds: float = 5):
        self.seconds = seconds


class DeliveryPort(Protocol):
    async def send(self, delivery: Delivery) -> None: ...
