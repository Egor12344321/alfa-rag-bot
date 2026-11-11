from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class AssistantRepository:
    """Python 2: users, conversations, messages, message_sources, events.

    TODO: состояние по version; история; ответ+источники+delivery атомарно;
    повтор update_id возвращает сохранённый результат.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions
