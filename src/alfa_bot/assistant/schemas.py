
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ReminderSlots(BaseModel):
    text: str | None = None
    local_start: datetime | None = Field(
        None,
        description="Наивное локальное время, без tzinfo",
    )
    timezone: str | None = Field(
        None,
        description="IANA, например Europe/Moscow",
    )
    repeat_kind: Literal["once", "daily", "weekly"] | None = None
    interval_n: int = Field(default=1, ge=1)
    material_query: str | None = Field(
        None,
        description="Если речь о материале ('этот курс'), его описание для поиска",
    )

    @field_validator("local_start")
    @classmethod
    def validate_local_start(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            raise ValueError("local_start должен быть без часового пояса")
        return value


class ProfilePatch(BaseModel):
    stage: str | None = Field(
        None,
        description="Стадия бизнеса, только если пользователь явно её сообщил",
    )
    industry: str | None = Field(
        None,
        description="Отрасль деятельности пользователя",
    )
    experience: str | None = Field(
        None,
        description="Опыт пользователя в предпринимательстве",
    )
    current_goal: str | None = Field(
        None,
        description="Текущая цель пользователя",
    )
    tax_regime: str | None = Field(
        None,
        description="Налоговый режим пользователя",
    )
    free_only: bool | None = Field(
        None,
        description="Ищет ли пользователь только бесплатные материалы",
    )


class IntentResult(BaseModel):
    intent: Literal["consult", "reminder", "smalltalk", "out_of_scope"]
    needs_clarification: bool = False
    clarification_question: str | None = None
    reminder: ReminderSlots | None = None
    profile_patch: ProfilePatch | None = None


class AnswerDraft(BaseModel):
    enough_info: bool = Field(description="Хватает ли источников для ответа")
    answer: str = Field(description="Текст с цитатами вида [1], [2]")
    missing: list[str] = Field(default_factory=list)
