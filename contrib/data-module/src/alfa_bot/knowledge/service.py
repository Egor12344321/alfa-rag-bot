"""Knowledge API matching the asynchronous ``Knowledge.retrieve`` contract."""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Any, Protocol, Sequence
from urllib.parse import urlsplit
from uuid import UUID

from .contracts import (
    ChunkInput,
    MaterialCard,
    MaterialInput,
    Profile,
    SearchResult,
    SourceBlock,
    SourceKey,
)
from .repository import (
    IndexedMaterial,
    InvalidIngestion,
    KnowledgeRepository,
)


class QueryEmbedder(Protocol):
    tokenizer: Any
    model_name: str
    resolved_revision: str

    def encode_query(self, text: str) -> list[float]: ...

    def encode_passage_batch(
        self, items: Sequence[tuple[str, ChunkInput]]
    ) -> list[list[float]]: ...


Chunker = Callable[[MaterialInput, Any], Sequence[ChunkInput]]


class KnowledgeService:
    """Vectorizes user queries and delegates access-filtered retrieval."""

    def __init__(
        self,
        repository: KnowledgeRepository,
        embedder: QueryEmbedder,
        *,
        chunker: Chunker | None = None,
    ):
        self._repository = repository
        self._embedder = embedder
        if chunker is None:
            from .chunking import chunk_material

            chunker = chunk_material
        self._chunker = chunker

    async def retrieve(
        self,
        query: str,
        *,
        limit: int = 6,
        run_id: UUID | None = None,
        external_context: bool = True,
    ) -> SearchResult:
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        vector = await asyncio.to_thread(self._embedder.encode_query, query)
        model_name, revision = self._embedding_identity()
        return await self._repository.retrieve_by_vector(
            vector,
            limit=limit,
            run_id=run_id,
            external_context=external_context,
            embedding_model=model_name,
            embedding_revision=revision,
        )

    async def recommend(
        self,
        query: str,
        profile: Profile,
        *,
        run_id: UUID,
        exclude: tuple[SourceKey, ...] = (),
        limit: int = 1,
        source: SourceKey | None = None,
    ) -> tuple[MaterialCard, ...]:
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        vector = await asyncio.to_thread(self._embedder.encode_query, query)
        model_name, revision = self._embedding_identity()
        return await self._repository.recommend_by_vector(
            vector,
            profile=profile,
            run_id=run_id,
            exclude=exclude,
            limit=limit,
            source=source,
            embedding_model=model_name,
            embedding_revision=revision,
        )

    async def get_card(
        self, source: SourceKey, *, run_id: UUID | None = None
    ) -> MaterialCard | None:
        return await self._repository.get_card(source, run_id=run_id)

    async def ingest(
        self,
        materials: Iterable[MaterialInput],
        *,
        source_fingerprint: str | None = None,
        embedding_revision: str | None = None,
        pipeline_version: str = "knowledge-v1",
        mapping_version: str = "mapping-v1",
        policy_version: str = "access-v1",
    ) -> UUID:
        """Chunk, batch-embed, and atomically publish allowed materials.

        Input records are validated before their text is sent to the tokenizer
        or embedding model. The E5 adapter applies ``passage:`` to every chunk.
        """
        source_materials = tuple(materials)
        if not source_materials:
            raise InvalidIngestion("No materials supplied for ingestion")
        seen: set[tuple[str, str]] = set()
        for material in source_materials:
            key = (material.source_table, material.source_id)
            if key in seen:
                raise InvalidIngestion(f"Duplicate source identity: {key}")
            seen.add(key)
            if not material.answer_allowed and not material.recommend_allowed:
                raise InvalidIngestion(
                    f"Refusing to process source without an answer or catalog permission: {key}"
                )
            if material.answer_allowed and material.access_type != "free":
                raise InvalidIngestion(f"Refusing non-free answer evidence: {key}")
            parsed_url = urlsplit(material.url or "")
            if (material.answer_allowed or material.recommend_allowed) and (
                parsed_url.scheme.lower() != "https" or not parsed_url.netloc
            ):
                raise InvalidIngestion(
                    f"Refusing to process source without a verified HTTPS URL: {key}"
                )
            if not material.title.strip():
                raise InvalidIngestion(f"Refusing to process source with an empty title: {key}")
            if material.recommend_allowed and not material.summary.strip():
                raise InvalidIngestion(
                    f"Refusing catalog material with an empty public summary: {key}"
                )

        tokenizer = self._embedder.tokenizer
        chunks_by_material: list[tuple[MaterialInput, tuple[ChunkInput, ...]]] = []
        batch_items: list[tuple[str, ChunkInput]] = []
        for material in source_materials:
            chunks: list[ChunkInput] = []
            if material.answer_allowed:
                chunks.extend(self._chunker(material, tokenizer))
            if material.recommend_allowed:
                # Catalog vectors contain only the approved short summary.
                summary_source = replace(
                    material,
                    blocks=(SourceBlock("public-summary", 0, material.summary),),
                    access_type="free",
                    answer_allowed=True,
                    recommend_allowed=False,
                )
                chunks.extend(
                    replace(chunk, purpose="catalog")
                    for chunk in self._chunker(summary_source, tokenizer)
                )
            chunks = tuple(chunks)
            if not chunks:
                raise InvalidIngestion(
                    f"Permitted source {material.source_table}/{material.source_id} "
                    "produced no text chunks"
                )
            chunks_by_material.append((material, chunks))
            batch_items.extend((material.title, chunk) for chunk in chunks)

        encode_batch = getattr(self._embedder, "encode_passage_batch", None)
        if callable(encode_batch):
            vectors = await asyncio.to_thread(encode_batch, batch_items)
        else:
            # Compatibility for small test doubles or adapters that only
            # implement per-material batch encoding.
            vectors = []
            for material, chunks in chunks_by_material:
                vectors.extend(
                    await asyncio.to_thread(
                        self._embedder.encode_passages, material.title, chunks
                    )
                )
        if len(vectors) != len(batch_items):
            raise InvalidIngestion("Embedding adapter returned a mismatched vector count")
        embedding_model, loaded_revision = self._embedding_identity()
        if embedding_revision is not None and embedding_revision != loaded_revision:
            raise InvalidIngestion(
                f"Requested embedding revision {embedding_revision!r} differs from loaded "
                f"revision {loaded_revision!r}; configure E5Embedder before loading it"
            )

        indexed: list[IndexedMaterial] = []
        offset = 0
        for material, chunks in chunks_by_material:
            end = offset + len(chunks)
            indexed.append(IndexedMaterial(material, chunks, tuple(vectors[offset:end])))
            offset = end

        if source_fingerprint is None:
            fingerprint_payload = [
                {
                    "source_table": material.source_table,
                    "source_id": material.source_id,
                    "kind": material.kind,
                    "title": material.title,
                    "url": material.url,
                    "blocks": [
                        [block.block_id, block.order, block.content]
                        for block in sorted(
                            material.blocks,
                            key=lambda block: (block.order, block.block_id),
                        )
                    ],
                    "access_type": material.access_type,
                    "answer_allowed": material.answer_allowed,
                    "recommend_allowed": material.recommend_allowed,
                    "external_allowed": material.external_allowed,
                    "summary": material.summary,
                    "price": str(material.price) if material.price is not None else None,
                    "currency": material.currency,
                    "topics": [(topic.source_id, topic.title) for topic in material.topics],
                    "recommendations": [
                        (target.source_table, target.source_id)
                        for target in material.recommendations
                    ],
                }
                for material in sorted(
                    source_materials,
                    key=lambda item: (item.source_table, item.source_id),
                )
            ]
            encoded = json.dumps(
                fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            source_fingerprint = hashlib.sha256(encoded).hexdigest()

        return await self._repository.publish_run(
            indexed,
            source_fingerprint=source_fingerprint,
            embedding_model=embedding_model,
            embedding_revision=loaded_revision,
            pipeline_version=pipeline_version,
            mapping_version=mapping_version,
            policy_version=policy_version,
        )

    def _embedding_identity(self) -> tuple[str, str]:
        model_name = getattr(self._embedder, "model_name", None)
        revision = getattr(self._embedder, "resolved_revision", None)
        if not model_name or not revision or revision == "local-cache":
            raise RuntimeError(
                "Embedding adapter must expose the loaded model name and its immutable revision"
            )
        return str(model_name), str(revision)
