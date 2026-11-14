import asyncio
import json
from hashlib import sha256

import httpx
import numpy as np
import pytest

from alfa_bot.assistant.schemas import Decision
from alfa_bot.infra.embeddings import OllamaEmbeddings
from alfa_bot.infra.llm import OllamaLLM
from alfa_bot.knowledge.corpus import PublishedSource, prepare_corpus, read_published_jsonl
from alfa_bot.knowledge.models import KnowledgeUnavailable
from alfa_bot.knowledge.retrieval import IndexState, PostgresKnowledge


def test_corpus_keeps_only_public_bodies_and_splits_by_token(tmp_path):
    base = {
        "source_id": "1",
        "source_type": "article",
        "title": "Как открыть кофейню",
        "description": "Подготовка и маркетинг",
        "url": "https://example.org/a",
        "is_active": True,
        "is_public": True,
        "access": "free",
        "body": "<script>secret-script</script><p>" + ("план кофейни " * 100) + "</p>",
    }
    sources = [
        base,
        base | {"source_id": "2", "is_closed": True},
        base | {"source_id": "3", "is_active": False},
        base | {"source_id": "4", "is_public": False},
        base | {"source_id": "5", "access": "paid", "body": "secret-paid-article"},
        base
        | {
            "source_id": "6",
            "source_type": "course",
            "access": "paid",
            "body": "secret-lesson",
        },
        base | {"source_id": "7", "source_type": "course", "access": "free"},
    ]
    path = tmp_path / "sources.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in sources), encoding="utf-8")
    chunks = prepare_corpus(read_published_jsonl(path), chunk_size=80, overlap=10)
    fingerprints = {chunk.material.id for chunk in chunks}
    assert fingerprints == {"article:1", "article:5", "course:6", "course:7"}
    evidence = [chunk for chunk in chunks if chunk.document.purpose == "knowledge"]
    assert len(evidence) > 1
    assert all(chunk.material.id == "article:1" for chunk in evidence)
    assert all("secret" not in chunk.document.text for chunk in chunks)
    assert len({chunk.document.id for chunk in chunks}) == len(chunks)


def test_unpublished_card_and_course_without_link_are_excluded():
    course = PublishedSource(
        source_id="2",
        source_type="course",
        title="Курс",
        access="paid",
        is_public=True,
        is_active=True,
    )
    with pytest.raises(ValueError, match="публичных материалов"):
        prepare_corpus([course], chunk_size=80, overlap=10)


def test_ollama_contracts_use_local_structured_output_and_no_truncation():
    async def scenario():
        calls = []

        def handle(request):
            payload = json.loads(request.content)
            calls.append((request.url.path, payload))
            if request.url.path == "/api/embed":
                return httpx.Response(200, json={"embeddings": [[0.1, 0.5]]})
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "done_reason": "stop",
                    "message": {
                        "content": '{"intent":"out_of_scope"}',
                    },
                },
            )

        async with httpx.AsyncClient(
            base_url="http://ollama:11434",
            transport=httpx.MockTransport(handle),
        ) as client:
            embeddings = OllamaEmbeddings("bge-m3:567m", client)
            llm = OllamaLLM("qwen2.5:3b", client, context_length=8192)
            assert np.allclose(await embeddings.embed_query("клиенты"), [0.1, 0.5])
            result = await llm.complete(
                system="правила", payload={"text": "вопрос"}, schema=Decision
            )
            assert result.intent == "out_of_scope"
        assert calls[0][1]["truncate"] is False
        assert calls[1][1]["format"]["type"] == "object"
        assert calls[1][1]["messages"][0]["role"] == "system"
        assert "вопрос" in calls[1][1]["messages"][1]["content"]

    asyncio.run(scenario())


def test_cosine_score_rejects_wrong_embedding_model_without_exposing_database():
    async def scenario():
        index = PostgresKnowledge(None, OllamaEmbeddings("test-model", None))

        async def state():
            return IndexState("some-other-model", 2, sha256(b"").hexdigest())

        index._state = state
        with pytest.raises(KnowledgeUnavailable, match="Модель"):
            await index.ensure_ready()

    asyncio.run(scenario())
