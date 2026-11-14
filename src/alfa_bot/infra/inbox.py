from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class Inbox:
    """DevOps + Python 3: durable приём Telegram updates до подтверждения доставки."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions

    async def accept(self, update: dict) -> None:
        # Нормализация; user/conversation; INSERT ON CONFLICT; COMMIT.
        # Сохранять только необходимые поля. update_id — ключ идемпотентности.
        raise NotImplementedError("Сохранить update в app.telegram_updates")

    async def process_pending(self) -> int:
        # Обновления одного чата последовательно, lease и восстановление после сбоя.
        raise NotImplementedError("Прочитать inbox и вызвать Assistant.handle_turn")
