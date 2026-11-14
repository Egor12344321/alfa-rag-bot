from dataclasses import dataclass

from alfa_bot.assistant.repository import AssistantRepository
from alfa_bot.assistant.service import AssistantService
from alfa_bot.infra.db import Database
from alfa_bot.infra.embeddings import LocalEmbeddings
from alfa_bot.infra.inbox import Inbox
from alfa_bot.infra.llm import UnconfiguredLLM
from alfa_bot.infra.outbox import Outbox
from alfa_bot.knowledge.repository import KnowledgeRepository
from alfa_bot.knowledge.service import KnowledgeService
from alfa_bot.reminders.repository import ReminderRepository
from alfa_bot.reminders.service import ReminderService


@dataclass
class Services:
    knowledge: KnowledgeService
    assistant: AssistantService
    reminders: ReminderService
    inbox: Inbox
    outbox: Outbox


def build_services(database: Database) -> Services:
    """Общее место сборки зависимостей. Пока все бизнес-адаптеры — заглушки."""
    knowledge = KnowledgeService(KnowledgeRepository(database.sessions), LocalEmbeddings())
    reminders = ReminderService(ReminderRepository(database.sessions))
    assistant = AssistantService(
        AssistantRepository(database.sessions),
        knowledge,
        reminders,
        UnconfiguredLLM(),
    )
    return Services(
        knowledge,
        assistant,
        reminders,
        Inbox(database.sessions),
        Outbox(database.sessions),
    )
