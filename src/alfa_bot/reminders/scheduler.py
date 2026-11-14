import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alfa_bot.reminders.models import Reminder
from alfa_bot.reminders.repository import ReminderRepository
from alfa_bot.reminders.schedule import ScheduleError, calculate_next_run


logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ScheduleError("Ожидалась дата со сведениями о часовом поясе.")
    return value.astimezone(UTC)


class ReminderScheduler:
    """Цикл доставки. Состояние хранится в БД, время можно подменить в проверках."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        bot: Bot,
        *,
        interval_seconds: float = 30,
        batch_size: int = 100,
        max_lateness: timedelta = timedelta(hours=24),
        request_timeout: int = 10,
        clock: Callable[[], datetime] = _now_utc,
    ):
        if interval_seconds <= 0 or batch_size <= 0 or request_timeout <= 0:
            raise ValueError("Интервал, размер пакета и таймаут должны быть положительными.")
        if max_lateness <= timedelta(0):
            raise ValueError("Допустимое опоздание должно быть положительным.")
        self.sessions = sessions
        self.bot = bot
        self.interval_seconds = interval_seconds
        self.batch_size = batch_size
        self.max_lateness = max_lateness
        self.request_timeout = request_timeout
        self.clock = clock
        self._retry_until: datetime | None = None

    async def run(self) -> None:
        """Проверять БД до отмены фоновой задачи."""
        logger.info("Reminder scheduler started")
        while True:
            try:
                await self.run_once()
            except TelegramUnauthorizedError:
                logger.error("Telegram rejected the bot token; scheduler stopped")
                raise
            except Exception:
                logger.exception("Reminder scheduler iteration failed")
            # CancelledError не перехватываем: задача должна корректно остановиться.
            await asyncio.sleep(self.interval_seconds)

    async def run_once(self) -> int:
        """Обработать один пакет и вернуть число подтверждённых доставок."""
        now = _utc(self.clock())
        if self._retry_until is not None and now < self._retry_until:
            return 0

        async with self.sessions() as session:
            reminders = await ReminderRepository(session).list_due(
                now=now, limit=self.batch_size,
            )
            pending = [(item.id, item.telegram_user_id) for item in reminders]

        sent = 0
        for reminder_id, telegram_user_id in pending:
            try:
                sent += int(await self._process(reminder_id, telegram_user_id))
            except TelegramRetryAfter as error:
                self._retry_until = _utc(self.clock()) + timedelta(
                    seconds=max(1, error.retry_after),
                )
                logger.warning("Telegram rate limit; retry after %s seconds", error.retry_after)
                break
            except TelegramUnauthorizedError:
                raise
            except (TelegramNetworkError, TelegramServerError, OSError, TimeoutError):
                logger.warning("Temporary delivery failure for reminder %s", reminder_id)
                break
            except SQLAlchemyError:
                logger.exception("Database operation failed for reminder %s", reminder_id)
                break
            except Exception:
                logger.exception("Failed to process reminder %s", reminder_id)
        return sent

    def _next(self, reminder: Reminder, after: datetime) -> datetime | None:
        return calculate_next_run(
            starts_at=reminder.starts_at,
            frequency=reminder.frequency,
            timezone_name=reminder.timezone,
            after=after,
        )

    def _has_recent_occurrence(self, reminder: Reminder, now: datetime) -> bool:
        cutoff = now - self.max_lateness
        if _utc(reminder.next_run_at) >= cutoff:
            return True
        # Включаем и срабатывание ровно на границе допустимого опоздания.
        recent = self._next(reminder, cutoff - timedelta(microseconds=1))
        return recent is not None and recent <= now

    async def _advance(
        self,
        repository: ReminderRepository,
        reminder: Reminder,
        *,
        next_run_at: datetime,
        status: str,
    ) -> None:
        updated = await repository.advance_without_delivery(
            reminder_id=reminder.id,
            expected_next_run_at=reminder.next_run_at,
            next_run_at=next_run_at,
            status=status,
        )
        if not updated:
            raise RuntimeError("Reminder changed while its row was locked")

    async def _process(self, reminder_id: UUID, telegram_user_id: int) -> bool:
        async with self.sessions.begin() as session:
            repository = ReminderRepository(session)
            reminder = await repository.get_for_user(
                reminder_id=reminder_id, telegram_user_id=telegram_user_id,
                for_update=True,
            )
            now = _utc(self.clock())
            if reminder is None or reminder.status != "active":
                return False
            expected_run = _utc(reminder.next_run_at)
            if expected_run > now:
                return False

            try:
                next_run = self._next(reminder, now)
                recent = self._has_recent_occurrence(reminder, now)
            except ScheduleError:
                await self._advance(
                    repository, reminder, next_run_at=expected_run, status="paused",
                )
                logger.error("Invalid schedule; reminder %s paused", reminder_id)
                return False

            if not recent:
                # Устаревшее разовое отключаем, повторяющееся переносим в будущее.
                await self._advance(
                    repository, reminder,
                    next_run_at=next_run if next_run is not None else expected_run,
                    status="active" if next_run is not None else "paused",
                )
                return False

            keyboard = None
            text = f"Напоминаю: {reminder.text}"
            if reminder.material_url:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="Открыть материал", url=reminder.material_url),
                ]])
                text += "\n\nПосле выполнения можете написать, что получилось."

            try:
                # Блокировку держим до записи результата; сетевой запрос ограничен по времени.
                await self.bot.send_message(
                    chat_id=reminder.chat_id,
                    text=text,
                    reply_markup=keyboard,
                    parse_mode=None,
                    request_timeout=self.request_timeout,
                )
            except (TelegramForbiddenError, TelegramBadRequest):
                await self._advance(
                    repository, reminder, next_run_at=expected_run, status="paused",
                )
                logger.warning("Permanent delivery failure; reminder %s paused", reminder_id)
                return False

            sent_at = _utc(self.clock())
            try:
                next_run = self._next(reminder, max(now, sent_at))
                status = "done" if next_run is None else "active"
            except ScheduleError:
                # Сообщение уже доставлено: сохраняем этот факт и отключаем расписание.
                next_run, status = None, "paused"
                logger.error("Cannot advance delivered reminder %s; paused", reminder_id)

            recorded = await repository.record_delivery(
                reminder_id=reminder_id,
                expected_next_run_at=expected_run,
                sent_at=sent_at,
                next_run_at=next_run if next_run is not None else expected_run,
                status=status,
            )
            if not recorded:
                raise RuntimeError("Cannot record delivery of the locked reminder")
            logger.info("Reminder %s delivered", reminder_id)
            return True
