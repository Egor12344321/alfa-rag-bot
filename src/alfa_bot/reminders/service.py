"""Подтверждение и управление напоминаниями без Telegram и LLM."""

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, select, update

from alfa_bot.contracts import (
    ActionResult,
    ReminderAction,
    ReminderDraft,
    ReminderIntent,
    ReminderView,
)
from alfa_bot.reminders.models import Reminder, ReminderDraftRow
from alfa_bot.reminders.schedule import (
    ScheduleError,
    calculate_first_run,
    calculate_next_run,
    validate_frequency,
)

logger = logging.getLogger(__name__)


class ReminderError(ValueError):
    """Ошибку можно безопасно показать пользователю."""


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReminderError("Дата должна содержать часовой пояс.")
    return value.astimezone(UTC)


def to_view(row: Reminder) -> ReminderView:
    return ReminderView(
        id=row.id,
        text=row.text,
        starts_at=utc(row.starts_at),
        next_run_at=utc(row.next_run_at),
        timezone=row.timezone,
        frequency=row.frequency,
        status=row.status,
        material_url=row.material_url,
    )


def to_draft(row: ReminderDraftRow) -> ReminderDraft:
    return ReminderDraft(
        id=row.id,
        text=row.text,
        starts_at=utc(row.starts_at),
        timezone=row.timezone,
        frequency=row.frequency,
        material_url=row.material_url,
        expires_at=utc(row.expires_at),
    )


def safe_material_url(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme in {"http", "https"}
            and bool(parts.hostname)
            and parts.username is None
            and parts.password is None
            and not any(char.isspace() for char in value)
        )
        _ = parts.port
    except ValueError:
        valid = False
    if not valid:
        raise ReminderError("У материала некорректная ссылка.")
    return value


