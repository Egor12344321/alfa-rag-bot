from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from alfa_bot.reminders.models import Reminder


class ReminderRepository:
    """Запросы к reminders. Транзакцией управляет вызывающий код."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add(self, reminder: Reminder) -> Reminder:
        self.session.add(reminder)
        await self.session.flush()
        return reminder

    async def get_for_user(
        self,
        *,
        reminder_id: UUID,
        telegram_user_id: int,
        for_update: bool = False,
    ) -> Reminder | None:
        statement = select(Reminder).where(
            Reminder.id == reminder_id,
            Reminder.telegram_user_id == telegram_user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def list_for_user(self, *, telegram_user_id: int) -> list[Reminder]:
        statement = (
            select(Reminder)
            .where(
                Reminder.telegram_user_id == telegram_user_id,
                Reminder.status != "done",
            )
            .order_by(Reminder.created_at.desc(), Reminder.id)
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def update_schedule(
        self,
        *,
        reminder_id: UUID,
        telegram_user_id: int,
        frequency: str,
        timezone: str,
        starts_at: datetime,
        next_run_at: datetime,
    ) -> Reminder | None:
        statement = (
            update(Reminder)
            .where(
                Reminder.id == reminder_id,
                Reminder.telegram_user_id == telegram_user_id,
            )
            .values(
                frequency=frequency,
                timezone=timezone,
                starts_at=starts_at,
                next_run_at=next_run_at,
            )
            .returning(Reminder)
        )
        return await self.session.scalar(statement)

    async def set_status(
        self,
        *,
        reminder_id: UUID,
        telegram_user_id: int,
        status: str,
    ) -> Reminder | None:
        statement = (
            update(Reminder)
            .where(
                Reminder.id == reminder_id,
                Reminder.telegram_user_id == telegram_user_id,
            )
            .values(status=status)
            .returning(Reminder)
        )
        return await self.session.scalar(statement)

    async def delete_for_user(
        self, *, reminder_id: UUID, telegram_user_id: int
    ) -> bool:
        statement = (
            delete(Reminder)
            .where(
                Reminder.id == reminder_id,
                Reminder.telegram_user_id == telegram_user_id,
            )
            .returning(Reminder.id)
        )
        deleted_id = await self.session.scalar(statement)
        return deleted_id is not None

    # Внутренние операции планировщика: работают с напоминаниями всех владельцев.

    async def list_due(self, *, now: datetime, limit: int = 100) -> list[Reminder]:
        statement = (
            select(Reminder)
            .where(
                Reminder.status == "active",
                Reminder.next_run_at <= now,
            )
            .order_by(Reminder.next_run_at, Reminder.id)
            .limit(limit)
        )
        result = await self.session.scalars(statement)
        return list(result.all())

    async def record_delivery(
        self,
        *,
        reminder_id: UUID,
        expected_next_run_at: datetime,
        sent_at: datetime,
        next_run_at: datetime,
        status: str,
    ) -> bool:
        """Записать доставку, если напоминание активно и срок не изменился."""
        statement = (
            update(Reminder)
            .where(
                Reminder.id == reminder_id,
                Reminder.status == "active",
                Reminder.next_run_at == expected_next_run_at,
            )
            .values(
                last_sent_at=sent_at,
                next_run_at=next_run_at,
                status=status,
            )
            .returning(Reminder.id)
        )
        updated_id = await self.session.scalar(statement)
        return updated_id is not None

    async def advance_without_delivery(
        self,
        *,
        reminder_id: UUID,
        expected_next_run_at: datetime,
        next_run_at: datetime,
        status: str,
    ) -> bool:
        """Изменить состояние без отметки об успешной отправке."""
        statement = (
            update(Reminder)
            .where(
                Reminder.id == reminder_id,
                Reminder.status == "active",
                Reminder.next_run_at == expected_next_run_at,
            )
            .values(next_run_at=next_run_at, status=status)
            .returning(Reminder.id)
        )
        updated_id = await self.session.scalar(statement)
        return updated_id is not None
