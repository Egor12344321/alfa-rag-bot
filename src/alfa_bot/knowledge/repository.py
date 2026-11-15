"""PostgreSQL persistence and exact pgvector retrieval for ``kb``.

Apply the application Alembic migrations to the dedicated assistant database.
This module never writes to the source dump database.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, Sequence
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from .contracts import (
    ChunkInput,
    Evidence,
    MaterialCard,
    MaterialInput,
    Profile,
    SearchResult,
    SourceKey,
)

EMBEDDING_DIM = 384


class AsyncConnection(Protocol):
    async def execute(self, query: str, params: tuple[Any, ...] = ()) -> Any: ...

    def transaction(self) -> Any: ...


class AsyncConnectionPool(Protocol):
    def connection(self) -> Any: ...


@dataclass(frozen=True)
class IndexedMaterial:
    """One already-authorized material, its chunks, and aligned vectors."""

    material: MaterialInput
    chunks: Sequence[ChunkInput]
    embeddings: Sequence[Sequence[float]]


class NoReadyIndex(RuntimeError):
    """No published index exists, or a requested run is not ready."""


class InvalidIngestion(ValueError):
    """A prepared material set violates the kb or access-policy contract."""


class EmbeddingCompatibilityError(RuntimeError):
    """The loaded embedding model does not match the selected index run."""


def _vector_literal(vector: Sequence[float]) -> str:
    if not 1 <= len(vector) <= 8192:
        raise InvalidIngestion("Embedding dimension must be between 1 and 8192")
    if not any(float(x) != 0 for x in vector):
        raise InvalidIngestion("Embedding cannot be a zero vector")
    values: list[str] = []
    for value in vector:
        number = float(value)
        if not math.isfinite(number):
            raise InvalidIngestion("Embedding contains a non-finite value")
        values.append(format(number, ".9g"))
    return "[" + ",".join(values) + "]"


def _valid_source_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlsplit(url)
    return parsed.scheme.lower() == "https" and bool(parsed.netloc)


def _material_hash(material: MaterialInput) -> str:
    digest = hashlib.sha256()
    digest.update(material.source_table.encode())
    digest.update(b"\0")
    digest.update(material.source_id.encode())
    digest.update(b"\0")
    digest.update(material.title.encode())
    digest.update(b"\0")
    digest.update((material.url or "").encode())
    digest.update(str(material.price).encode())
    digest.update((material.currency or "").encode())
    digest.update((material.price_note or "").encode())
    digest.update(material.summary.encode())
    for topic in material.topics:
        digest.update(b"\0topic\0")
        digest.update(topic.source_id.encode())
        digest.update(topic.title.encode())
    for target in material.recommendations:
        digest.update(b"\0recommendation\0")
        digest.update(target.source_table.encode())
        digest.update(target.source_id.encode())
    for block in sorted(material.blocks, key=lambda b: (b.order, b.block_id)):
        digest.update(b"\0")
        digest.update(block.block_id.encode())
        digest.update(b"\0")
        digest.update(block.content.encode())
    return digest.hexdigest()


def _validate_records(records: Sequence[IndexedMaterial]) -> None:
    if not records:
        raise InvalidIngestion("Cannot publish an empty knowledge run")
    dimension = len(records[0].embeddings[0]) if records[0].embeddings else 0
    seen_materials: set[tuple[str, str]] = set()
    for record in records:
        material = record.material
        key = (material.source_table, material.source_id)
        if key in seen_materials:
            raise InvalidIngestion(f"Duplicate source identity: {key}")
        seen_materials.add(key)
        if not material.title.strip():
            raise InvalidIngestion(f"Empty title for source {key}")
        if not material.answer_allowed and not material.recommend_allowed:
            raise InvalidIngestion(f"Source {key} has no answer or catalog permission")
        if material.answer_allowed and material.access_type != "free":
            raise InvalidIngestion(f"Source {key} is not confirmed free answer evidence")
        if material.recommend_allowed and (
            not material.summary.strip() or not _valid_source_url(material.url)
        ):
            raise InvalidIngestion(f"Catalog source {key} has no public summary or verified URL")
        if material.price is not None and material.price < 0:
            raise InvalidIngestion(f"Source {key} has a negative price")
        expected_purposes = set()
        if material.answer_allowed:
            expected_purposes.add("knowledge")
        if material.recommend_allowed:
            expected_purposes.add("catalog")
        if not record.chunks or len(record.chunks) != len(record.embeddings):
            raise InvalidIngestion(f"Source {key} has no chunks or mismatched embeddings")
        block_ids = {block.block_id for block in material.blocks}
        chunk_numbers: set[tuple[str, int]] = set()
        for chunk, embedding in zip(record.chunks, record.embeddings, strict=True):
            if chunk.purpose not in expected_purposes:
                raise InvalidIngestion(f"Chunk purpose is not allowed for source {key}")
            if not chunk.text.strip() or not 0 <= chunk.token_count <= 8192:
                raise InvalidIngestion(f"Invalid text/token count for source {key}")
            allowed_block_ids = block_ids | (
                {"public-summary"} if chunk.purpose == "catalog" else set()
            )
            if any(block_id not in allowed_block_ids for block_id in chunk.source_blocks):
                raise InvalidIngestion(f"Chunk from {key} references an unknown source block")
            chunk_key = (chunk.purpose, chunk.chunk_no)
            if chunk_key in chunk_numbers:
                raise InvalidIngestion(f"Duplicate chunk number in source {key}: {chunk.chunk_no}")
            chunk_numbers.add(chunk_key)
            _vector_literal(embedding)
            if len(embedding) != dimension:
                raise InvalidIngestion("Mixed embedding dimensions in one run")


class KnowledgeRepository:
    """Async repository over a psycopg 3 ``AsyncConnectionPool``-compatible pool."""

    def __init__(self, pool: AsyncConnectionPool):
        self._pool = pool

    async def publish_run(
        self,
        records: Sequence[IndexedMaterial],
        *,
        source_fingerprint: str,
        embedding_model: str,
        embedding_revision: str,
        pipeline_version: str,
        mapping_version: str,
        policy_version: str,
    ) -> UUID:
        """Build in isolation, then atomically mark ready and switch active run.

        A building run is committed first for diagnostics. All material/chunk
        writes and publication happen in a second transaction. A failed second
        transaction rolls back its rows and records ``failed`` separately, so
        the previous ``kb.index_state`` remains untouched.
        """
        _validate_records(records)
        if (
            not embedding_model.strip()
            or not embedding_revision.strip()
            or embedding_revision == "local-cache"
        ):
            raise InvalidIngestion(
                "A loaded embedding model and immutable resolved revision are required"
            )
        run_id = uuid4()
        async with self._pool.connection() as conn:
            async with conn.transaction():
                # Reusing the active run for an identical source and pipeline
                # makes a normal repeat import a no-op instead of duplicating
                # the same materials/chunks as a second published version.
                cursor = await conn.execute(
                    """SELECT r.id FROM kb.index_state s
                       JOIN kb.ingestion_runs r ON r.id = s.active_run_id
                       WHERE s.singleton = TRUE AND r.status = 'ready'
                         AND r.source_fingerprint = %s
                         AND r.embedding_model = %s
                         AND r.embedding_revision = %s
                         AND r.pipeline_version = %s
                         AND r.mapping_version = %s
                         AND r.policy_version = %s""",
                    (
                        source_fingerprint,
                        embedding_model,
                        embedding_revision,
                        pipeline_version,
                        mapping_version,
                        policy_version,
                    ),
                )
                existing_run = await cursor.fetchone()
                if existing_run is not None:
                    return existing_run[0]
                await conn.execute(
                    """INSERT INTO kb.ingestion_runs
                       (id, status, source_fingerprint, embedding_model,
                        embedding_revision, embedding_dim, pipeline_version,
                        mapping_version, policy_version)
                       VALUES (%s, 'building', %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        run_id,
                        source_fingerprint,
                        embedding_model,
                        embedding_revision,
                        len(records[0].embeddings[0]),
                        pipeline_version,
                        mapping_version,
                        policy_version,
                    ),
                )

        material_count = 0
        chunk_count = 0
        material_ids_by_key: dict[tuple[str, str], UUID] = {}
        topic_count = 0
        relation_count = 0
        try:
            async with self._pool.connection() as conn:
                async with conn.transaction():
                    for indexed in records:
                        material = indexed.material
                        material_hash = _material_hash(material)
                        cursor = await conn.execute(
                            """INSERT INTO kb.materials
                               (run_id, source_table, source_id, kind, title, summary,
                                url, access_type, is_active, answer_allowed,
                                recommend_allowed, external_allowed, source_modified_at,
                                price, currency, price_note, content_hash, provenance)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s,
                                       %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                               ON CONFLICT (run_id, source_table, source_id) DO UPDATE SET
                                kind = EXCLUDED.kind,
                                title = EXCLUDED.title,
                                summary = EXCLUDED.summary,
                                url = EXCLUDED.url,
                                access_type = EXCLUDED.access_type,
                                is_active = EXCLUDED.is_active,
                                answer_allowed = EXCLUDED.answer_allowed,
                                recommend_allowed = EXCLUDED.recommend_allowed,
                                external_allowed = EXCLUDED.external_allowed,
                                source_modified_at = EXCLUDED.source_modified_at,
                                price = EXCLUDED.price,
                                currency = EXCLUDED.currency,
                                price_note = EXCLUDED.price_note,
                                content_hash = EXCLUDED.content_hash,
                                provenance = EXCLUDED.provenance
                               RETURNING id""",
                            (
                                run_id,
                                material.source_table,
                                material.source_id,
                                material.kind,
                                material.title.strip(),
                                material.summary,
                                material.url,
                                material.access_type,
                                material.answer_allowed,
                                material.recommend_allowed,
                                material.external_allowed,
                                material.source_modified_at,
                                material.price,
                                material.currency,
                                material.price_note,
                                material_hash,
                                json.dumps(
                                    {
                                        **material.provenance,
                                        "reason_features": list(material.reason_features),
                                    },
                                    ensure_ascii=False,
                                    default=str,
                                ),
                            ),
                        )
                        material_row = await cursor.fetchone()
                        material_id = material_row[0]
                        material_ids_by_key[(material.source_table, material.source_id)] = (
                            material_id
                        )
                        material_count += 1

                        for chunk, vector in zip(indexed.chunks, indexed.embeddings, strict=True):
                            chunk_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
                            await conn.execute(
                                """INSERT INTO kb.chunks
                                   (run_id, material_id, purpose, chunk_no, text,
                                    section_path, source_blocks, token_count,
                                    content_hash, embedding)
                                   VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb,
                                           %s, %s, %s::vector)
                                   ON CONFLICT (material_id, purpose, chunk_no) DO UPDATE SET
                                    run_id = EXCLUDED.run_id,
                                    text = EXCLUDED.text,
                                    section_path = EXCLUDED.section_path,
                                    source_blocks = EXCLUDED.source_blocks,
                                    token_count = EXCLUDED.token_count,
                                    content_hash = EXCLUDED.content_hash,
                                    embedding = EXCLUDED.embedding""",
                                (
                                    run_id,
                                    material_id,
                                    chunk.purpose,
                                    chunk.chunk_no,
                                    chunk.text,
                                    chunk.section_path,
                                    json.dumps(list(chunk.source_blocks), ensure_ascii=False),
                                    chunk.token_count,
                                    chunk_hash,
                                    _vector_literal(vector),
                                ),
                            )
                            chunk_count += 1

                    # Topics and recommendations are attached only to material
                    # rows present in this exact run. Cross-version edges cannot
                    # enter the schema because both FKs carry run_id.
                    topics_by_source: dict[str, Any] = {}
                    for indexed in records:
                        source_key = (indexed.material.source_table, indexed.material.source_id)
                        material_id = material_ids_by_key[source_key]
                        for topic in indexed.material.topics:
                            existing_topic = topics_by_source.get(topic.source_id)
                            if existing_topic is not None and existing_topic != topic.title:
                                raise InvalidIngestion(
                                    f"Topic {topic.source_id!r} has inconsistent titles within run"
                                )
                            topics_by_source[topic.source_id] = topic.title
                            cursor = await conn.execute(
                                """INSERT INTO kb.topics(run_id, source_id, title)
                                   VALUES (%s, %s, %s)
                                   ON CONFLICT (run_id, source_id) DO UPDATE
                                     SET title = EXCLUDED.title
                                   RETURNING id""",
                                (run_id, topic.source_id, topic.title),
                            )
                            topic_id = (await cursor.fetchone())[0]
                            await conn.execute(
                                """INSERT INTO kb.material_topics(run_id, material_id, topic_id)
                                   VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""",
                                (run_id, material_id, topic_id),
                            )
                            topic_count += 1
                        for recommendation_order, target in enumerate(
                            indexed.material.recommendations, start=1
                        ):
                            target_id = material_ids_by_key.get(
                                (target.source_table, target.source_id)
                            )
                            if target_id is None or target_id == material_id:
                                continue
                            await conn.execute(
                                """INSERT INTO kb.material_relations
                                   (run_id, from_material_id, to_material_id,
                                    relation_type, provenance)
                                   VALUES (%s, %s, %s, 'recommended', %s::jsonb)
                                   ON CONFLICT (from_material_id, to_material_id, relation_type)
                                   DO NOTHING""",
                                (
                                    run_id,
                                    material_id,
                                    target_id,
                                    json.dumps(
                                        {
                                            "source": "RecommendedItems.Id -> ItemId",
                                            "order": recommendation_order,
                                        }
                                    ),
                                ),
                            )
                            relation_count += 1

                    cursor = await conn.execute(
                        """SELECT count(DISTINCT m.id), count(c.id),
                                  count(c.id) FILTER (
                                      WHERE (c.purpose = 'knowledge'
                                             AND (NOT m.answer_allowed OR m.access_type <> 'free'))
                                         OR (c.purpose = 'catalog' AND NOT m.recommend_allowed)
                                  ),
                                  count(DISTINCT m.id) FILTER (
                                      WHERE m.answer_allowed AND NOT EXISTS (
                                          SELECT 1 FROM kb.chunks k
                                          WHERE k.run_id=m.run_id AND k.material_id=m.id
                                            AND k.purpose='knowledge'
                                      )
                                  ),
                                  count(DISTINCT m.id) FILTER (
                                      WHERE m.recommend_allowed AND NOT EXISTS (
                                          SELECT 1 FROM kb.chunks k
                                          WHERE k.run_id=m.run_id AND k.material_id=m.id
                                            AND k.purpose='catalog'
                                      )
                                  )
                           FROM kb.materials m LEFT JOIN kb.chunks c
                             ON c.run_id = m.run_id AND c.material_id = m.id
                           WHERE m.run_id = %s""",
                        (run_id,),
                    )
                    (
                        persisted_materials,
                        persisted_chunks,
                        invalid_purpose_chunks,
                        missing_knowledge_chunks,
                        missing_catalog_chunks,
                    ) = await cursor.fetchone()
                    if (
                        persisted_materials != len(records)
                        or persisted_chunks != chunk_count
                        or invalid_purpose_chunks != 0
                        or missing_knowledge_chunks != 0
                        or missing_catalog_chunks != 0
                    ):
                        raise InvalidIngestion(
                            "Run integrity check failed before publication: "
                            f"materials={persisted_materials}/{len(records)}, "
                            f"chunks={persisted_chunks}/{chunk_count}, "
                            f"invalid_purpose_chunks={invalid_purpose_chunks}, "
                            f"missing_knowledge_chunks={missing_knowledge_chunks}, "
                            f"missing_catalog_chunks={missing_catalog_chunks}"
                        )

                    metrics = {
                        "materials": material_count,
                        "chunks": chunk_count,
                        "embedding_dim": len(records[0].embeddings[0]),
                        "topics": topic_count,
                        "relations": relation_count,
                        "embedding_model": embedding_model,
                        "embedding_revision": embedding_revision,
                    }
                    await conn.execute(
                        """UPDATE kb.ingestion_runs
                           SET status = 'ready', finished_at = %s, metrics = %s::jsonb
                           WHERE id = %s AND status = 'building'""",
                        (datetime.now(timezone.utc), json.dumps(metrics), run_id),
                    )
                    await conn.execute(
                        """INSERT INTO kb.index_state(singleton, active_run_id)
                           VALUES (TRUE, %s)
                           ON CONFLICT (singleton) DO UPDATE
                           SET active_run_id = EXCLUDED.active_run_id""",
                        (run_id,),
                    )
        except Exception:
            # The write/publication transaction has rolled back. Keep the old
            # pointer and mark the detached run for diagnosis in a fresh txn.
            async with self._pool.connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """UPDATE kb.ingestion_runs
                           SET status = 'failed', finished_at = %s
                           WHERE id = %s AND status = 'building'""",
                        (datetime.now(timezone.utc), run_id),
                    )
            raise
        return run_id

    async def retrieve_by_vector(
        self,
        vector: Sequence[float],
        *,
        limit: int = 6,
        run_id: UUID | None = None,
        external_context: bool = True,
        embedding_model: str,
        embedding_revision: str,
    ) -> SearchResult:
        """Return exact cosine top-k evidence from one ready run snapshot."""
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        query_vector = _vector_literal(vector)
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                if run_id is None:
                    cursor = await conn.execute(
                        """SELECT r.id FROM kb.index_state s
                           JOIN kb.ingestion_runs r ON r.id = s.active_run_id
                           WHERE s.singleton = TRUE AND r.status = 'ready'"""
                    )
                else:
                    cursor = await conn.execute(
                        "SELECT id FROM kb.ingestion_runs WHERE id = %s AND status = 'ready'",
                        (run_id,),
                    )
                run_row = await cursor.fetchone()
                if run_row is None:
                    raise NoReadyIndex("There is no active ready knowledge run")
                selected_run_id = run_row[0]
                cursor = await conn.execute(
                    """SELECT embedding_model, embedding_revision, embedding_dim
                       FROM kb.ingestion_runs WHERE id = %s""",
                    (selected_run_id,),
                )
                identity_row = await cursor.fetchone()
                self._assert_compatible(identity_row, embedding_model, embedding_revision)
                if identity_row[2] != len(vector):
                    raise EmbeddingCompatibilityError(
                        "Embedding dimension differs from the selected run"
                    )

                cursor = await conn.execute(
                    """WITH ranked AS (
                           SELECT c.id AS chunk_id, m.id AS material_id,
                                  m.source_table, m.source_id, m.title, c.text, m.url,
                                  c.material_id AS material_order,
                                  c.chunk_no,
                                  c.embedding <=> %s::vector AS cosine_distance
                           FROM kb.chunks c
                           JOIN kb.materials m
                             ON m.run_id = c.run_id AND m.id = c.material_id
                           WHERE c.run_id = %s
                             AND c.purpose = 'knowledge'
                             AND m.is_active = TRUE
                             AND m.answer_allowed = TRUE
                             AND m.access_type = 'free'
                             AND m.url IS NOT NULL
                             AND (%s::boolean = FALSE OR m.external_allowed = TRUE)
                       ), best_per_material AS (
                           SELECT DISTINCT ON (material_id) *
                           FROM ranked
                           ORDER BY material_id, cosine_distance, chunk_no
                       )
                       SELECT chunk_id, material_id, source_table, source_id,
                              title, text, url, cosine_distance
                       FROM best_per_material
                       ORDER BY cosine_distance, material_order, chunk_no
                       LIMIT %s""",
                    (query_vector, selected_run_id, external_context, limit),
                )
                rows = await cursor.fetchall()
        evidence = tuple(
            Evidence(
                chunk_id=row[0],
                material_id=row[1],
                source=SourceKey(source_table=row[2], source_id=row[3]),
                title=row[4],
                text=row[5],
                url=row[6],
                distance=float(row[7]),
            )
            for row in rows
        )
        return SearchResult(run_id=selected_run_id, evidence=evidence)

    async def recommend_by_vector(
        self,
        vector: Sequence[float],
        *,
        profile: Profile,
        run_id: UUID,
        exclude: tuple[SourceKey, ...],
        limit: int,
        source: SourceKey | None = None,
        embedding_model: str,
        embedding_revision: str,
    ) -> tuple[MaterialCard, ...]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        query_vector = _vector_literal(vector)
        excluded = json.dumps(
            [{"source_table": key.source_table, "source_id": key.source_id} for key in exclude]
        )
        profile_values = [
            value.strip()
            for value in (
                profile.stage,
                profile.industry,
                profile.experience,
                profile.current_goal,
                profile.tax_regime,
            )
            if value and value.strip()
        ]
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                cursor = await conn.execute(
                    """SELECT embedding_model, embedding_revision, embedding_dim
                       FROM kb.ingestion_runs WHERE id = %s AND status = 'ready'""",
                    (run_id,),
                )
                identity_row = await cursor.fetchone()
                if identity_row is None:
                    raise NoReadyIndex(f"Requested run {run_id} is not ready")
                self._assert_compatible(identity_row, embedding_model, embedding_revision)
                if identity_row[2] != len(vector):
                    raise EmbeddingCompatibilityError(
                        "Embedding dimension differs from the selected run"
                    )
                cursor = await conn.execute(
                    """WITH candidates AS (
                           SELECT m.id, m.run_id, m.source_table, m.source_id, m.kind,
                                  m.title, m.summary, m.url, m.access_type, m.price,
                                  m.currency, m.price_note, m.provenance,
                                  c.embedding <=> %s::vector AS distance,
                                  relation.relation_order,
                                  CASE WHEN EXISTS (
                                      SELECT 1
                                      FROM jsonb_array_elements(
                                          COALESCE(m.provenance->'topics', '[]'::jsonb)
                                      ) AS t(topic)
                                      WHERE EXISTS (
                                      SELECT 1 FROM jsonb_array_elements_text(%s::jsonb) AS p(value)
                                          WHERE btrim(p.value) <> ''
                                            AND t.topic->>'title' ILIKE ('%%' || p.value || '%%')
                                      )
                                  ) THEN 0.05 ELSE 0 END AS profile_boost
                           FROM kb.chunks c
                           JOIN kb.materials m
                             ON m.run_id = c.run_id AND m.id = c.material_id
                           LEFT JOIN LATERAL (
                               SELECT min(COALESCE(
                                          (r.provenance->>'order')::integer,
                                          2147483647
                                      )) AS relation_order
                               FROM kb.material_relations r
                               JOIN kb.materials origin
                                 ON origin.run_id = r.run_id
                                AND origin.id = r.from_material_id
                               WHERE r.run_id = c.run_id
                                 AND r.to_material_id = m.id
                                 AND r.relation_type = 'recommended'
                                 AND origin.source_table = %s
                                 AND origin.source_id = %s
                           ) relation ON TRUE
                           WHERE c.run_id = %s AND c.purpose = 'catalog'
                             AND m.is_active = TRUE AND m.recommend_allowed = TRUE
                             AND m.url IS NOT NULL AND m.url ~* '^https://[^[:space:]]+'
                             AND (%s::boolean = FALSE OR m.access_type = 'free')
                             AND NOT EXISTS (
                                 SELECT 1 FROM jsonb_array_elements(%s::jsonb) AS x(item)
                                 WHERE x.item->>'source_table' = m.source_table
                                   AND x.item->>'source_id' = m.source_id
                             )
                       ), best AS (
                           SELECT DISTINCT ON (id) * FROM candidates
                           ORDER BY id, distance
                       )
                       SELECT id, run_id, source_table, source_id, kind, title, summary,
                              url, access_type, price, currency, price_note, provenance,
                              relation_order, distance
                       FROM best
                       WHERE distance <= 0.35 OR relation_order IS NOT NULL
                       ORDER BY relation_order NULLS LAST,
                                distance - profile_boost, distance, source_table, source_id
                       LIMIT %s""",
                    (
                        query_vector,
                        json.dumps(profile_values),
                        source.source_table if source else None,
                        source.source_id if source else None,
                        run_id,
                        profile.free_only,
                        excluded,
                        limit,
                    ),
                )
                rows = await cursor.fetchall()
        return tuple(
            self._material_card(row[:13], profile_values, score=1 - float(row[14])) for row in rows
        )

    async def get_card(
        self,
        source: SourceKey,
        *,
        run_id: UUID | None = None,
    ) -> MaterialCard | None:
        async with self._pool.connection() as conn:
            async with conn.transaction():
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                if run_id is None:
                    cursor = await conn.execute(
                        """SELECT m.id, m.run_id, m.source_table, m.source_id, m.kind,
                                  m.title, m.summary, m.url, m.access_type, m.price,
                                  m.currency, m.price_note, m.provenance
                           FROM kb.index_state s
                           JOIN kb.ingestion_runs r ON r.id = s.active_run_id AND r.status = 'ready'
                           JOIN kb.materials m ON m.run_id = r.id
                           WHERE s.singleton = TRUE AND m.source_table = %s
                             AND m.source_id = %s AND m.is_active = TRUE
                             AND (m.recommend_allowed = TRUE OR m.answer_allowed = TRUE)
                             AND m.url IS NOT NULL
                             AND m.url ~* '^https://[^[:space:]]+'""",
                        (source.source_table, source.source_id),
                    )
                else:
                    cursor = await conn.execute(
                        """SELECT m.id, m.run_id, m.source_table, m.source_id, m.kind,
                                  m.title, m.summary, m.url, m.access_type, m.price,
                                  m.currency, m.price_note, m.provenance
                           FROM kb.ingestion_runs r
                           JOIN kb.materials m ON m.run_id = r.id
                           WHERE r.id = %s AND r.status = 'ready'
                             AND m.source_table = %s AND m.source_id = %s
                             AND m.is_active = TRUE
                             AND (m.recommend_allowed = TRUE OR m.answer_allowed = TRUE)
                             AND m.url IS NOT NULL AND m.url ~* '^https://[^[:space:]]+'""",
                        (run_id, source.source_table, source.source_id),
                    )
                row = await cursor.fetchone()
        return self._material_card(row, []) if row is not None else None

    @staticmethod
    def _assert_compatible(
        stored: Sequence[Any] | None,
        requested_model: str,
        requested_revision: str,
    ) -> None:
        if stored is None:
            raise NoReadyIndex("There is no active ready knowledge run")
        if stored[0] != requested_model or stored[1] != requested_revision:
            raise EmbeddingCompatibilityError(
                "Embedding/index mismatch: selected run uses "
                f"{stored[0]}@{stored[1]}, loaded model is {requested_model}@{requested_revision}"
            )

    @staticmethod
    def _material_card(
        row: Sequence[Any], profile_values: Sequence[str], score=None
    ) -> MaterialCard:
        provenance = row[12] or {}
        features = list(provenance.get("reason_features", []))
        topics = provenance.get("topics", [])
        lowered_profiles = [value.casefold() for value in profile_values]
        for topic in topics:
            title = str(topic.get("title", ""))
            if title and any(
                value in title.casefold() or title.casefold() in value for value in lowered_profiles
            ):
                features.append(title)
        return MaterialCard(
            id=row[0],
            run_id=row[1],
            source=SourceKey(row[2], row[3]),
            kind=row[4],
            title=row[5],
            public_summary=row[6],
            url=row[7],
            access_type=row[8],
            price=row[9],
            currency=row[10],
            price_note=row[11],
            reason_features=tuple(dict.fromkeys(str(feature) for feature in features)),
            score=score,
        )
