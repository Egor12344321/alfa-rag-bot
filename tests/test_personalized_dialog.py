import asyncio
from datetime import datetime, timedelta

from test_scenarios import NOW, ScriptedLLM, knowledge, setup_repository

from alfa_bot.assistant.service import AssistantService
from alfa_bot.contracts import ReplyType
from alfa_bot.knowledge.models import Document, KnowledgeUnavailable, Material
from alfa_bot.knowledge.repository import KnowledgeRepository
from alfa_bot.knowledge.service import KnowledgeService

GROUNDED = {
    "answerable": True,
    "paragraphs": [{"text": "Определите целевую аудиторию.", "chunk_ids": ["a:0"]}],
}


def test_current_format_overrides_preference_without_overwriting_it(tmp_path):
    async def scenario():
        path = tmp_path / "memory.db"
        engine, repo = await setup_repository(path)
        llm = ScriptedLLM(
            {
                "intent": "profile",
                "profile": {
                    "business_stage": "planning",
                    "industry": "общепит",
                    "experience_level": "beginner",
                    "tax_regime": "УСН",
                    "response_style": "detailed",
                    "course_preference": "welcome",
                },
            }
        )
        assistant = AssistantService(repo, knowledge(), llm)
        updated = await assistant.handle(
            telegram_user_id=1,
            text="Планирую открыть кафе, я новичок на УСН. Обычно хочу подробные ответы и курсы.",
            now=NOW,
        )
        assert updated.type == ReplyType.ANSWER and len(llm.calls) == 1
        await engine.dispose()

        engine, repo = await setup_repository(path)
        llm = ScriptedLLM(
            {"intent": "knowledge", "query": "Клиенты кофейни", "response_style": "brief"},
            GROUNDED,
            {"intent": "knowledge", "query": "Клиенты кофейни"},
            GROUNDED,
        )
        assistant = AssistantService(repo, knowledge(), llm)
        short = await assistant.handle(
            telegram_user_id=1, text="Как привлечь клиентов? Сейчас кратко", now=NOW,
        )
        assert llm.calls[1][1]["response_style"] == "brief"
        assert llm.calls[1][1]["profile"]["tax_regime"] == "УСН"
        assert not short.answer.recommendations

        await assistant.handle(telegram_user_id=1, text="Как привлечь клиентов?", now=NOW)
        assert llm.calls[3][1]["response_style"] == "detailed"
        assert (await repo.load(1)).profile["response_style"] == "detailed"
        await engine.dispose()

    asyncio.run(scenario())


def test_recommends_course_for_requested_level_and_current_access(tmp_path):
    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        profile = await repo.load(1)
        profile.profile = {
            "experience_level": "beginner",
            "preferred_access": "free",
            "course_preference": "avoid",
        }
        await repo.save(profile)
        materials = [
            Material(
                "adv", "Продажи для опытных", "", "https://example.org/adv", "course", "paid",
            ),
            Material(
                "intro", "Продажи для новичков", "", "https://example.org/intro", "course", "paid",
            ),
        ]
        courses = KnowledgeService(
            KnowledgeRepository(
                materials,
                [Document(m.id, m.id, m.title, "catalog") for m in materials],
            )
        )
        llm = ScriptedLLM(
            {
                "intent": "recommendation",
                "query": "Продажи",
                "requested_kind": "course",
                "requested_access": "paid",
            }
        )
        assistant = AssistantService(repo, courses, llm)
        reply = await assistant.handle(
            telegram_user_id=1,
            text="Сегодня подбери платный курс по продажам",
            now=NOW,
        )
        assert [card.url for card in reply.answer.recommendations] == ["https://example.org/intro"]
        assert (await repo.load(1)).profile["course_preference"] == "avoid"
        await engine.dispose()

    asyncio.run(scenario())


