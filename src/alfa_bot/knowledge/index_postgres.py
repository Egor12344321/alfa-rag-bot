"""Пересобирает индекс, сохраняя предыдущий снимок до commit."""

import logging
from itertools import batched

import numpy as np
from pgvector import Vector
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alfa_bot.infra.embeddings import Embeddings
from alfa_bot.knowledge.corpus import CorpusChunk, chunk_fingerprint, chunk_metadata
from alfa_bot.knowledge.retrieval import read_index_state

logger = logging.getLogger(__name__)

# Ключ advisory lock общий для всех команд, которые обновляют индекс этой БД.
INDEX_LOCK = text("SELECT pg_advisory_xact_lock(6188202601)")
WRITE_STATE = text("""
    INSERT INTO kb.index_state (id, model_name, dimensions, fingerprint)
    VALUES (1, :model, :dimensions, :fingerprint)
    ON CONFLICT (id) DO UPDATE SET
        model_name = EXCLUDED.model_name,
        dimensions = EXCLUDED.dimensions,
        fingerprint = EXCLUDED.fingerprint
""")
INSERT_CHUNK = text("""
    INSERT INTO kb.chunks (id, text, material_id, purpose, metadata, embedding)
    VALUES (:id, :text, :material_id, :purpose, :metadata, CAST(CAST(:vector AS text) AS vector))
""").bindparams(bindparam("metadata", type_=JSONB))


async def has_index(sessions: async_sessionmaker[AsyncSession]) -> bool:
    async with sessions() as session:
        return await read_index_state(session) is not None


async def replace_index(
    sessions: async_sessionmaker[AsyncSession],
    chunks: list[CorpusChunk],
    embeddings: Embeddings,
    *,
    if_empty: bool = False,
) -> dict:
    if not chunks:
        raise ValueError("Индекс без чанков нельзя опубликовать.")
    model = embeddings.model_name
    fingerprint = chunk_fingerprint(chunks, model)
    stats = {
        "model": model,
        "materials": len({item.material.id for item in chunks}),
        "knowledge_chunks": sum(item.document.purpose == "knowledge" for item in chunks),
        "catalog_chunks": sum(item.document.purpose == "catalog" for item in chunks),
    }
    async with sessions() as session:
        if (current := await read_index_state(session)) and (
            if_empty or current.fingerprint == fingerprint
        ):
            logger.info("Knowledge index already exists; upload skipped")
            return stats | {"status": "unchanged"}

    # Кодируем до транзакции, чтобы индекс оставался доступным во время расчёта.
    vectors = []
    dimensions = None
    for group in batched(chunks, 32):
        vectors_batch = np.asarray(
            await embeddings.embed_documents(
                [f"{item.material.title}\n{item.document.text}" for item in group]
            ),
            dtype=np.float32,
        )
        if (
            vectors_batch.ndim != 2
            or vectors_batch.shape[0] != len(group)
            or not np.isfinite(vectors_batch).all()
            or not np.any(vectors_batch, axis=1).all()
            or (dimensions is not None and vectors_batch.shape[1] != dimensions)
        ):
            raise ValueError("Модель вернула несовместимые эмбеддинги.")
        dimensions = vectors_batch.shape[1]
        vectors.extend(vectors_batch)
        if len(vectors) % 512 == 0:
            logger.info("Embedded %d/%d chunks", len(vectors), len(chunks))

    async with sessions.begin() as session:
        await session.execute(INDEX_LOCK)
        if (current := await read_index_state(session)) and (
            if_empty or current.fingerprint == fingerprint
        ):
            return stats | {"status": "unchanged"}
        await session.execute(text("DELETE FROM kb.chunks"))
        for batch in batched(zip(chunks, vectors, strict=True), 256):
            await session.execute(
                INSERT_CHUNK,
                [
                    {
                        "id": item.document.id,
                        "text": item.document.text,
                        "material_id": item.material.id,
                        "purpose": item.document.purpose,
                        "metadata": chunk_metadata(item.material),
                        "vector": Vector(vector).to_text(),
                    }
                    for item, vector in batch
                ],
            )
        await session.execute(
            WRITE_STATE,
            {"model": model, "dimensions": dimensions, "fingerprint": fingerprint},
        )
    logger.info("Knowledge index published: %d chunks, dimension=%d", len(chunks), dimensions)
    return stats | {"status": "updated", "dimensions": dimensions}
