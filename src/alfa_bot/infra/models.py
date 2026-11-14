"""Общая ORM-база для существующих reminders и памяти assistant."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
