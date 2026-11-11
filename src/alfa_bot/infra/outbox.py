from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class Outbox:
    """DevOps + Python 3: общий sender для ответов и reminders."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    async def deliver_pending(self) -> int:
        # Claim + COMMIT -> проверить revision -> Telegram -> сохранить результат.
        # Учитывать lock_token, expired lease, retry_after и постоянную блокировку.
        raise NotImplementedError("Реализовать обработку app.deliveries")
