"""Сценарии на реальных сервисах и SQLite; LLM заменён управляемым тестовым ответом."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alfa_bot.assistant.repository import AssistantRepository, ConcurrentTurnError
from alfa_bot.assistant.schemas import Decision, ReminderFields
from alfa_bot.assistant.service import AssistantService
from alfa_bot.assistant.validation import validate_reminder
from alfa_bot.contracts import ReplyType
from alfa_bot.infra.llm import DeepSeekLLM, LLMError
from alfa_bot.knowledge.ingestion import build_index
from alfa_bot.knowledge.models import Document, Material
from alfa_bot.knowledge.repository import KnowledgeRepository
from alfa_bot.knowledge.service import KnowledgeService
from alfa_bot.reminders.service import ReminderService

NOW = datetime(2026, 11, 14, 10, tzinfo=UTC)


def knowledge():
    materials = [
        Material(
            "a",
            "Привлечение клиентов",
            "Продвижение кофейни",
            "https://example.org/a",
            "article",
            "free",
        ),
        Material(
            "free",
            "Курс продаж",
            "Продажи и привлечение клиентов",
            "https://example.org/f",
            "course",
            "free",
        ),
        Material(
            "paid",
            "Курс продаж платный",
            "Продажи клиентам",
            "https://example.org/p",
            "course",
            "paid",
            "1000 RUB",
        ),
    ]
    docs = [
        Document(
            "a:0",
            "a",
            "Чтобы привлечь клиентов в кофейню, определите целевую аудиторию.",
            "knowledge",
        ),
        Document("f:0", "free", "Курс продаж: привлечение клиентов", "catalog"),
        Document("p:0", "paid", "Курс продаж: привлечение клиентов", "catalog"),
    ]
    return KnowledgeService(KnowledgeRepository(materials, docs))


class ScriptedLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, *, system, payload, schema):
        self.calls.append((schema, payload))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return schema.model_validate(response)


async def setup_repository(path: Path):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(path))
    repository = AssistantRepository(async_sessionmaker(engine, expire_on_commit=False))
    await repository.init_db()
    return engine, repository


def test_clarification_survives_restart_and_answer_uses_source(tmp_path):
    async def scenario():
        path = tmp_path / "memory.db"
        engine, repo = await setup_repository(path)
        llm = ScriptedLLM(
            {
                "intent": "knowledge",
                "query": "Как привлечь клиентов?",
                "clarification": "Какой у вас бизнес и вы уже открылись?",
            }
        )
        assistant = AssistantService(repo, knowledge(), llm)
        reply = await assistant.handle(telegram_user_id=1, text="Как привлечь клиентов?", now=NOW)
        assert reply.type == ReplyType.CLARIFICATION
        await engine.dispose()

        engine, repo = await setup_repository(path)
        llm = ScriptedLLM(
            {
                "intent": "knowledge",
                "continue_pending": True,
                "query": "Привлечение клиентов",
                "profile": {"business_type": "кофейня", "stage": "уже открылись"},
            },
            {
                "answerable": True,
                "paragraphs": [
                    {
                        "text": "Определите целевую аудиторию кофейни.",
                        "chunk_ids": ["a:0"],
                    }
                ],
            },
        )
        assistant = AssistantService(repo, knowledge(), llm)
        reply = await assistant.handle(telegram_user_id=1, text="Кофейня, уже открылись", now=NOW)
        assert reply.type == ReplyType.ANSWER
        assert "[1]" in reply.answer.text
        assert reply.answer.sources[0].url == "https://example.org/a"
        assert "Как привлечь клиентов?" in llm.calls[1][1]["question"]
        assert llm.calls[0][1]["pending"]["kind"] == "knowledge"
        saved = await repo.load(1)
        assert saved.profile["business_type"] == "кофейня"
        assert not saved.pending
        assert len(saved.history) == 4
        assert not (await repo.load(2)).history
        await engine.dispose()

    asyncio.run(scenario())


def test_reminder_details_are_collected_across_messages_and_prepare_accepts_them(tmp_path):
    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        llm = ScriptedLLM(
            {
                "intent": "reminder",
                "reminder": {"text": "Изучить статью"},
            },
            {
                "intent": "reminder",
                "continue_pending": True,
                "reminder": {
                    "local_datetime": "2026-11-15T10:00:00",
                    "timezone": "Europe/Moscow",
                },
            },
        )
        assistant = AssistantService(repo, knowledge(), llm)
        first = await assistant.handle(telegram_user_id=1, text="Напомни изучить статью", now=NOW)
        assert first.type == ReplyType.CLARIFICATION
        assert "часовой пояс" in first.text
        second = await assistant.handle(telegram_user_id=1, text="Завтра в 10 МСК", now=NOW)
        assert second.type == ReplyType.REMINDER
        assert second.reminder.text == "Изучить статью"
        service = ReminderService(repo.sessions)
        intent = second.reminder
        draft = service.prepare(
            telegram_user_id=1,
            chat_id=1,
            text=intent.text,
            local_datetime=intent.local_datetime,
            timezone_name=intent.timezone,
            frequency=intent.frequency,
            material_url=intent.material_url,
            now=NOW,
        )
        assert draft.starts_at == datetime(2026, 11, 15, 7, tzinfo=UTC)
        # Подготовка не создавала таблицу reminders и ничего в неё не записывала.
        await engine.dispose()

    asyncio.run(scenario())


def test_unknown_source_is_rejected_and_failed_turn_does_not_overwrite_memory(tmp_path):
    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        llm = ScriptedLLM(
            {"intent": "knowledge", "query": "Клиенты кофейни"},
            {
                "answerable": True,
                "paragraphs": [{"text": "Выдуманный ответ", "chunk_ids": ["invented-id"]}],
            },
        )
        reply = await AssistantService(repo, knowledge(), llm).handle(
            telegram_user_id=1,
            text="Клиенты кофейни",
            now=NOW,
        )
        assert "не получилось" in reply.answer.text
        assert not reply.answer.sources
        assert not (await repo.load(1)).history
        await engine.dispose()

    asyncio.run(scenario())


def test_recommendations_filter_kind_and_access_and_preserve_catalog_price(tmp_path):
    async def scenario():
        service = knowledge()
        free = await service.recommend("Продажи", kind="course", access="free")
        assert [hit.material.id for hit in free] == ["free"]
        paid = await service.recommend("Продажи", kind="course", access="paid")
        assert paid[0].material.card().price == "1000 RUB"
        assert not await service.retrieve("квантовая гравитация")

    asyncio.run(scenario())


def test_topic_change_out_of_scope_and_explicit_reset(tmp_path):
    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        llm = ScriptedLLM(
            {
                "intent": "knowledge",
                "query": "Как привлечь клиентов?",
                "clarification": "Какой бизнес?",
                "profile": {"business_type": "кофейня"},
            },
            {"intent": "out_of_scope"},
        )
        assistant = AssistantService(repo, knowledge(), llm)
        await assistant.handle(telegram_user_id=1, text="Как привлечь клиентов?", now=NOW)
        reply = await assistant.handle(telegram_user_id=1, text="Напиши стих", now=NOW)
        assert reply.type == ReplyType.OUT_OF_SCOPE
        await assistant.handle(telegram_user_id=1, text="/cancel", now=NOW)
        assert not (await repo.load(1)).pending
        assert (await repo.load(1)).profile
        await assistant.handle(telegram_user_id=1, text="/new", now=NOW)
        state = await repo.load(1)
        assert not state.profile and not state.history and not state.recent_materials
        assert len(llm.calls) == 2  # Служебные команды не обращаются к LLM.
        await engine.dispose()

    asyncio.run(scenario())


def test_optimistic_version_prevents_lost_update(tmp_path):
    async def scenario():
        engine, repo = await setup_repository(tmp_path / "memory.db")
        first, stale = await repo.load(1), await repo.load(1)
        first.profile = {"stage": "работает"}
        await repo.save(first)
        stale.profile = {"stage": "планируется"}
        with pytest.raises(ConcurrentTurnError):
            await repo.save(stale)
        assert (await repo.load(1)).profile == first.profile
        await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "local,zone,part",
    [
        ("2026-11-13T10:00", "Europe/Moscow", "прошло"),
        ("2026-11-15T10:00", "Unknown/City", "часовой пояс"),
        ("2027-03-28T02:30", "Europe/Berlin", "пропускается"),
        ("2027-10-31T02:30", "Europe/Berlin", "дважды"),
        ("2026-11-15T10:00+03:00", "Europe/Moscow", "отдельно"),
    ],
)
def test_invalid_reminder_datetime(local, zone, part):
    fields = ReminderFields(text="Задача", local_datetime=local, timezone=zone)
    intent, question = validate_reminder(fields, now=NOW, material_url=None)
    assert intent is None and part in question


def test_ingest_access_policy_atomic_snapshot_and_catalog(tmp_path):
    async def scenario():
        chunks, catalog, root = (
            tmp_path / "chunks.jsonl",
            tmp_path / "catalog.jsonl",
            tmp_path / "kb",
        )
        base = {
            "material_id": "a",
            "chunk_id": "a:0",
            "title": "Клиенты кофейни",
            "text": "Как привлечь\u00a0клиентов\\nв кофейню",
            "url_slug": "clients",
        }
        chunks.write_text(
            "\n".join(
                json.dumps(row, ensure_ascii=False)
                for row in [
                    base,
                    {**base, "material_id": "private", "chunk_id": "p:0", "is_public": False},
                    {**base, "material_id": "paid", "chunk_id": "p:1", "access": "paid"},
                ]
            ),
            encoding="utf-8",
        )
        course = {
            "id": "c",
            "title": "Курс продаж",
            "url": "https://example.org/c",
            "kind": "course",
            "access": "paid",
            "price": "1000 RUB",
            "is_public": True,
            "is_active": True,
            "description": "Продажи клиентам",
        }
        catalog.write_text(
            "\n".join(
                json.dumps(row)
                for row in [
                    course,
                    {**course, "id": "closed", "is_closed": True},
                    {"id": "no-policy"},
                ]
            ),
            encoding="utf-8",
        )
        stats = await build_index(chunks=chunks, catalog=catalog, root=root)
        assert stats["materials"] == 2 and stats["knowledge_chunks"] == 1
        repository = KnowledgeRepository.load(root)
        assert "\u00a0" not in repository.documents[0].text
        assert "\\n" not in repository.documents[0].text
        assert all(
            doc.material_id != "c" for doc in repository.documents if doc.purpose == "knowledge"
        )
        previous = (root / "CURRENT").read_text()
        chunks.write_text("broken JSON", encoding="utf-8")
        with pytest.raises(ValueError):
            await build_index(chunks=chunks, root=root)
        assert (root / "CURRENT").read_text() == previous
        assert len(KnowledgeRepository.load(root).materials) == 2

    asyncio.run(scenario())


def test_hybrid_rank_uses_dense_similarity_and_checks_dimension():
    repo = KnowledgeRepository(
        [
            Material("a", "Кофе", "", None, "article", "free"),
            Material("b", "Найм", "", None, "article", "free"),
        ],
        [
            Document("a:0", "a", "Напитки", "knowledge"),
            Document("b:0", "b", "Персонал", "knowledge"),
        ],
        vectors=np.array([[1.0, 0], [0, 1.0]], dtype=np.float32),
        model_name="test-model",
    )
    ranking = repo.rank("неизвестно", purpose="knowledge", query_vector=np.array([1.0, 0]))
    assert ranking[0][0] == 0
    with pytest.raises(ValueError):
        repo.rank("кофе", purpose="knowledge", query_vector=np.ones(3))


def test_http_llm_retries_blank_json_and_validates_schema():
    async def scenario():
        requests = []

        def handler(request):
            body = json.loads(request.content)
            requests.append(body)
            content = "   " if len(requests) == 1 else '{"intent":"out_of_scope"}'
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": content},
                        }
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            llm = DeepSeekLLM(api_key="test-key", client=client)
            result = await llm.complete(
                system="Return JSON", payload={"text": "hi"}, schema=Decision
            )
            assert result.intent == "out_of_scope"
            assert len(requests) == 2
            assert requests[0]["response_format"] == {"type": "json_object"}

    asyncio.run(scenario())


def test_http_llm_auth_failure_is_not_retried_and_does_not_expose_key():
    async def scenario():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(401, json={"error": "sensitive-provider-message"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            llm = DeepSeekLLM(api_key="secret-test-key", client=client)
            with pytest.raises(LLMError) as error:
                await llm.complete(system="Return JSON", payload={}, schema=Decision)
            assert len(calls) == 1
            assert str(error.value) == "LLM HTTP 401"

    asyncio.run(scenario())
