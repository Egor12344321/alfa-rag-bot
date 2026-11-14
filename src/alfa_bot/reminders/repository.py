from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class ReminderRepository:
    """Python 3: reminders и черновик в conversations.pending_payload.

    TODO: проверка владельца, user+draft idempotency, optimistic revision,
    сохранение подтверждения, отмена старых pending delivery в той же транзакции.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions
