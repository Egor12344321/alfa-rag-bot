"""Рабочие таблицы профиля, состояния диалога и истории сообщений."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Identity, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from alfa_bot.db import Base


class Profile(Base):
    __tablename__ = "profiles"
    __table_args__ = {"schema": "app"}

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    business_stage: Mapped[str] = mapped_column(String(24), default="unknown")
    experience_level: Mapped[str] = mapped_column(String(24), default="unknown")
    industry: Mapped[str | None] = mapped_column(Text)
    tax_regime: Mapped[str | None] = mapped_column(Text)
    response_style: Mapped[str] = mapped_column(String(24), default="auto")
    preferred_access: Mapped[str] = mapped_column(String(16), default="any")
    wants_courses: Mapped[bool | None] = mapped_column(Boolean)
    goal: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class DialogModel(Base):
    __tablename__ = "dialogs"
    __table_args__ = {"schema": "app"}

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    pending: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    recent_materials: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    last_update_id: Mapped[int | None] = mapped_column(BigInteger)
    last_reply: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_user_id_id", "user_id", "id"), {"schema": "app"})

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TurnResult(Base):
    """User-scoped deduplication also handles retries of an older message."""

    __tablename__ = "turn_results"
    __table_args__ = {"schema": "app"}
    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    reply: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
