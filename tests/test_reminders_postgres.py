"""Транзакционные сценарии на реальном PostgreSQL, fixture db создаёт root."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from alfa_bot.contracts import DeliveryRetry, ReminderAction, ReminderIntent
from alfa_bot.reminders.models import Reminder, ReminderDraftRow
from alfa_bot.reminders.service import ReminderError, ReminderService
from alfa_bot.reminders.worker import ReminderWorker

pytestmark = pytest.mark.asyncio
NOW = datetime(2030, 1, 1, 7, tzinfo=UTC)


def intent(frequency="once"):
    return ReminderIntent(
        text="Прочитать статью",
        local_datetime=datetime(2030, 1, 1, 10, 2),
        timezone="Europe/Moscow",
        frequency=frequency,
    )


def action(kind, draft_id=None, *, user=900001, now=NOW, frequency=None):
    return ReminderAction(
        user_id=user,
        chat_id=user,
        action=kind,
        reminder_id=draft_id,
        now=now,
        frequency=frequency,
    )


async def test_draft_survives_new_service_and_confirmation_is_idempotent(db):
    service = ReminderService(db.sessions)
    draft = await service.create_draft(user_id=900001, chat_id=900001, intent=intent(), now=NOW)
    async with db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Reminder)) == 0
        assert await session.scalar(select(func.count()).select_from(ReminderDraftRow)) == 1
    # Новый экземпляр сервиса не использует старую память процесса.
    restarted = ReminderService(db.sessions)
    first = await restarted.action(action("confirm", draft.id))
    repeated = await restarted.action(action("confirm", draft.id))
    assert first.reminder.id == repeated.reminder.id == draft.id
    async with db.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Reminder)) == 1


async def test_foreign_expired_and_cancelled_drafts_cannot_be_confirmed(db):
    service = ReminderService(db.sessions)
    draft = await service.create_draft(user_id=900001, chat_id=900001, intent=intent(), now=NOW)
    with pytest.raises(ReminderError, match="недоступен"):
        await service.action(action("confirm", draft.id, user=900002))
    with pytest.raises(ReminderError, match="истёк"):
        await service.action(action("confirm", draft.id, now=NOW + timedelta(minutes=31)))
    await service.cancel_pending(900001, 900001)
    with pytest.raises(ReminderError, match="отменён"):
        await service.action(action("confirm", draft.id))


async def test_deleted_reminder_is_not_recreated_by_old_confirmation(db):
    service = ReminderService(db.sessions)
    draft = await service.create_draft(user_id=900001, chat_id=900001, intent=intent(), now=NOW)
    await service.action(action("confirm", draft.id))
    await service.action(action("delete", draft.id))
    with pytest.raises(ReminderError, match="уже удалено"):
        await service.action(action("confirm", draft.id))


async def test_frequency_change_preserves_paused_status_and_resume_uses_future_date(db):
    service = ReminderService(db.sessions)
    draft = await service.create_draft(
        user_id=900001, chat_id=900001, intent=intent("daily"), now=NOW
    )
    await service.action(action("confirm", draft.id))
    await service.action(action("disable", draft.id))
    changed = await service.action(action("frequency", draft.id, frequency="weekly"))
    assert changed.reminder.status == "paused"
    resumed = await service.action(action("resume", draft.id, now=NOW + timedelta(days=2)))
    assert resumed.reminder.status == "active"
    assert resumed.reminder.next_run_at == datetime(2030, 1, 8, 7, 2, tzinfo=UTC)


class TestDelivery:
    __test__ = False

    def __init__(self, error=None):
        self.sent = []
        self.error = error

    async def send(self, delivery):
        if self.error:
            raise self.error
        await asyncio.sleep(0.02)
        self.sent.append(delivery)


async def test_two_workers_do_not_send_same_locked_reminder(db):
    service = ReminderService(db.sessions)
    draft = await service.create_draft(user_id=900001, chat_id=900001, intent=intent(), now=NOW)
    await service.action(action("confirm", draft.id))
    transport = TestDelivery()

    def clock():
        return NOW + timedelta(minutes=3)

    first = ReminderWorker(db.sessions, transport, clock=clock)
    second = ReminderWorker(db.sessions, transport, clock=clock)
    results = await asyncio.gather(first.run_once(), second.run_once())
    assert sum(results) == 1 and len(transport.sent) == 1
    assert await first.run_once() == 0
    async with db.sessions() as session:
        row = await session.get(Reminder, draft.id)
        assert row.status == "done" and row.last_sent_at is not None


async def test_temporary_delivery_failure_keeps_reminder_active(db):
    service = ReminderService(db.sessions)
    draft = await service.create_draft(user_id=900001, chat_id=900001, intent=intent(), now=NOW)
    await service.action(action("confirm", draft.id))
    worker = ReminderWorker(
        db.sessions,
        TestDelivery(DeliveryRetry(10)),
        clock=lambda: NOW + timedelta(minutes=3),
    )
    assert await worker.run_once() == 0
    async with db.sessions() as session:
        row = await session.get(Reminder, draft.id)
        assert row.status == "active" and row.last_sent_at is None


async def test_concurrent_new_drafts_leave_only_one_pending(db):
    service = ReminderService(db.sessions)
    await asyncio.gather(
        service.create_draft(user_id=900001, chat_id=900001, intent=intent(), now=NOW),
        service.create_draft(user_id=900001, chat_id=900001, intent=intent(), now=NOW),
    )
    async with db.sessions() as session:
        pending = await session.scalar(
            select(func.count())
            .select_from(ReminderDraftRow)
            .where(
                ReminderDraftRow.status == "pending",
            )
        )
        assert pending == 1
