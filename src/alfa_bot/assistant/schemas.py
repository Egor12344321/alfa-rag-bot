import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfilePatch(StrictModel):
    business_type: str | None = Field(default=None, max_length=150)
    stage: str | None = Field(default=None, max_length=150)
    location: str | None = Field(default=None, max_length=150)
    goal: str | None = Field(default=None, max_length=300)
    timezone: str | None = Field(default=None, max_length=64)
    preferred_access: Literal["free", "paid", "any"] | None = None


class ReminderFields(StrictModel):
    text: str | None = Field(default=None, max_length=1000)
    local_datetime: datetime | None = None
    timezone: str | None = Field(default=None, max_length=64)
    frequency: Literal["once", "daily", "weekly", "monthly"] | None = None
    material_id: str | None = Field(default=None, max_length=200)


class Decision(StrictModel):
    intent: Literal["knowledge", "recommendation", "reminder", "out_of_scope"]
    query: str = Field(default="", max_length=2000)
    clarification: str | None = Field(default=None, max_length=700)
    continue_pending: bool = False
    profile: ProfilePatch = Field(default_factory=ProfilePatch)
    reminder: ReminderFields = Field(default_factory=ReminderFields)
    requested_kind: Literal["any", "article", "course", "other"] = "any"
    requested_access: Literal["any", "free", "paid"] = "any"


class Paragraph(StrictModel):
    text: str = Field(min_length=1, max_length=1800)
    chunk_ids: list[str] = Field(min_length=1, max_length=6)

    @field_validator("text")
    @classmethod
    def plain_text(cls, value):
        if not value.strip() or re.search(r"https?://|www\.|\[[0-9]+\]", value):
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
