from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from alfa_bot.infra.models import Base


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
