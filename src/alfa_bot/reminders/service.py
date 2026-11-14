from datetime import datetime
from uuid import UUID

from alfa_bot.contracts import ReminderDraft, ReminderIntent, ReminderView
from alfa_bot.reminders.repository import ReminderRepository


class ReminderService:
    def __init__(self, repository: ReminderRepository):
        self.repository = repository

    async def prepare(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        intent: ReminderIntent,
        now: datetime,
    ) -> ReminderDraft:
        raise NotImplementedError("Python 3: заполнить и сохранить черновик")

    async def confirm(
        self,
        *,
        user_id: UUID,
        conversation_id: UUID,
        draft_id: UUID,
        expected_version: int,
        confirmation_key: str,
    ) -> ReminderView:
        raise NotImplementedError("Python 3: подтверждение и атомарное создание reminder")

    async def list_for_user(self, user_id: UUID) -> tuple[ReminderView, ...]:
        raise NotImplementedError("Python 3: список напоминаний текущего пользователя")

    async def change(
        self,
        *,
        user_id: UUID,
        reminder_id: UUID,
        expected_revision: int,
        replacement: ReminderIntent,
    ) -> ReminderView:
        raise NotImplementedError("Python 3: изменить правило и увеличить revision")

    async def cancel(
        self,
        *,
        user_id: UUID,
        reminder_id: UUID,
        expected_revision: int,
    ) -> ReminderView:
        raise NotImplementedError("Python 3: отменить reminder и будущие задания")

    async def enqueue_due(self, now: datetime) -> int:
        raise NotImplementedError("Python 3: due reminders -> deliveries + next_run_at")
