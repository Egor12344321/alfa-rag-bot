import asyncio
import logging
from datetime import UTC, datetime

import httpx
from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from alfa_bot.adapters.telegram import router
from alfa_bot.assistant.repository import AssistantRepository
from alfa_bot.assistant.service import AssistantService
from alfa_bot.infra.config import load_config
from alfa_bot.infra.db import Database
from alfa_bot.infra.llm import OllamaLLM
from alfa_bot.knowledge.runtime import open_knowledge
from alfa_bot.reminders.scheduler import ReminderScheduler
from alfa_bot.reminders.service import ReminderService

logger = logging.getLogger(__name__)


async def main() -> None:
    config = load_config()
    config.validate_runtime()
    logging.basicConfig(level=config.log_level)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    bot = Bot(token=config.bot_token)
    database = Database(config.database_url)
    try:
        async with httpx.AsyncClient(
            base_url=config.ollama_base_url,
            timeout=config.llm_timeout,
            trust_env=False,
        ) as client:
            knowledge = await open_knowledge(config, database.sessions, client, require_index=False)
            repository = AssistantRepository(database.sessions)
            llm = OllamaLLM(config.llm_model, client, context_length=config.ollama_context_length)
            assistant = AssistantService(
                repository, knowledge, llm, clock=lambda: datetime.now(UTC)
            )
            reminders = ReminderService(database.sessions)
            scheduler = ReminderScheduler(
                database.sessions,
                bot,
                interval_seconds=config.reminder_interval,
            )
            dispatcher = Dispatcher(
                storage=MemoryStorage(),
                events_isolation=SimpleEventIsolation(),
                reminders=reminders,
                assistant=assistant,
            )
            dispatcher.include_router(router)
            logger.info(
                "Bot ready, knowledge=%s model=%s", config.knowledge_backend, config.llm_model
            )
            async with asyncio.TaskGroup() as tasks:
                scheduler_task = tasks.create_task(scheduler.run(), name="reminder-scheduler")
                try:
                    await dispatcher.start_polling(bot, close_bot_session=False)
                finally:
                    scheduler_task.cancel()
    finally:
        await bot.session.close()
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
