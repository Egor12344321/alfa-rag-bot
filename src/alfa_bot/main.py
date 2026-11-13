import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation

from alfa_bot.adapters.fake_assistant import FakeAssistant
from alfa_bot.adapters.telegram import router
from alfa_bot.infra.config import load_config
from alfa_bot.infra.db import Database
from alfa_bot.reminders.init_db import init_reminders
from alfa_bot.reminders.scheduler import ReminderScheduler
from alfa_bot.reminders.service import ReminderService


async def main() -> None:
    config = load_config()
    bot = Bot(token=config.bot_token)
    database = Database(config.database_url)

    try:
        await init_reminders(database)
        reminders = ReminderService(database.sessions)
        scheduler = ReminderScheduler(database.sessions, bot)
        dispatcher = Dispatcher(
            storage=MemoryStorage(),
            events_isolation=SimpleEventIsolation(),
            reminders=reminders,
            # TODO: Заменить заглушку настоящей реализацией AssistantPort.
            assistant=FakeAssistant(),
        )
        dispatcher.include_router(router)

        async with asyncio.TaskGroup() as tasks:
            scheduler_task = tasks.create_task(
                scheduler.run(), name="reminder-scheduler",
            )
            try:
                await dispatcher.start_polling(bot, close_bot_session=False)
            finally:
                scheduler_task.cancel()
    finally:
        try:
            await bot.session.close()
        finally:
            await database.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
