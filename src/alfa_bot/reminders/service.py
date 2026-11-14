from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alfa_bot.infra.models import Reminder
from alfa_bot.reminders.repository import ReminderRepository
from alfa_bot.reminders.schedule import (
    ScheduleError,
    calculate_first_run,
    calculate_next_run,
    validate_frequency,
)


MAX_TEXT_LENGTH = 1000


@dataclass(frozen=True)
class ReminderDraft:
    id: UUID
    telegram_user_id: int
    chat_id: int
    text: str
    frequency: str
    timezone: str
    starts_at: datetime
    material_url: str | None = None


@dataclass(frozen=True)
class ReminderView:
    id: UUID
    text: str
    frequency: str
    timezone: str
    starts_at: datetime
    next_run_at: datetime
    status: str
    material_url: str | None = None


class ReminderError(ValueError):
    """Ошибка данных или состояния напоминания."""


class ReminderNotFoundError(ReminderError):
    """Напоминания нет либо оно принадлежит другому пользователю."""



def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReminderError("Дата должна содержать часовой пояс.")
    try:
        return value.astimezone(UTC)
    except OverflowError:
        raise ReminderError("Дата выходит за допустимый диапазон.") from None


def _require(reminder: Reminder | None) -> Reminder:
    if reminder is None:
        raise ReminderNotFoundError("Напоминание не найдено.")
    return reminder


def _to_view(reminder: Reminder | None) -> ReminderView:
    reminder = _require(reminder)
    return ReminderView(
        id=reminder.id,
        text=reminder.text,
        frequency=reminder.frequency,
        timezone=reminder.timezone,
        starts_at=_utc(reminder.starts_at),
        next_run_at=_utc(reminder.next_run_at),
        status=reminder.status,
        material_url=reminder.material_url,
    )


