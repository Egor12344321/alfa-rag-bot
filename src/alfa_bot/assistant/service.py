from alfa_bot.assistant.repository import AssistantRepository
from alfa_bot.contracts import HandledTurn, Knowledge, Reminders, UserTurn
from alfa_bot.infra.llm import LLM


class AssistantService:
    def __init__(
        self,
        repository: AssistantRepository,
        knowledge: Knowledge,
        reminders: Reminders,
        llm: LLM,
    ):
        self.repository = repository
        self.knowledge = knowledge
        self.reminders = reminders
        self.llm = llm

    async def handle_turn(self, turn: UserTurn) -> HandledTurn:
        # Состояние -> намерение -> уточнение / retrieval / reminder.
        # Сетевые вызовы вне длинной транзакции. Проверка version перед commit.
        raise NotImplementedError("Python 2: собрать сценарий консультации")
