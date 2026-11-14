import asyncio
from datetime import datetime

import pytest
from test_scenarios import NOW, ScriptedLLM, knowledge, setup_repository

from alfa_bot.assistant.service import AssistantService
from alfa_bot.contracts import ReplyType
from alfa_bot.infra.llm import LLMError
from alfa_bot.knowledge.models import Document, Material
from alfa_bot.knowledge.repository import KnowledgeRepository
from alfa_bot.knowledge.service import KnowledgeService


def test_no_courses_means_empty_recommendation_instead_of_invented_card(tmp_path):
    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        only_articles = KnowledgeService(
            KnowledgeRepository(
                [Material("a", "Продажи", "", "https://example.org/a", "article", "free")],
                [Document("a:0", "a", "Продажи", "catalog")],
            )
        )
        assistant = AssistantService(
            repo,
            only_articles,
            ScriptedLLM(
                {
                    "intent": "recommendation",
                    "query": "Продажи",
                    "requested_kind": "course",
                }
            ),
        )
        reply = await assistant.handle(telegram_user_id=1, text="Курсы продаж", now=NOW)
        assert reply.type == ReplyType.ANSWER
        assert "нет подходящих курсов" in reply.answer.text
        assert not reply.answer.recommendations
        await engine.dispose()

    asyncio.run(scenario())


def test_source_without_url_does_not_shift_card_numbers(tmp_path):
    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        service = KnowledgeService(
            KnowledgeRepository(
                [
                    Material("a", "Клиенты без ссылки", "", None, "article", "free"),
                    Material(
                        "b", "Клиенты со ссылкой", "", "https://example.org/b", "article", "free"
                    ),
                ],
                [
                    Document("a:0", "a", "Клиенты", "knowledge"),
                    Document("b:0", "b", "Клиенты", "knowledge"),
                ],
            )
        )
        llm = ScriptedLLM(
            {"intent": "knowledge", "query": "Клиенты"},
            {
                "answerable": True,
                "paragraphs": [
                    {"text": "Первая рекомендация.", "chunk_ids": ["a:0"]},
                    {"text": "Вторая рекомендация.", "chunk_ids": ["b:0"]},
                ],
            },
        )
        reply = await AssistantService(repo, service, llm).handle(
            telegram_user_id=1,
            text="Клиенты",
            now=NOW,
        )
        assert "(источник: Клиенты без ссылки)" in reply.answer.text
        assert "Вторая рекомендация. [1]" in reply.answer.text
        assert (
            len(reply.answer.sources) == 1 and reply.answer.sources[0].title == "Клиенты со ссылкой"
        )
        await engine.dispose()

    asyncio.run(scenario())


def test_llm_timeout_preserves_pending_and_profile(tmp_path):
    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        state = await repo.load(1)
        state.pending = {"kind": "knowledge", "query": "Как привлечь клиентов?"}
        state.profile = {"business_type": "кофейня"}
        await repo.save(state)
        assistant = AssistantService(repo, knowledge(), ScriptedLLM(LLMError("timeout")))
        reply = await assistant.handle(telegram_user_id=1, text="Уже открылись", now=NOW)
        assert "не получилось" in reply.answer.text
        updated = await repo.load(1)
        assert updated.pending == state.pending and updated.profile == state.profile
        assert updated.version == state.version
        await engine.dispose()

    asyncio.run(scenario())


def test_unknown_material_id_does_not_attach_link_to_reminder(tmp_path):
    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        llm = ScriptedLLM(
            {
                "intent": "reminder",
                "reminder": {
                    "text": "Изучить курс",
                    "local_datetime": "2026-11-15T10:00:00",
                    "timezone": "Europe/Moscow",
                    "material_id": "paid",
                },
            }
        )
        reply = await AssistantService(repo, knowledge(), llm).handle(
            telegram_user_id=1,
            text="Завтра в 10 МСК напомни",
            now=NOW,
        )
        assert reply.type == ReplyType.REMINDER and reply.reminder.material_url is None
        await engine.dispose()

    asyncio.run(scenario())


def test_repeated_knowledge_clarification_proceeds_to_answer(tmp_path):
    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        llm = ScriptedLLM(
            {"intent": "knowledge", "query": "Клиенты", "clarification": "Какой бизнес?"},
            {
                "intent": "knowledge",
                "continue_pending": True,
                "query": "Клиенты кофейни",
                "clarification": "Где находится кофейня?",
            },
            {
                "answerable": True,
                "paragraphs": [{"text": "Определите аудиторию.", "chunk_ids": ["a:0"]}],
            },
        )
        assistant = AssistantService(repo, knowledge(), llm)
        await assistant.handle(telegram_user_id=1, text="Клиенты", now=NOW)
        reply = await assistant.handle(telegram_user_id=1, text="Кофейня", now=NOW)
        assert reply.type == ReplyType.ANSWER
        await engine.dispose()

    asyncio.run(scenario())


def test_naive_now_is_rejected_before_loading_state():
    assistant = AssistantService(None, None, None)
    with pytest.raises(ValueError):
        asyncio.run(assistant.handle(telegram_user_id=1, text="Вопрос", now=datetime(2026, 11, 14)))


def test_orm_postgres_ddl_compiles():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.schema import CreateTable

    from alfa_bot.assistant.models import AssistantDialog

    ddl = str(CreateTable(AssistantDialog.__table__).compile(dialect=postgresql.dialect()))
    assert "JSONB" in ddl and "BIGINT" in ddl
