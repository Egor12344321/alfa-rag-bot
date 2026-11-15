"""Exercise the actual assistant/adapter/UI boundary without paid external APIs."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from alfa_bot import domain
from alfa_bot.assistant.integration import IntegratedAssistant
from alfa_bot.assistant.llm import LLMError
from alfa_bot.assistant.repository import ConcurrentTurnError, Dialog
from alfa_bot.config import Settings
from alfa_bot.contracts import ReminderDraft, ReplyType, UserProfile, UserRequest

NOW = datetime(2030, 1, 1, 7, tzinfo=UTC)


class Store:
    def __init__(self):
        self.dialogs = {}
        self.messages = {}
        self.results = {}

    async def get_reply(self, user_id, update_id):
        return deepcopy(self.results.get((user_id, update_id)))

    async def load(self, user_id):
        self.dialogs.setdefault(user_id, Dialog(user_id, 1, UserProfile(user_id=user_id)))
        return deepcopy(self.dialogs[user_id])

    async def history(self, user_id, limit):
        return deepcopy(self.messages.get(user_id, [])[-limit:])

    async def commit_turn(self, dialog, messages, *, reply, update_id, history_limit):
        if self.dialogs[dialog.user_id].version != dialog.version:
            raise ConcurrentTurnError()
        dialog.version += 1
        dialog.last_update_id = update_id
        dialog.last_reply = reply.model_dump(mode="json")
        self.dialogs[dialog.user_id] = deepcopy(dialog)
        self.messages[dialog.user_id] = (self.messages.get(dialog.user_id, []) + list(messages))[
            -history_limit:
        ]
        if update_id is not None:
            self.results[(dialog.user_id, update_id)] = deepcopy(reply)

    async def reset(self, user_id):
        self.dialogs[user_id] = Dialog(user_id, 1, UserProfile(user_id=user_id))
        self.messages[user_id] = []


class ScriptedLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def generate(self, messages, *, schema):
        self.calls.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return schema.model_validate(response).model_dump_json()


class Knowledge:
    def __init__(self):
        self.run = uuid4()
        self.source = domain.SourceKey("Articles", "article-1")
        self.evidence = domain.Evidence(
            uuid4(),
            uuid4(),
            self.source,
            "Клиенты кофейни",
            "Для привлечения клиентов используйте рекламу.",
            "https://example.org/article",
            0.1,
        )
        self.course = domain.MaterialCard(
            uuid4(),
            self.run,
            domain.SourceKey("Courses", "course-1"),
            "course",
            "Курс по маркетингу",
            "Описание курса",
            "https://example.org/course",
            "free",
            score=0.9,
        )
        self.queries = []

    async def retrieve(self, query, **kwargs):
        self.queries.append(query)
        return domain.SearchResult(self.run, (self.evidence,))

    async def recommend(self, query, profile, **kwargs):
        return (self.course,)

    async def get_card(self, source, **kwargs):
        return self.course if source == self.course.source else None


class Reminders:
    def __init__(self):
        self.drafts = []
        self.cancelled = []

    async def create_draft(self, *, user_id, chat_id, intent, now):
        draft = ReminderDraft(
            id=uuid4(),
            text=intent.text,
            starts_at=intent.local_datetime.replace(tzinfo=UTC) - timedelta(hours=3),
            timezone=intent.timezone,
            frequency=intent.frequency,
            material_url=intent.material_url,
            expires_at=now + timedelta(minutes=30),
        )
        self.drafts.append(draft)
        return draft

    async def cancel_pending(self, user_id, chat_id):
        self.cancelled.append((user_id, chat_id))


def request(text, update_id=1, user_id=1):
    return UserRequest(user_id=user_id, chat_id=user_id, text=text, now=NOW, update_id=update_id)


def assistant(store, llm, knowledge=None, reminders=None):
    return IntegratedAssistant(
        store, knowledge or Knowledge(), llm, reminders or Reminders(), Settings(_env_file=None)
    )


@pytest.mark.asyncio
async def test_clarification_profile_history_and_separate_materials_survive_new_instance():
    store, kb = Store(), Knowledge()
    first = assistant(
        store,
        ScriptedLLM(
            {
                "intent": "consult",
                "needs_clarification": True,
                "clarification_question": "Какой у вас бизнес?",
            }
        ),
        kb,
    )
    assert (await first.handle(request("Как привлечь клиентов?"))).type == ReplyType.CLARIFICATION
    llm = ScriptedLLM(
        {"intent": "consult", "profile_patch": {"industry": "кофейня"}},
        {"enough_info": True, "answer": "Используйте рекламу [1]."},
    )
    second = assistant(store, llm, kb)
    reply = await second.handle(request("У меня кофейня", 2))
    assert reply.answer.text == "Используйте рекламу [1]."
    assert reply.answer.sources[0].url == kb.evidence.url
    assert reply.answer.recommendations[0].material.title == kb.course.title
    assert kb.course.title not in reply.answer.text
    assert "Как привлечь клиентов" in kb.queries[0] and "кофейня" in kb.queries[0]
    assert store.dialogs[1].profile.industry == "кофейня"
    assert store.dialogs[1].pending["core"]["pending_question"] is None
    assert len(store.messages[1]) == 4
    assert "Исходный вопрос" in str(llm.calls[0])


@pytest.mark.asyncio
async def test_old_duplicate_is_cached_and_scoped_to_user():
    store = Store()
    bot = assistant(store, ScriptedLLM({"intent": "smalltalk"}, {"intent": "out_of_scope"}))
    first = await bot.handle(request("Привет", 1))
    await bot.handle(request("другая тема", 2))
    replay = await assistant(store, ScriptedLLM()).handle(request("Привет", 1))
    assert replay == first and len(store.messages[1]) == 4
    other = await assistant(store, ScriptedLLM({"intent": "out_of_scope"})).handle(
        request("вне темы", 1, 2)
    )
    assert other != first


@pytest.mark.asyncio
async def test_reminder_details_and_recent_course_keep_user_module_contract():
    store, kb, reminders = Store(), Knowledge(), Reminders()
    bot = assistant(
        store, ScriptedLLM({"intent": "recommend", "requested_kind": "course"}), kb, reminders
    )
    recommendation = await bot.handle(request("Подбери курс"))
    assert recommendation.answer.recommendations[0].material.url == kb.course.url
    first = assistant(
        store,
        ScriptedLLM(
            {
                "intent": "reminder",
                "reminder": {"text": "Пройти этот курс", "material_query": "этот курс"},
            }
        ),
        kb,
        reminders,
    )
    assert (
        await first.handle(request("Напомни пройти этот курс", 2))
    ).type == ReplyType.CLARIFICATION
    assert not reminders.drafts
    second = assistant(
        store,
        ScriptedLLM(
            {
                "intent": "reminder",
                "reminder": {"local_start": "2030-01-02T10:00:00", "repeat_kind": "monthly"},
            }
        ),
        kb,
        reminders,
    )
    reply = await second.handle(request("Завтра в десять, каждый месяц", 3))
    assert reply.type == ReplyType.REMINDER
    assert reply.draft.text == "Пройти этот курс"
    assert reply.draft.frequency == "monthly"
    assert reply.draft.timezone == "Europe/Moscow"
    assert reply.draft.material_url == kb.course.url
    assert len(reminders.drafts) == 1


@pytest.mark.asyncio
async def test_provider_failure_preserves_pending_question():
    store = Store()
    first = assistant(
        store,
        ScriptedLLM(
            {
                "intent": "consult",
                "needs_clarification": True,
                "clarification_question": "Какой бизнес?",
            }
        ),
    )
    await first.handle(request("Как привлечь клиентов?"))
    state = deepcopy(store.dialogs[1].pending)
    reply = await assistant(store, ScriptedLLM(LLMError("timeout"))).handle(request("кофейня", 2))
    assert "не получилось" in reply.answer.text
    assert store.dialogs[1].pending == state


@pytest.mark.asyncio
async def test_commands_reset_only_current_user_and_skip_llm():
    store, reminders = Store(), Reminders()
    bot = assistant(store, ScriptedLLM(), reminders=reminders)
    await bot.handle(request("/start"))
    await bot.handle(request("/start", 1, 2))
    await bot.handle(request("/new", 2))
    assert not store.messages[1]
    assert len(store.messages[2]) == 2
    assert reminders.cancelled == [(1, 1)]
