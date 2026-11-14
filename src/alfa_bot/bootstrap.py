from dataclasses import dataclass

from alfa_bot.assistant.repository import UnconfiguredAssistantRepository
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
    """Общее место сборки зависимостей приложения."""

    knowledge = KnowledgeService(
        KnowledgeRepository(database.sessions),
        LocalEmbeddings(),
    )

    reminders = ReminderService(
        ReminderRepository(database.sessions),
    )

    assistant_repository = UnconfiguredAssistantRepository(
        database.sessions,
    )

    assistant = AssistantService(
        assistant_repository,
        knowledge,
        reminders,
        UnconfiguredLLM(),
    )

    inbox = Inbox(database.sessions)
    outbox = Outbox(database.sessions)

    return Services(
        knowledge=knowledge,
        assistant=assistant,
        reminders=reminders,
        inbox=inbox,
        outbox=outbox,
    )