class ReminderService:
    def __init__(self, sessions, *, draft_ttl_minutes: int = 30):
        self.sessions = sessions
        self.draft_ttl = timedelta(minutes=draft_ttl_minutes)

    async def cancel_pending(self, user_id: int, chat_id: int) -> None:
        async with self.sessions.begin() as session:
            await session.execute(select(func.pg_advisory_xact_lock(1427, user_id % 2147483647)))
            await session.execute(
                update(ReminderDraftRow)
                .where(
                    ReminderDraftRow.telegram_user_id == user_id,
                    ReminderDraftRow.chat_id == chat_id,
                    ReminderDraftRow.status == "pending",
                )
                .values(status="cancelled")
            )

    async def create_draft(
        self,
        *,
        user_id: int,
        chat_id: int,
        intent: ReminderIntent,
        now: datetime,
    ) -> ReminderDraft:
        now = utc(now)
        if user_id <= 0 or chat_id != user_id:
            raise ReminderError("Напоминания доступны в личном чате с ботом.")
        text = intent.text.strip()
        if not 1 <= len(text) <= 1000:
            raise ReminderError("Текст напоминания должен содержать от 1 до 1000 символов.")
        try:
            validate_frequency(intent.frequency)
            starts_at = calculate_first_run(
                local_datetime=intent.local_datetime,
                timezone_name=intent.timezone,
                now=now,
            )
        except ScheduleError as error:
            raise ReminderError(str(error)) from None
        row = ReminderDraftRow(
            telegram_user_id=user_id,
            chat_id=chat_id,
            text=text,
            starts_at=starts_at,
            timezone=intent.timezone,
            frequency=intent.frequency,
            material_url=safe_material_url(intent.material_url),
            expires_at=now + self.draft_ttl,
            status="pending",
        )
        async with self.sessions.begin() as session:
            # Короткая блокировка пользователя защищает от двух параллельных черновиков.
            await session.execute(select(func.pg_advisory_xact_lock(1427, user_id % 2147483647)))
            # Старые кнопки остаются в Telegram, но больше не создают записи.
            await session.execute(
                update(ReminderDraftRow)
                .where(
                    ReminderDraftRow.telegram_user_id == user_id,
                    ReminderDraftRow.chat_id == chat_id,
                    ReminderDraftRow.status == "pending",
                )
                .values(status="cancelled")
            )
            session.add(row)
            await session.flush()
            result = to_draft(row)
        logger.info("reminder_draft_created")
        return result

    async def action(self, request: ReminderAction) -> ActionResult:
        now = utc(request.now)
        if request.chat_id != request.user_id:
            raise ReminderError("Напоминания доступны в личном чате с ботом.")
        if request.action == "list":
            async with self.sessions() as session:
                rows = await session.scalars(
                    select(Reminder)
                    .where(
                        Reminder.telegram_user_id == request.user_id,
                        Reminder.chat_id == request.chat_id,
                        Reminder.status != "done",
                    )
                    .order_by(Reminder.created_at.desc(), Reminder.id)
                    .limit(20)
                )
                items = [to_view(row) for row in rows]
            return ActionResult(
                text="Ваши напоминания:" if items else "У вас пока нет напоминаний.",
                items=items,
            )
        if request.reminder_id is None:
            raise ReminderError("Не указано напоминание.")
        if request.action in {"confirm", "cancel_draft"}:
            return await self._draft_action(request, now)
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(Reminder)
                .where(
                    Reminder.id == request.reminder_id,
                    Reminder.telegram_user_id == request.user_id,
                    Reminder.chat_id == request.chat_id,
                )
                .with_for_update()
            )
            if row is None:
                raise ReminderError("Напоминание не найдено или уже удалено.")
            notice = ""
            if request.action == "delete":
                await session.delete(row)
                logger.info("reminder_deleted")
                return ActionResult(text="Напоминание удалено.")
            if request.action == "show":
                return ActionResult(reminder=to_view(row))
            if row.status == "done":
                raise ReminderError("Напоминание уже выполнено. Создайте новое.")
            if request.action == "disable":
                row.status = "paused"
                notice = "Напоминание отключено."
            elif request.action == "resume":
                next_run = self._next(row, now)
                if next_run is None:
                    raise ReminderError("Время разового напоминания прошло. Создайте новое.")
                row.next_run_at = next_run
                row.status = "active"
                notice = "Напоминание включено."
            elif request.action == "frequency":
                if request.frequency is None:
                    raise ReminderError("Выберите частоту напоминания.")
                if request.frequency != row.frequency:
                    self._change_frequency(row, request.frequency, now)
                notice = "Частота обновлена."
            await session.flush()
            result = ActionResult(text=notice, reminder=to_view(row))
        logger.info("reminder_updated action=%s", request.action)
        return result

    async def _draft_action(self, request: ReminderAction, now: datetime) -> ActionResult:
        async with self.sessions.begin() as session:
            draft = await session.scalar(
                select(ReminderDraftRow)
                .where(
                    ReminderDraftRow.id == request.reminder_id,
                    ReminderDraftRow.telegram_user_id == request.user_id,
                    ReminderDraftRow.chat_id == request.chat_id,
                )
                .with_for_update()
            )
            if draft is None:
                raise ReminderError("Черновик недоступен.")
            if draft.status == "confirmed":
                if request.action == "cancel_draft":
                    return ActionResult(text="Напоминание уже создано. Управление — /reminders.")
                row = await session.scalar(
                    select(Reminder).where(
                        Reminder.id == draft.id,
                        Reminder.telegram_user_id == request.user_id,
                        Reminder.chat_id == request.chat_id,
                    )
                )
                if row is None:
                    raise ReminderError("Это напоминание уже удалено. Создайте новый запрос.")
                return ActionResult(text="Напоминание уже создано.", reminder=to_view(row))
            if draft.status != "pending":
                raise ReminderError("Этот черновик отменён. Напишите новое сообщение.")
            if request.action == "cancel_draft":
                draft.status = "cancelled"
                return ActionResult(text="Создание напоминания отменено.")
            if utc(draft.expires_at) <= now:
                raise ReminderError("Срок подтверждения истёк. Напишите запрос ещё раз.")
            if utc(draft.starts_at) <= now:
                raise ReminderError("Время напоминания уже прошло. Выберите новую дату.")
            row = Reminder(
                id=draft.id,
                telegram_user_id=request.user_id,
                chat_id=request.chat_id,
                text=draft.text,
                starts_at=draft.starts_at,
                next_run_at=draft.starts_at,
                timezone=draft.timezone,
                frequency=draft.frequency,
                material_url=draft.material_url,
                status="active",
            )
            session.add(row)
            draft.status = "confirmed"
            await session.flush()
            result = ActionResult(text="Напоминание создано.", reminder=to_view(row))
        logger.info("reminder_confirmed")
        return result

    @staticmethod
    def _next(row: Reminder, now: datetime):
        try:
            return calculate_next_run(
                starts_at=utc(row.starts_at),
                frequency=row.frequency,
                timezone_name=row.timezone,
                after=now,
            )
        except ScheduleError as error:
            raise ReminderError(str(error)) from None

    def _change_frequency(self, row: Reminder, frequency: str, now: datetime) -> None:
        try:
            validate_frequency(frequency)
            if frequency == "once":
                next_run = utc(row.next_run_at)
                if next_run <= now:
                    next_run = self._next(row, now)
                if next_run is None:
                    raise ReminderError("Для разового напоминания нужна будущая дата.")
                row.starts_at = next_run
            next_run = calculate_next_run(
                starts_at=utc(row.starts_at),
                frequency=frequency,
                timezone_name=row.timezone,
                after=now,
            )
        except ScheduleError as error:
            raise ReminderError(str(error)) from None
        if next_run is None:
            raise ReminderError("Не удалось определить будущую дату напоминания.")
        row.frequency = frequency
        row.next_run_at = next_run
