"""Память личного диалога, без зависимости от чужих таблиц users."""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from alfa_bot.infra.models import Base


class AssistantDialog(Base):
    __tablename__ = "assistant_dialogs"

    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=False,
    )
    profile: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), default=dict)
    history: Mapped[list] = mapped_column(JSON().with_variant(JSONB, "postgresql"), default=list)
    pending: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), default=dict)
    recent_materials: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        default=list,
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
