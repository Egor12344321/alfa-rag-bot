import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from alfa_bot.adapters.telegram import router
from alfa_bot.assistant.repository import AssistantRepository
from alfa_bot.assistant.service import AssistantService
from alfa_bot.infra.config import load_config
from alfa_bot.infra.db import Database
from alfa_bot.infra.llm import DeepSeekLLM
from alfa_bot.knowledge.repository import KnowledgeRepository
from alfa_bot.knowledge.service import KnowledgeService
from alfa_bot.reminders.init_db import init_reminders
from alfa_bot.reminders.scheduler import ReminderScheduler
from alfa_bot.reminders.service import ReminderService


async def main() -> None:
    config = load_config()
    config.validate_runtime()
    # Индекс читается до подключения Telegram, чтобы ошибку было видно при запуске.
    index = await asyncio.to_thread(KnowledgeRepository.load, config.knowledge_index)
    knowledge = KnowledgeService(index)
    database = Database(config.database_url)
    llm = DeepSeekLLM(
        api_key=config.deepseek_api_key.get_secret_value(),
        model=config.deepseek_model,
        base_url=config.deepseek_base_url,
        timeout=config.llm_timeout,
    )
    bot = Bot(token=config.bot_token)
    try:
        await init_reminders(database)
        repository = AssistantRepository(database.sessions)
        await repository.init_db()
        assistant = AssistantService(repository, knowledge, llm)
        reminders = ReminderService(database.sessions)
        scheduler = ReminderScheduler(database.sessions, bot)
        dispatcher = Dispatcher(
            storage=MemoryStorage(),
            events_isolation=SimpleEventIsolation(),
            reminders=reminders,
            assistant=assistant,
        )
        dispatcher.include_router(router)
        async with asyncio.TaskGroup() as tasks:
            scheduler_task = tasks.create_task(scheduler.run(), name="reminder-scheduler")
            try:
                await dispatcher.start_polling(bot, close_bot_session=False)
            finally:
                scheduler_task.cancel()
    finally:
        await bot.session.close()
        await llm.close()
        await database.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
