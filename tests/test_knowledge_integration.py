import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

from alfa_bot.contracts import Profile, SearchResult, SourceKey
from alfa_bot.knowledge.contracts import Profile as KnowledgeProfile
from alfa_bot.knowledge.contracts import SourceKey as KnowledgeSourceKey
from alfa_bot.knowledge.service import KnowledgeService
from alfa_bot.knowledge.sqlalchemy_pool import SQLAlchemyPool


def test_shared_contract_types_and_retrieval():
    assert KnowledgeProfile is Profile
    assert KnowledgeSourceKey is SourceKey
    run_id = uuid4()

    class Embedder:
        model_name = "model"
        resolved_revision = "revision"

        def encode_query(self, text):
            return [0.0] * 384

    class Repository:
        async def retrieve_by_vector(self, vector, **kwargs):
            assert len(vector) == 384
            assert kwargs["embedding_revision"] == "revision"
            return SearchResult(run_id, ())

    service = KnowledgeService(Repository(), Embedder(), chunker=lambda *args: ())
    result = asyncio.run(service.retrieve("test"))
    assert isinstance(result, SearchResult)
    assert result.run_id == run_id


def test_pool_returns_driver_only_while_checkout_is_open():
    driver = object()
    state = {"open": False}

    class Connection:
        async def get_raw_connection(self):
            assert state["open"]
            return type("Raw", (), {"driver_connection": driver})()

    class Engine:
        @asynccontextmanager
        async def connect(self):
            state["open"] = True
            try:
                yield Connection()
            finally:
                state["open"] = False

    async def check():
        async with SQLAlchemyPool(Engine()).connection() as connection:
            assert connection is driver
            assert state["open"]
        assert not state["open"]

    asyncio.run(check())