def _material_url(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    value = value.strip()
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in ("http", "https") and bool(parsed.hostname)
    except ValueError:
        valid = False
    if not valid or any(char.isspace() for char in value):
        raise ReminderError("Ссылка на материал должна быть HTTP или HTTPS URL.")
    return value


class ReminderService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    def prepare(
        self,
        *,
        telegram_user_id: int,
        chat_id: int,
        text: str,
        local_datetime: datetime,
        timezone_name: str,
        now: datetime,
        frequency: str = "once",
        material_url: str | None = None,
    ) -> ReminderDraft:
        """Проверить данные и подготовить черновик без записи в БД."""
        if telegram_user_id <= 0 or chat_id == 0:
            raise ReminderError("Некорректный пользователь или чат.")
        text = text.strip()
        if not 1 <= len(text) <= MAX_TEXT_LENGTH:
            raise ReminderError(f"Текст должен содержать от 1 до {MAX_TEXT_LENGTH} символов.")

        timezone_name = timezone_name.strip()
        try:
            validate_frequency(frequency)
            starts_at = calculate_first_run(
                local_datetime=local_datetime, timezone_name=timezone_name, now=now,
            )
        except ScheduleError as error:
            raise ReminderError(str(error)) from error

        return ReminderDraft(
            id=uuid4(), telegram_user_id=telegram_user_id, chat_id=chat_id,
            text=text, frequency=frequency, timezone=timezone_name,
            starts_at=starts_at, material_url=_material_url(material_url),
        )

    async def confirm(
        self,
        *,
        draft: ReminderDraft,
        telegram_user_id: int,
        chat_id: int,
        now: datetime,
    ) -> ReminderView:
        """Сохранить серверный черновик; повторное подтверждение не создаёт дубль."""
        if draft.telegram_user_id != telegram_user_id or draft.chat_id != chat_id:
            raise ReminderNotFoundError("Черновик недоступен.")
        now_utc = _utc(now)

        try:
            async with self.sessions.begin() as session:
                repository = ReminderRepository(session)
                existing = await repository.get_for_user(
                    reminder_id=draft.id, telegram_user_id=telegram_user_id,
                )
                if existing is not None:
                    return _to_view(existing)
                if _utc(draft.starts_at) <= now_utc:
                    raise ReminderError("Время напоминания уже прошло. Выберите новую дату.")

                reminder = Reminder(
                    id=draft.id, telegram_user_id=telegram_user_id, chat_id=chat_id,
                    text=draft.text, frequency=draft.frequency, timezone=draft.timezone,
                    starts_at=draft.starts_at, next_run_at=draft.starts_at,
                    status="active", material_url=draft.material_url,
                )
                return _to_view(await repository.add(reminder))
        except IntegrityError:
            # Два одновременных подтверждения могли пройти первоначальный SELECT.
            # Неудачная транзакция уже откатилась; читаем запись в новой сессии.
            async with self.sessions() as session:
                existing = await ReminderRepository(session).get_for_user(
                    reminder_id=draft.id, telegram_user_id=telegram_user_id,
                )
                if existing is None:
                    raise
                return _to_view(existing)

    async def get(
        self, *, reminder_id: UUID, telegram_user_id: int
    ) -> ReminderView:
        async with self.sessions() as session:
            reminder = await ReminderRepository(session).get_for_user(
                reminder_id=reminder_id, telegram_user_id=telegram_user_id,
            )
            return _to_view(reminder)

    async def list_for_user(self, *, telegram_user_id: int) -> list[ReminderView]:
        async with self.sessions() as session:
            reminders = await ReminderRepository(session).list_for_user(
                telegram_user_id=telegram_user_id,
            )
            return [_to_view(reminder) for reminder in reminders]

    async def change_frequency(
        self,
        *,
        reminder_id: UUID,
        telegram_user_id: int,
        frequency: str,
        now: datetime,
    ) -> ReminderView:
        now_utc = _utc(now)
        async with self.sessions.begin() as session:
            repository = ReminderRepository(session)
            reminder = _require(await repository.get_for_user(
                reminder_id=reminder_id, telegram_user_id=telegram_user_id,
                for_update=True,
            ))
            if reminder.status == "done":
                raise ReminderError("Напоминание уже выполнено. Создайте новое.")

            starts_at = _utc(reminder.starts_at)
            try:
                validate_frequency(frequency)
                if frequency == "once":
                    # Сохраняем только ближайшее оставшееся срабатывание.
                    remaining = _utc(reminder.next_run_at)
                    if remaining <= now_utc:
                        remaining = calculate_next_run(
                            starts_at=starts_at, frequency=reminder.frequency,
                            timezone_name=reminder.timezone, after=now_utc,
                        )
                    if remaining is None:
                        raise ReminderError("Для разового напоминания нужна новая будущая дата.")
                    starts_at = remaining

                next_run_at = calculate_next_run(
                    starts_at=starts_at, frequency=frequency,
                    timezone_name=reminder.timezone, after=now_utc,
                )
            except ScheduleError as error:
                raise ReminderError(str(error)) from error
            if next_run_at is None:
                raise ReminderError("У напоминания нет будущего срабатывания.")

            updated = await repository.update_schedule(
                reminder_id=reminder_id, telegram_user_id=telegram_user_id,
                frequency=frequency, timezone=reminder.timezone,
                starts_at=starts_at, next_run_at=next_run_at,
            )
            return _to_view(updated)

    async def disable(
        self, *, reminder_id: UUID, telegram_user_id: int
    ) -> ReminderView:
        async with self.sessions.begin() as session:
            repository = ReminderRepository(session)
            reminder = _require(await repository.get_for_user(
                reminder_id=reminder_id, telegram_user_id=telegram_user_id,
                for_update=True,
            ))
            if reminder.status == "done":
                raise ReminderError("Напоминание уже выполнено.")
            if reminder.status == "paused":
                return _to_view(reminder)
            updated = await repository.set_status(
                reminder_id=reminder_id, telegram_user_id=telegram_user_id,
                status="paused",
            )
            return _to_view(updated)

    async def delete(self, *, reminder_id: UUID, telegram_user_id: int) -> bool:
        async with self.sessions.begin() as session:
            return await ReminderRepository(session).delete_for_user(
                reminder_id=reminder_id, telegram_user_id=telegram_user_id,
            )