def test_missing_evidence_can_offer_explicitly_requested_course_description(tmp_path):
    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        catalog_card = Material(
            "c",
            "Курс по найму",
            "Обзор подбора команды",
            "https://example.org/c",
            "course",
            "paid",
        )
        catalog = KnowledgeService(
            KnowledgeRepository(
                [catalog_card], [Document("c:0", "c", "Найм и подбор команды", "catalog")],
            )
        )
        llm = ScriptedLLM(
            {"intent": "knowledge", "query": "Найм", "requested_kind": "course"}
        )
        reply = await AssistantService(repo, catalog, llm).handle(
            telegram_user_id=1, text="Как нанять команду? И подскажи курс по найму", now=NOW,
        )
        assert "В базе знаний нет точного ответа" in reply.answer.text
        assert not reply.answer.sources
        assert [card.description for card in reply.answer.recommendations] == [
            "Обзор подбора команды"
        ]
        assert len(llm.calls) == 1
        await engine.dispose()

    asyncio.run(scenario())


def test_relative_minute_uses_received_time_and_timezone(tmp_path):
    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        llm = ScriptedLLM(
            {
                "intent": "reminder",
                "reminder": {
                    "text": "проверить каталог",
                    "local_datetime": "2026-11-14T13:09",
                    "timezone": "Europe/Moscow",
                },
            }
        )
        reply = await AssistantService(repo, knowledge(), llm).handle(
            telegram_user_id=1,
            text="Напомни через 1 минуту по МСК проверить каталог",
            now=NOW,
        )
        assert reply.type == ReplyType.REMINDER
        assert reply.reminder.local_datetime == datetime(2026, 11, 14, 13, 1)
        await engine.dispose()

    asyncio.run(scenario())


def test_user_can_replace_relative_reminder_time_while_clarifying(tmp_path):
    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        llm = ScriptedLLM(
            {"intent": "reminder", "reminder": {"text": "проверить каталог"}},
            {
                "intent": "reminder",
                "continue_pending": True,
                "reminder": {
                    "local_datetime": "2026-11-15T10:00",
                    "timezone": "Europe/Moscow",
                },
            },
        )
        assistant = AssistantService(repo, knowledge(), llm)
        first = await assistant.handle(
            telegram_user_id=1, text="Напомни через 1 минуту проверить каталог", now=NOW,
        )
        assert first.type == ReplyType.CLARIFICATION
        final = await assistant.handle(
            telegram_user_id=1, text="Лучше завтра в 10 по МСК", now=NOW,
        )
        assert final.reminder.local_datetime == datetime(2026, 11, 15, 10)
        await engine.dispose()

    asyncio.run(scenario())


def test_unavailable_index_preserves_state_and_reports_search_failure(tmp_path):
    class OfflineIndex:
        async def retrieve(self, query: str, *, limit: int | None = None):
            raise KnowledgeUnavailable("no index")

    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        llm = ScriptedLLM({"intent": "knowledge", "query": "Как найти клиентов?"})
        reply = await AssistantService(repo, OfflineIndex(), llm).handle(
            telegram_user_id=1, text="Как найти клиентов?", now=NOW,
        )
        assert "недоступен" in reply.answer.text
        assert not (await repo.load(1)).history
        await engine.dispose()

    asyncio.run(scenario())
def test_slow_reminder_uses_time_when_the_draft_is_ready(tmp_path):
    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        llm = ScriptedLLM(
            {
                "intent": "reminder",
                "reminder": {"text": "проверить каталог", "timezone": "Europe/Moscow"},
            },
        )
        draft_time = NOW + timedelta(minutes=3)
        assistant = AssistantService(repo, knowledge(), llm, clock=lambda: draft_time)
        reply = await assistant.handle(
            telegram_user_id=1, text="Напомни через 1 минуту по МСК проверить каталог", now=NOW,
        )
        assert reply.reminder.local_datetime == datetime(2026, 11, 14, 13, 4)
        await engine.dispose()

    asyncio.run(scenario())


def test_relevant_course_can_be_suggested_during_consultation(tmp_path):
    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        assistant = AssistantService(
            repo,
            knowledge(),
            ScriptedLLM(
                {"intent": "knowledge", "query": "Привлечение клиентов"},
                GROUNDED,
            ),
        )
        reply = await assistant.handle(
            telegram_user_id=1, text="Как привлечь клиентов к нам?", now=NOW,
        )
        assert reply.answer.sources
        assert any(item.kind == "course" for item in reply.answer.recommendations)
        await engine.dispose()

    asyncio.run(scenario())
