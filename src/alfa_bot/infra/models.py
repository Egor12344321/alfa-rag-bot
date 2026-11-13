"""Технические таблицы приёма, доставки и событий."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    Boolean,
)
from sqlalchemy import String, func, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from alfa_bot.infra.base import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("jsonb_typeof(profile) = 'object'"), {"schema": "app"})

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=sql_text("gen_random_uuid()"))
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    timezone: Mapped[str | None] = mapped_column(Text)
    profile: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=sql_text("'{}'::jsonb"))
    delivery_enabled: Mapped[bool] = mapped_column(Boolean, server_default=sql_text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )

class Reminder(Base):
    __tablename__ = "reminders"
    __table_args__ = (
        CheckConstraint(
            "frequency IN ('once', 'daily', 'weekly', 'monthly')",
            name="ck_reminders_frequency",
        ),
        CheckConstraint(
            "status IN ('active', 'paused', 'done')",
            name="ck_reminders_status",
        ),
        Index("ix_reminders_due", "status", "next_run_at"),
        Index("ix_reminders_owner", "telegram_user_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)

    # Telegram identity and delivery address; no dependency on users.
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[int] = mapped_column(BigInteger)

    text: Mapped[str] = mapped_column(Text)
    material_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    frequency: Mapped[str] = mapped_column(String(16), server_default="once")
    timezone: Mapped[str] = mapped_column(String(64))

    # The original anchor is retained when next_run_at advances.
    # Calendar rules are calculated in the stored IANA timezone.
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(16), server_default="active")
    last_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class TelegramUpdate(Base):
    __tablename__ = "telegram_updates"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'processing', 'done', 'failed')"),
        CheckConstraint("attempts >= 0"),
        CheckConstraint(
            "(status = 'processing' AND lock_token IS NOT NULL AND locked_until IS NOT NULL) "
            "OR (status <> 'processing' AND lock_token IS NULL AND locked_until IS NULL)"
        ),
        Index(
            "updates_due_idx",
            "available_at",
            "update_id",
            postgresql_where=sql_text("status = 'pending'"),
        ),
        Index(
            "one_processing_update_per_chat_idx",
            "chat_id",
            unique=True,
            postgresql_where=sql_text("status = 'processing'"),
        ),
        Index(
            "updates_lease_idx", "locked_until", postgresql_where=sql_text("status = 'processing'")
        ),
        {"schema": "app"},
    )

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )
    lock_token: Mapped[UUID | None]
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (
        CheckConstraint("kind IN ('reply', 'reminder')"),
        CheckConstraint("part_no >= 0"),
        CheckConstraint("status IN ('pending', 'leased', 'sent', 'cancelled', 'dead')"),
        CheckConstraint("attempts >= 0"),
        CheckConstraint(
            "(kind = 'reply' AND message_id IS NOT NULL AND reminder_id IS NULL "
            "AND reminder_revision IS NULL AND occurrence_at IS NULL) OR "
            "(kind = 'reminder' AND message_id IS NULL AND reminder_id IS NOT NULL "
            "AND reminder_revision IS NOT NULL AND reminder_revision > 0 "
            "AND occurrence_at IS NOT NULL)"
        ),
        CheckConstraint(
            "(status = 'leased' AND lock_token IS NOT NULL AND locked_until IS NOT NULL) "
            "OR (status <> 'leased' AND lock_token IS NULL AND locked_until IS NULL)"
        ),
        CheckConstraint(
            "status <> 'sent' OR (sent_at IS NOT NULL AND telegram_message_id IS NOT NULL)"
        ),
        UniqueConstraint("message_id", "part_no"),
        UniqueConstraint("reminder_id", "reminder_revision", "occurrence_at"),
        Index(
            "deliveries_due_idx",
            "available_at",
            "id",
            postgresql_where=sql_text("status = 'pending'"),
        ),
        Index(
            "deliveries_lease_idx", "locked_until", postgresql_where=sql_text("status = 'leased'")
        ),
        Index("deliveries_reminder_idx", "reminder_id", "reminder_revision"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=sql_text("gen_random_uuid()"))
    dedupe_key: Mapped[str] = mapped_column(Text, unique=True)
    kind: Mapped[str] = mapped_column(Text)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"))
    target_chat_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.messages.id"))
    part_no: Mapped[int] = mapped_column(SmallInteger, server_default=sql_text("0"))
    reminder_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.reminders.id"))
    reminder_revision: Mapped[int | None] = mapped_column(Integer)
    occurrence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text: Mapped[str] = mapped_column(Text)
    reply_markup: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default=sql_text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, server_default=sql_text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )
    lock_token: Mapped[UUID | None]
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    last_error_code: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("events_type_time_idx", "event_type", "occurred_at"),
        Index("events_user_time_idx", "user_id", "occurred_at"),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, server_default=sql_text("gen_random_uuid()"))
    user_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))
    conversation_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.conversations.id"))
    message_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.messages.id"))
    material_id: Mapped[UUID | None] = mapped_column(ForeignKey("kb.materials.id"))
    event_type: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(Text, unique=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sql_text("now()")
    )
    extra_data: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, server_default=sql_text("'{}'::jsonb")
    )
