from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class KnowledgeRepository:
    """Python 1. Владеет kb.*. Все запросы одного ответа используют один run_id.

    TODO: active_run, vector/FTS retrieval, карточки, темы, связи, публикация run.
    Пользовательские таблицы здесь не изменяются.
    """

    def __init__(self, sessions: async_sessionmaker[AsyncSession]):
        self.sessions = sessions
