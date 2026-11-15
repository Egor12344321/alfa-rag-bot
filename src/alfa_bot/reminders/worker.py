"""Планировщик не знает о Telegram: транспорт реализует DeliveryPort."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from alfa_bot.contracts import Delivery, DeliveryPort, DeliveryRejected, DeliveryRetry
from alfa_bot.reminders.models import Reminder
from alfa_bot.reminders.schedule import ScheduleError, calculate_next_run
from alfa_bot.reminders.service import utc

logger = logging.getLogger(__name__)


class ReminderWorker:
    def __init__(
        self,
        sessions,
        delivery: DeliveryPort,
        *,
        interval_seconds: float = 15,
        batch_size: int = 50,
        max_lateness: timedelta = timedelta(hours=24),
        clock=None,
    ):
        if interval_seconds <= 0 or batch_size <= 0:
            raise ValueError("Интервал и размер пакета должны быть положительными")
        self.sessions = sessions
        self.delivery = delivery
        self.interval = interval_seconds
        self.batch_size = batch_size
        self.max_lateness = max_lateness
        self.clock = clock or (lambda: datetime.now(UTC))
        self._retry_until = None

    async def run(self):
        logger.info("reminder_worker_started")
        while True:
            try:
                await self.run_once()
            except Exception as error:  # noqa: BLE001 — граница фоновой задачи/транспорта
                logger.error("reminder_iteration_failed error_type=%s", type(error).__name__)
            await asyncio.sleep(self.interval)

    async def run_once(self) -> int:
        now = utc(self.clock())
        if self._retry_until and now < self._retry_until:
            return 0
        sent = 0
        for _ in range(self.batch_size):
            try:
                result = await self._process_one()
            except DeliveryRetry as error:
                self._retry_until = utc(self.clock()) + timedelta(seconds=max(1, error.seconds))
                logger.warning("reminder_delivery_retry")
                break
            if result is None:
                break
            sent += result
        if sent:
            logger.info("reminders_delivered count=%s", sent)
        return sent

    async def _process_one(self) -> int | None:
        # Одну строку держим под блокировкой на время короткой отправки.
        async with self.sessions.begin() as session:
            now = utc(self.clock())
            row = await session.scalar(
                select(Reminder)
                .where(
                    Reminder.status == "active",
                    Reminder.next_run_at <= now,
                )
                .order_by(Reminder.next_run_at, Reminder.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            try:
                next_run = calculate_next_run(
                    starts_at=utc(row.starts_at),
                    frequency=row.frequency,
                    timezone_name=row.timezone,
                    after=now,
                )
                cutoff = now - self.max_lateness
                recent = utc(row.next_run_at) >= cutoff
                if not recent and row.frequency != "once":
                    occurrence = calculate_next_run(
                        starts_at=utc(row.starts_at),
                        frequency=row.frequency,
                        timezone_name=row.timezone,
                        after=cutoff - timedelta(microseconds=1),
                    )
                    recent = occurrence is not None and occurrence <= now
            except (ScheduleError, ValueError):
                row.status = "paused"
                logger.error("reminder_invalid_schedule_paused")
                return 0
            if not recent:
                row.status = "active" if next_run else "paused"
                row.next_run_at = next_run or row.next_run_at
                return 0
            try:
                await self.delivery.send(
                    Delivery(
                        id=row.id,
                        chat_id=row.chat_id,
                        text=row.text,
                        material_url=row.material_url,
                    )
                )
            except DeliveryRejected:
                row.status = "paused"
                logger.warning("reminder_delivery_rejected_paused")
                return 0
            row.last_sent_at = now
            row.next_run_at = next_run or row.next_run_at
            row.status = "active" if next_run else "done"
            return 1
