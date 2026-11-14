"""Точный cosine-поиск по pgvector и выборка карточек."""

import logging
from dataclasses import dataclass

import numpy as np
from pgvector import Vector
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alfa_bot.contracts import MaterialCard
from alfa_bot.infra.embeddings import EmbeddingError, Embeddings
from alfa_bot.knowledge.models import Document, KnowledgeUnavailable, Material, SearchHit

logger = logging.getLogger(__name__)

INDEX_STATE = text("SELECT model_name, dimensions, fingerprint FROM kb.index_state WHERE id = 1")
SEARCH = text("""
    WITH matches AS (
        SELECT id, material_id, text, metadata,
               1 - (embedding <=> CAST(CAST(:embedding AS text) AS vector)) AS score
        FROM kb.chunks
        WHERE purpose = :purpose
          AND (CAST(:kind AS text) = 'any' OR metadata ->> 'kind' = :kind)
          AND (CAST(:access AS text) = 'any' OR metadata ->> 'access' = :access)
          AND NOT (material_id = ANY(CAST(:exclude AS text[])))
    ),
    ranked AS (
        SELECT *, row_number() OVER (PARTITION BY material_id ORDER BY score DESC, id) AS position
        FROM matches
        WHERE score >= :minimum_score
    )
    SELECT id, material_id, text, metadata, score
    FROM ranked WHERE position <= :max_per_material
    ORDER BY score DESC, id
    LIMIT :limit
""")
GET_CARD = text("""
    SELECT metadata FROM kb.chunks
    WHERE purpose = 'catalog' AND material_id = :material_id
    LIMIT 1
""")


@dataclass(frozen=True)
class IndexState:
    model_name: str
    dimensions: int
    fingerprint: str


async def read_index_state(session: AsyncSession) -> IndexState | None:
    row = (await session.execute(INDEX_STATE)).one_or_none()
    return IndexState(*row) if row is not None else None


def _material(metadata: dict) -> Material:
    return Material(**{key: value for key, value in metadata.items() if key != "is_paid"})


class PostgresKnowledge:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        embeddings: Embeddings,
        *,
        minimum_score: float = 0.6,
        catalog_minimum_score: float = 0.55,
        top_k: int = 5,
    ):
        self.sessions = sessions
        self.embeddings = embeddings
        self.minimum_score = minimum_score
        self.catalog_minimum_score = catalog_minimum_score
        self.top_k = top_k

    async def _state(self) -> IndexState | None:
        async with self.sessions() as session:
            return await read_index_state(session)

    def _check_model(self, state: IndexState | None) -> None:
        if state is None:
            raise KnowledgeUnavailable("Индекс пуст. Выполните knowledge.ingest.")
        if state.model_name != self.embeddings.model_name:
            raise KnowledgeUnavailable(
                "Модель эмбеддингов отличается от индекса. Пересоберите его."
            )

    async def ensure_ready(self) -> None:
        self._check_model(await self._state())

    async def _search(
        self,
        query: str,
        *,
        purpose: str,
        limit: int,
        kind: str = "any",
        access: str = "any",
        exclude: set[str] | None = None,
    ) -> list[SearchHit]:
        if not query.strip():
            return []
        try:
            vector = np.asarray(await self.embeddings.embed_query(query), dtype=np.float32)
        except EmbeddingError as error:
            raise KnowledgeUnavailable("Не удалось получить эмбеддинг запроса.") from error
        if vector.ndim != 1 or not np.isfinite(vector).all() or not np.any(vector):
            raise KnowledgeUnavailable("Модель вернула некорректный эмбеддинг запроса.")

        async with self.sessions.begin() as session:
            # Индекс может обновиться одновременно: читаем описание и чанки из одного снимка.
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            state = await read_index_state(session)
            self._check_model(state)
            if vector.size != state.dimensions:
                raise KnowledgeUnavailable("Размер эмбеддинга отличается от индекса.")
            result = await session.execute(
                SEARCH,
                {
                    "embedding": Vector(vector).to_text(),
                    "purpose": purpose,
                    "limit": limit,
                    "kind": kind,
                    "access": access,
                    "exclude": sorted(exclude or ()),
                    "minimum_score": (
                        self.minimum_score if purpose == "knowledge" else self.catalog_minimum_score
                    ),
                    "max_per_material": 2 if purpose == "knowledge" else 1,
                },
            )
            hits = [
                SearchHit(
                    Document(row["id"], row["material_id"], row["text"], purpose),
                    _material(row["metadata"]),
                    row["score"],
                )
                for row in result.mappings()
            ]
        logger.debug("Knowledge search purpose=%s hits=%d", purpose, len(hits))
        return hits

    async def retrieve(self, query: str, *, limit: int | None = None) -> list[SearchHit]:
        limit = self.top_k if limit is None else limit
        if not 1 <= limit <= 20:
            raise ValueError("limit должен быть от 1 до 20")
        return await self._search(query, purpose="knowledge", limit=limit, access="free")

    async def recommend(
        self,
        query: str,
        *,
        exclude: set[str] | None = None,
        limit: int = 2,
        kind: str = "any",
        access: str = "any",
    ) -> list[SearchHit]:
        if not 1 <= limit <= 10:
            raise ValueError("limit должен быть от 1 до 10")
        return await self._search(
            query,
            purpose="catalog",
            limit=limit,
            kind=kind,
            access=access,
            exclude=exclude,
        )

    async def get_card(self, material_id: str) -> MaterialCard | None:
        async with self.sessions() as session:
            metadata = await session.scalar(GET_CARD, {"material_id": material_id})
        return _material(metadata).card() if metadata else None
