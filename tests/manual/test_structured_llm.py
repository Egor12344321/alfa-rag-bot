
import asyncio

from alfa_bot.assistant.prompts import ANSWER_SYSTEM, INTENT_SYSTEM
from alfa_bot.assistant.schemas import AnswerDraft, IntentResult
from alfa_bot.infra.llm import build_llm


async def main() -> None:
    llm = build_llm()

    print("\n1. Проверяем IntentResult...")

    intent = await llm.structured(
        system=INTENT_SYSTEM,
        user=(
            "Текущее сообщение пользователя: Привет!\n"
            "Нужно определить намерение пользователя."
        ),
        schema=IntentResult,
    )

    print("Результат:", intent)
    print("Тип:", type(intent).__name__)
    print("Намерение:", intent.intent)

    assert isinstance(intent, IntentResult)

    print("\n2. Проверяем AnswerDraft...")

    answer = await llm.structured(
        system=ANSWER_SYSTEM,
        user=(
            "Контекст: надёжных источников нет.\n"
            "Вопрос пользователя: Как открыть бизнес?\n"
            "Не придумывай информацию. Верни структуру "
            "с указанием, что данных недостаточно."
        ),
        schema=AnswerDraft,
    )

    print("Результат:", answer)
    print("Тип:", type(answer).__name__)
    print("Достаточно информации:", answer.enough_info)
    print("Ответ:", answer.answer)
    print("Недостающая информация:", answer.missing)

    assert isinstance(answer, AnswerDraft)

    print("\nВсе проверки структурированных ответов пройдены!")


if __name__ == "__main__":
    asyncio.run(main())
