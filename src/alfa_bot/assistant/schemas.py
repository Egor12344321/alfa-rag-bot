import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AnswerStyle = Literal["balanced", "brief", "detailed", "checklist", "template"]
MaterialFilter = Literal["any", "article", "course", "other"]
AccessFilter = Literal["any", "free", "paid"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProfilePatch(StrictModel):
    business_type: str | None = Field(default=None, max_length=150)
    stage: str | None = Field(default=None, max_length=150)
    business_stage: Literal["planning", "running", "growth"] | None = None
    experience_level: Literal["beginner", "experienced"] | None = None
    industry: str | None = Field(default=None, max_length=150)
    tax_regime: str | None = Field(default=None, max_length=64)
    location: str | None = Field(default=None, max_length=150)
    goal: str | None = Field(default=None, max_length=300)
    timezone: str | None = Field(default=None, max_length=64)
    preferred_access: AccessFilter | None = None
    response_style: AnswerStyle | None = None
    course_preference: Literal["welcome", "avoid", "any"] | None = None


class ReminderFields(StrictModel):
    text: str | None = Field(default=None, max_length=1000)
    local_datetime: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    frequency: Literal["once", "daily", "weekly", "monthly"] | None = None
    material_id: str | None = Field(default=None, max_length=200)


class Decision(StrictModel):
    intent: Literal["knowledge", "recommendation", "reminder", "profile", "out_of_scope"]
    query: str = Field(default="", max_length=1200)
    clarification: str | None = Field(default=None, max_length=300)
    continue_pending: bool = False
    profile: ProfilePatch = Field(default_factory=ProfilePatch)
    reminder: ReminderFields = Field(default_factory=ReminderFields)
    response_style: AnswerStyle | None = None
    requested_kind: MaterialFilter | None = None
    requested_access: AccessFilter | None = None
    learning_goal: bool | None = None
    omit_recommendations: bool | None = None
    omit_courses: bool | None = None


class Paragraph(StrictModel):
    text: str = Field(min_length=1, max_length=1800)
    chunk_ids: list[str] = Field(min_length=1, max_length=6)

    @field_validator("text")
    @classmethod
    def plain_text(cls, value: str) -> str:
        if not value.strip() or re.search(
            r"\b(?:[a-z][a-z0-9+.-]*://|mailto:|tel:|www\.)|"
            r"\b(?:[a-z0-9-]+\.)+(?:[a-z]{2,24}|рф)\b|\[[0-9]+\]",
            value,
            re.IGNORECASE,
        ):
            raise ValueError("Текст без URL и номеров ссылок")
        return value.strip()


class GroundedAnswer(StrictModel):
    answerable: bool
    paragraphs: list[Paragraph] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def consistent(self):
        if self.answerable != bool(self.paragraphs):
            raise ValueError("При отсутствии ответа paragraphs должен быть пустым")
        return self
