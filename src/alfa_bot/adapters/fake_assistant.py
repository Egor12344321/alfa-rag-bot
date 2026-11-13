from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from alfa_bot.contracts import ReminderIntent, Reply, ReplyType


class FakeAssistant:
    """Временная заглушка: любой текст предлагает одно и то же напоминание."""

    async def handle(
        self,
        *,
        telegram_user_id: int,
        text: str,
        now: datetime,
    ) -> Reply:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now должен содержать часовой пояс.")
        timezone_name = "Europe/Moscow"
        run_at = (now.astimezone(UTC) + timedelta(minutes=2)).replace(microsecond=0)
        local_datetime = run_at.astimezone(ZoneInfo(timezone_name)).replace(tzinfo=None)
        return Reply(
            type=ReplyType.REMINDER,
            reminder=ReminderIntent(
                text="Продолжить обучение на «Альфа-Курсе»",
                local_datetime=local_datetime,
                timezone=timezone_name,
                frequency="once",
            ),
        )
