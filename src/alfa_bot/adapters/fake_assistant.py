from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from alfa_bot.contracts import (
    Answer, MaterialAccess, MaterialCard, MaterialKind,
    ReminderIntent, Reply, ReplyType,
)


class FakeAssistant:
    """Заглушка с явными тестовыми фразами; классификацию LLM не имитирует.

    «тест ответ», «тест уточнение», «тест вне темы» проверяют ветки Reply.
    Любой другой текст по-прежнему предлагает напоминание через две минуты.
    Заглушка не помнит диалог и не собирает ответы на уточняющие вопросы.
    """

    async def handle(
        self,
        *,
        telegram_user_id: int,
        text: str,
        now: datetime,
    ) -> Reply:
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now должен содержать часовой пояс.")
        demo = " ".join(text.casefold().split())
        if demo == "тест ответ":
            return Reply(
                type=ReplyType.ANSWER,
                answer=Answer(
                    text=(
                        "Это демонстрационный ответ для проверки карточек. "
                        "Настоящий assistant будет отвечать на вопрос по материалам каталога.\n\n"
                        "Карточки ниже — примеры оформления, не рекомендации из базы «Альфа-Курса»."
                    ),
                    sources=[MaterialCard(
                        title="Пример карточки источника",
                        url="https://example.com/?demo=source",
                        kind=MaterialKind.ARTICLE,
                        description="Здесь assistant укажет, какой материал использован в ответе.",
                    )],
                    recommendations=[MaterialCard(
                        title="Пример карточки курса",
                        url="https://example.com/?demo=course",
                        kind=MaterialKind.COURSE,
                        access=MaterialAccess.UNKNOWN,
                        description="Здесь будет объяснение, чем курс полезен по теме вопроса.",
                    )],
                ),
            )
        if demo == "тест уточнение":
            return Reply(
                type=ReplyType.CLARIFICATION,
                text="Когда напомнить о продолжении обучения? Укажите дату, время и часовой пояс.",
            )
        if demo == "тест вне темы":
            return Reply(type=ReplyType.OUT_OF_SCOPE)

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
