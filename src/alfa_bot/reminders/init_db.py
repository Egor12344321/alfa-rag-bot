import asyncio

from alfa_bot.infra.config import load_config
from alfa_bot.infra.db import Database
from alfa_bot.reminders.models import Reminder


async def init_reminders(database: Database) -> None:
    """Create reminders and its indexes if the table does not exist."""
    async with database.engine.begin() as connection:
        await connection.run_sync(
            Reminder.__table__.create,
            checkfirst=True,
        )


async def main() -> None:
    config = load_config()
    database = Database(config.database_url)

    try:
        await init_reminders(database)
    finally:
        await database.close()

    print("Таблица reminders готова.")


if __name__ == "__main__":
    asyncio.run(main())
