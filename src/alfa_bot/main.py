"""Application composition: contributed modules and the user's Telegram/reminders."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from sqlalchemy import text

from alfa_bot.assistant.integration import IntegratedAssistant
from alfa_bot.assistant.llm import DeepSeekLLM
from alfa_bot.assistant.repository import AssistantRepository
from alfa_bot.bot.delivery import TelegramDelivery
from alfa_bot.bot.telegram import build_router
from alfa_bot.config import load_config
from alfa_bot.db import Database
from alfa_bot.knowledge.configured_embeddings import ConfiguredEmbedder
from alfa_bot.knowledge.repository import KnowledgeRepository
from alfa_bot.knowledge.service import KnowledgeService
from alfa_bot.knowledge.sqlalchemy_pool import SQLAlchemyPool
from alfa_bot.reminders.service import ReminderService
from alfa_bot.runtime import configure_runtime, require_migrations


def build_components(database, settings):
    llm = DeepSeekLLM(settings)
    knowledge = KnowledgeService(
        KnowledgeRepository(SQLAlchemyPool(database.engine)),
        ConfiguredEmbedder(settings),
    )
    reminders = ReminderService(database.sessions, draft_ttl_minutes=settings.draft_ttl_minutes)
    assistant = IntegratedAssistant(
        AssistantRepository(database.sessions),
        knowledge,
        llm,
        reminders,
        settings,
    )
    return assistant, llm, knowledge


async def require_index(database, settings):
    async with database.engine.connect() as connection:
        row = (
            await connection.execute(
                text(
                    "SELECT r.embedding_model,r.embedding_revision,r.embedding_dim "
                    "FROM kb.index_state s JOIN kb.ingestion_runs r ON r.id=s.active_run_id "
                    "WHERE r.status='ready'"
                )
            )
        ).first()
    if row is None:
        raise RuntimeError("Импортируйте индекс: python -m alfa_bot.knowledge.bundle")
    if tuple(row) != (
        settings.embedding_model,
        settings.embedding_revision,
        settings.embedding_dimension,
    ):
        raise RuntimeError("Настройки модели не совпадают с активным индексом")


async def main():
    from alfa_bot.reminders.worker import ReminderWorker

    settings = load_config()
    configure_runtime(settings.log_level)
    settings.validate_runtime()
    database = Database(settings.database_url)
    llm = bot = None
    try:
        await require_migrations(database)
        await require_index(database, settings)
        assistant, llm, _ = build_components(database, settings)
        bot = Bot(token=settings.bot_token.get_secret_value())
        dispatcher = Dispatcher()
        dispatcher.include_router(build_router(assistant))
        worker = ReminderWorker(
            database.sessions, TelegramDelivery(bot), interval_seconds=settings.reminder_interval
        )
        logging.getLogger(__name__).info("Application ready; polling and reminders starting")
        async with asyncio.TaskGroup() as tasks:
            worker_task = tasks.create_task(worker.run(), name="reminder-worker")
            try:
                await dispatcher.start_polling(bot, close_bot_session=False)
            finally:
                worker_task.cancel()
    finally:
        if bot:
            await bot.session.close()
        if llm:
            await llm.close()
        await database.close()


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
